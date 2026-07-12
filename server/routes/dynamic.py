from flask import Blueprint, request, jsonify, g as flask_g
from db import get_db
from datetime import datetime, timezone
from auth import login_required
from utils.permissions import can_page
from utils.rbac_guard import require_page_action
from utils.operation_log import log_operation, get_page_info, pick_display_name, get_field_label_map
from utils.mongo_query import translate as mongo_translate, remap_labels, MongoQueryError
from utils.version import get_user_current_branch, MAIN_BRANCH_ID
from utils.branch_lock import check_branch_lock
from utils.sequences import allocate_sequence
import psycopg2.extras
import json

dynamic_bp = Blueprint('dynamic', __name__)

# Reserved paths that should not be handled by the dynamic catch-all
RESERVED = {'menus', 'pageConfigs', 'favicon.ico', 'relations', 'auth', 'users', 'roles', 'operationLogs', 'backups', 'exportScripts', 'apiKeys', 'validationScripts', 'etlTasks', 'relation-graph', 'query', 'comments', 'timeline', 'dashboards', 'notifications', 'triggerRules', 'ai', 'versions', 'project-versions', 'webhook', 'dependencies', 'system-config', 'home-widgets', 'data-files', 'ai-scan-tasks'}


def _get_current_user_branch(collection):
    """Get the current user's branch for a collection.

    Returns the branch_id (str), defaults to 'main' for main branch.
    """
    user = getattr(flask_g, 'current_user', {}) if hasattr(flask_g, 'current_user') else {}
    user_id = user.get('userId')
    if not user_id:
        return MAIN_BRANCH_ID
    return get_user_current_branch(user_id, collection)


def format_ts(dt):
    """Format datetime to ISO 8601 with trailing Z (UTC)."""
    if dt is None:
        return None
    if hasattr(dt, 'astimezone'):
        dt = dt.astimezone(timezone.utc)
    s = dt.strftime('%Y-%m-%dT%H:%M:%S.000Z')
    return s


def row_to_record(row):
    """Reconstruct flat object from (id, collection, data, created_at, updated_at, version, branch_id) row."""
    record = {'id': row[0]}
    if row[2]:  # data JSONB
        record.update(row[2])
    if row[3]:  # created_at
        record['createdAt'] = format_ts(row[3])
    if row[4]:  # updated_at
        record['updatedAt'] = format_ts(row[4])
    record['_version'] = row[5] if len(row) > 5 and row[5] is not None else 1
    if len(row) > 6 and row[6] is not None:
        record['_branchId'] = row[6]
    return record


def get_primary_key_fields(cur, collection):
    """Get primary key field names from page config for a collection."""
    page_id = f'page-{collection}'
    cur.execute(
        'SELECT fields FROM page_configs WHERE id = %s', (page_id,)
    )
    row = cur.fetchone()
    if not row or not row[0]:
        return []
    return [f['fieldName'] for f in row[0] if f.get('isPrimaryKey')]


def check_primary_key_unique(cur, collection, data, pk_fields, exclude_id=None, branch_id=None):
    """Check if primary key combination is unique within the same branch. Returns error message or None."""
    if not pk_fields:
        return None

    pk_values = {}
    for field in pk_fields:
        pk_values[field] = data.get(field)

    # Build JSONB conditions for each primary key field
    conditions = ['collection = %s', 'branch_id = %s']
    params = [collection, branch_id]

    for field, value in pk_values.items():
        if value is None:
            conditions.append(f"(data->>%s IS NULL)")
            params.append(field)
        else:
            conditions.append(f"data->>%s = %s")
            params.append(field)
            params.append(str(value))

    if exclude_id:
        conditions.append('id != %s')
        params.append(exclude_id)

    sql = f"SELECT id FROM dynamic_data WHERE {' AND '.join(conditions)} LIMIT 1"
    cur.execute(sql, params)
    existing = cur.fetchone()

    if existing:
        labels = ', '.join(f'{f}={pk_values[f]}' for f in pk_fields)
        return f'主键重复：{labels}'
    return None


def acquire_pk_lock(cur, collection, pk_values):
    """对 (collection + 主键值拼接) 取事务级 advisory lock，串行化同主键并发写。
    pk_values: {field: value}。空 dict 则不加锁。事务提交/回滚自动释放。"""
    if not pk_values:
        return
    parts = [str(pk_values.get(f, '')) for f in sorted(pk_values)]
    key = collection + '|' + '|'.join(parts)
    cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))", (collection, key))


def get_validation_script(cur, collection):
    """Get validation script code from validation_scripts table via page_configs binding."""
    page_id = f'page-{collection}'
    cur.execute('SELECT validation_script FROM page_configs WHERE id = %s', (page_id,))
    row = cur.fetchone()
    script_id = row[0] if row and row[0] else None
    if not script_id:
        return None
    cur.execute('SELECT script FROM validation_scripts WHERE id = %s', (script_id,))
    script_row = cur.fetchone()
    return script_row[0] if script_row and script_row[0] else None


def get_display_field(fields):
    """获取用于显示的字段名（第一个 text/textarea/autoSequence 字段）"""
    for f in fields:
        if f.get('controlType') in ('text', 'textarea', 'autoSequence', 'compositeText'):
            return f['fieldName']
    return None


def build_keyword_conditions(cur, collection, keyword, fields, branch_id):
    """
    构建关键字搜索条件，支持普通字段、关联字段和记录ID精确匹配。
    返回 (where_fragment, params, matching_ids) 三元组。
    - where_fragment: 用于普通字段的 WHERE 条件片段
    - params: 对应的参数
    - matching_ids: 关联字段匹配的记录 ID 集合（用于 UNION 合并）
    """
    if not keyword or not keyword.strip():
        return None, [], set()

    keyword = keyword.strip()
    keyword_pattern = f'%{keyword}%'
    conditions = []
    params = []
    matching_ids = set()

    # 首先添加 ID 精确匹配条件
    conditions.append("id = %s")
    params.append(keyword)

    # 批量预加载目标集合的 page_configs（消除 N+1）
    target_collections = set()
    for field in fields:
        ct = field.get('controlType', 'text')
        if ct == 'relation':
            tc = (field.get('relationConfig') or {}).get('targetCollection')
        elif ct == 'reference':
            tc = (field.get('referenceConfig') or {}).get('targetCollection')
        elif ct == 'quoteSelect':
            tc = (field.get('quoteConfig') or field.get('relationConfig') or {}).get('targetCollection')
        else:
            tc = None
        if tc:
            target_collections.add(tc)

    target_configs = {}
    if target_collections:
        page_ids = [f'page-{c}' for c in target_collections]
        cur.execute('SELECT id, fields FROM page_configs WHERE id = ANY(%s)', (page_ids,))
        for row in cur.fetchall():
            target_configs[row[0]] = row[1]

    # 可直接在 JSONB 中搜索的字段类型
    direct_searchable = {'text', 'textarea', 'markdown', 'number', 'autoSequence', 'select', 'radio', 'date', 'datetime', 'autoTimestamp', 'compositeText'}

    for field in fields:
        field_name = field.get('fieldName')
        control_type = field.get('controlType', 'text')

        if control_type in direct_searchable:
            # 直接在 JSONB data 字段中搜索
            conditions.append(f"data->>%s ILIKE %s")
            params.extend([field_name, keyword_pattern])

        elif control_type == 'relation':
            # M:N 关联字段：通过 data_relations 表搜索
            rel_config = field.get('relationConfig', {})
            target_collection = rel_config.get('targetCollection')
            if target_collection:
                target_page_id = f'page-{target_collection}'
                target_fields = target_configs.get(target_page_id)
                if target_fields:
                    # 优先使用字段配置中指定的 displayField，而非自动检测
                    target_display = rel_config.get('displayField') or get_display_field(target_fields)
                    if target_display:
                        sql = '''
                            SELECT DISTINCT dr.record_id
                            FROM data_relations dr
                            JOIN dynamic_data dd ON dd.id = dr.related_id AND dd.collection = dr.related_collection
                            WHERE dr.collection = %s AND dr.field_name = %s AND dr.branch_id = %s
                            AND dd.data->>%s ILIKE %s
                        '''
                        cur.execute(sql, (collection, field_name, branch_id, target_display, keyword_pattern))
                        matching_ids.update(row[0] for row in cur.fetchall())

        elif control_type == 'reference':
            # 1:N 引用字段：字段值是引用记录的 ID
            ref_config = field.get('referenceConfig', {})
            target_collection = ref_config.get('targetCollection')
            if target_collection:
                target_page_id = f'page-{target_collection}'
                target_fields = target_configs.get(target_page_id)
                if target_fields:
                    # 优先使用字段配置中指定的 displayField，而非自动检测
                    target_display = ref_config.get('displayField') or get_display_field(target_fields)
                    if target_display:
                        sql = '''
                            SELECT dd.id FROM dynamic_data dd
                            JOIN dynamic_data ref ON ref.id = dd.data->>%s
                            WHERE dd.collection = %s AND dd.branch_id = %s
                            AND ref.collection = %s AND ref.branch_id = %s
                            AND ref.data->>%s ILIKE %s
                        '''
                        cur.execute(sql, (field_name, collection, branch_id, target_collection, branch_id, target_display, keyword_pattern))
                        matching_ids.update(row[0] for row in cur.fetchall())

        elif control_type == 'quoteSelect':
            # 引用选择字段：值是 JSONB 数组 [id1, id2, ...]，不使用 data_relations
            quote_config = field.get('quoteConfig', {}) or field.get('relationConfig', {})
            target_collection = quote_config.get('targetCollection')
            if target_collection:
                target_page_id = f'page-{target_collection}'
                target_fields = target_configs.get(target_page_id)
                if target_fields:
                    # 优先使用字段配置中指定的 displayField，而非自动检测
                    target_display = quote_config.get('displayField') or get_display_field(target_fields)
                    if target_display:
                        # 先找目标集合中匹配关键字的记录 ID，再找当前集合中引用了它们的记录
                        sql = '''
                            SELECT dd.id FROM dynamic_data dd
                            WHERE dd.collection = %s AND dd.branch_id = %s
                            AND EXISTS (
                                SELECT 1 FROM dynamic_data ref
                                WHERE ref.collection = %s AND ref.branch_id = %s
                                AND ref.data->>%s ILIKE %s
                                AND ref.id = ANY(
                                    SELECT jsonb_array_elements_text(dd.data->%s)
                                )
                            )
                        '''
                        cur.execute(sql, (collection, branch_id, target_collection, branch_id,
                                          target_display, keyword_pattern, field_name))
                        matching_ids.update(row[0] for row in cur.fetchall())

    return conditions, params, matching_ids


def apply_pending_relations(cur, collection, record_id, pending_relations, branch_id=None):
    """Apply relation operations queued by validation script (bidirectional sync)."""
    for rel in pending_relations:
        _apply_relation_update(
            cur, collection, record_id,
            rel['fieldName'], rel['targetCollection'], rel['targetField'],
            set(rel['ids']), branch_id=branch_id,
        )


def _apply_relation_update(cur, collection, record_id, field_name, target_collection, target_field, new_ids, branch_id=None):
    """Bidirectional M:N relation sync (reusable for both validation scripts and client-side relations)."""
    # Get old related IDs (within the same branch)
    cur.execute(
        'SELECT related_id FROM data_relations '
        'WHERE collection = %s AND record_id = %s AND field_name = %s AND branch_id = %s',
        (collection, record_id, field_name, branch_id),
    )
    old_ids = set(row[0] for row in cur.fetchall())

    # Delete all existing forward relations for this field (within the same branch)
    cur.execute(
        'DELETE FROM data_relations '
        'WHERE collection = %s AND record_id = %s AND field_name = %s AND branch_id = %s',
        (collection, record_id, field_name, branch_id),
    )

    # Insert new forward relations (with branch_id)
    for rid in new_ids:
        cur.execute(
            'INSERT INTO data_relations (collection, record_id, field_name, related_collection, related_id, branch_id) '
            'VALUES (%s, %s, %s, %s, %s, %s)',
            (collection, record_id, field_name, target_collection, rid, branch_id),
        )

    # Sync reverse: remove reverse entries for removed IDs
    for rid in old_ids - new_ids:
        cur.execute(
            'DELETE FROM data_relations '
            'WHERE collection = %s AND record_id = %s AND field_name = %s AND related_id = %s AND branch_id = %s',
            (target_collection, rid, target_field, record_id, branch_id),
        )

    # Sync reverse: add reverse entries for added IDs
    for rid in new_ids - old_ids:
        cur.execute(
            'INSERT INTO data_relations (collection, record_id, field_name, related_collection, related_id, branch_id) '
            'VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING',
            (target_collection, rid, target_field, collection, record_id, branch_id),
        )


@dynamic_bp.route('/<collection>', methods=['GET'])
@login_required
def list_items(collection):
    if collection in RESERVED:
        return jsonify({"error": "Not found"}), 404
    denied = require_page_action(collection, 'read')
    if denied:
        return denied

    query_str = request.args.get('q', '')
    keyword = request.args.get('keyword', '')
    locate_id = request.args.get('locateId', '')
    # 按真实 id 列过滤（逗号分隔）。用于「只取被引用到的记录」的标签解析，避免全量加载。
    # 注意：id 是独立列、不在 data JSONB 里，所以不能用 q={"id":...}，必须走这里。
    ids_param = request.args.get('ids')  # None 表示未传；'' 表示传了但为空 → 不匹配任何记录
    # 分页参数
    page = request.args.get('page', 1, type=int)
    page_size = request.args.get('pageSize', 50, type=int)
    # 全量加载参数（用于 Excel 视图等需要加载所有数据的场景）
    load_all = request.args.get('all', 'false').lower() == 'true'
    # 限制 page_size 最大值（全量加载时不限制）
    if not load_all:
        page_size = min(page_size, 1000)
    offset = (page - 1) * page_size if not load_all else 0

    # 排序参数（列名与方向均走白名单，防 SQL 注入）
    _SORT_COLUMNS = {'createdAt': 'created_at', 'updatedAt': 'updated_at', 'id': 'id'}
    sort_col = _SORT_COLUMNS.get(request.args.get('sort', 'createdAt'), 'created_at')
    direction = 'DESC' if request.args.get('order', 'asc').lower() == 'desc' else 'ASC'
    # locateId 的页码定位依赖默认升序排列，二者不同时生效（有 locateId 时强制升序）
    if locate_id:
        order_by_clause = 'created_at, id'
    elif sort_col == 'id':
        order_by_clause = f'id {direction}'
    else:
        order_by_clause = f'{sort_col} {direction}, id {direction}'

    branch_id = _get_current_user_branch(collection)

    with get_db() as conn:
        cur = conn.cursor()

        # 获取页面配置（用于关键字搜索和标签重映射）
        page_id = f'page-{collection}'
        cur.execute('SELECT fields FROM page_configs WHERE id = %s', (page_id,))
        pc_row = cur.fetchone()
        fields = pc_row[0] if pc_row and pc_row[0] else []

        # 构建基础查询条件
        base_conditions = ['collection = %s', 'branch_id = %s']
        base_params = [collection, branch_id]

        # 按 id 列过滤（真实列，非 JSONB）。传了 ids 即限定到这些 id；空列表 → 无匹配。
        if ids_param is not None:
            id_list = [i for i in ids_param.split(',') if i]
            base_conditions.append('id = ANY(%s)')
            base_params.append(id_list)

        # 处理 MongoDB 查询
        query_conditions = []
        query_params = []
        if query_str:
            try:
                query = json.loads(query_str)
                if fields:
                    query = remap_labels(query, fields)
                where_fragment, q_params = mongo_translate(query)
                query_conditions.append(f'({where_fragment})')
                query_params = q_params
            except json.JSONDecodeError as e:
                return jsonify({"error": f"查询语法错误: JSON 解析失败 - {e}"}), 400
            except MongoQueryError as e:
                return jsonify({"error": f"查询语法错误: {e}"}), 400

        # 处理关键字搜索（支持关联字段）
        keyword_conditions = []
        keyword_params = []
        keyword_matching_ids = set()
        if keyword and keyword.strip():
            keyword_conditions, keyword_params, keyword_matching_ids = build_keyword_conditions(
                cur, collection, keyword, fields, branch_id
            )

        # 合并所有条件
        all_conditions = base_conditions + query_conditions
        all_params = base_params + query_params

        # 构建最终的 WHERE 子句
        if keyword_conditions or keyword_matching_ids:
            # 关键字搜索：普通字段 OR 关联字段匹配的 ID
            keyword_parts = []
            if keyword_conditions:
                keyword_parts.append('(' + ' OR '.join(keyword_conditions) + ')')
            if keyword_matching_ids:
                # 将匹配的 ID 转为列表
                id_list = list(keyword_matching_ids)
                placeholders = ','.join(['%s'] * len(id_list))
                keyword_parts.append(f'id IN ({placeholders})')
                keyword_params.extend(id_list)

            if keyword_parts:
                all_conditions.append('(' + ' OR '.join(keyword_parts) + ')')
            all_params.extend(keyword_params)

        where_clause = ' AND '.join(all_conditions)

        # 获取总数
        count_sql = f'SELECT COUNT(*) FROM dynamic_data WHERE {where_clause}'
        cur.execute(count_sql, all_params)
        total = cur.fetchone()[0]

        # locateId: 定位指定记录所在的页码
        located_page = None
        located_index = None
        locate_filter_miss = False

        if locate_id and not load_all:
            # 检查记录是否存在
            cur.execute(
                'SELECT id, created_at FROM dynamic_data '
                'WHERE id = %s AND collection = %s AND branch_id = %s',
                (locate_id, collection, branch_id)
            )
            locate_row = cur.fetchone()

            if locate_row:
                loc_created_at = locate_row[1]
                # 计算在当前筛选条件下目标记录前面有多少条记录
                position_sql = (
                    f'SELECT COUNT(*) FROM dynamic_data WHERE {where_clause} '
                    'AND (created_at < %s OR (created_at = %s AND id < %s))'
                )
                cur.execute(position_sql, all_params + [loc_created_at, loc_created_at, locate_id])
                position = cur.fetchone()[0]

                # 检查记录是否在当前筛选结果中
                check_sql = f'SELECT 1 FROM dynamic_data WHERE {where_clause} AND id = %s LIMIT 1'
                cur.execute(check_sql, all_params + [locate_id])
                in_filter = cur.fetchone() is not None

                if in_filter:
                    located_page = position // page_size + 1
                    located_index = position % page_size
                    # 覆盖分页参数，返回目标记录所在页
                    page = located_page
                    offset = (page - 1) * page_size
                else:
                    locate_filter_miss = True

        # 获取数据（全量加载时不分页）
        if load_all:
            data_sql = (
                'SELECT id, collection, data, created_at, updated_at, version, branch_id '
                f'FROM dynamic_data WHERE {where_clause} '
                f'ORDER BY {order_by_clause}'
            )
            cur.execute(data_sql, all_params)
        else:
            data_sql = (
                'SELECT id, collection, data, created_at, updated_at, version, branch_id '
                f'FROM dynamic_data WHERE {where_clause} '
                f'ORDER BY {order_by_clause} LIMIT %s OFFSET %s'
            )
            cur.execute(data_sql, all_params + [page_size, offset])
        rows = cur.fetchall()

    result = {
        'data': [row_to_record(r) for r in rows],
        'total': total,
        'page': page if not load_all else 1,
        'pageSize': page_size if not load_all else total
    }
    if locate_id:
        result['locatedPage'] = located_page
        result['locatedIndex'] = located_index
        result['locateFilterMiss'] = locate_filter_miss
    return jsonify(result)


@dynamic_bp.route('/<collection>/<item_id>', methods=['GET'])
@login_required
def get_item(collection, item_id):
    if collection in RESERVED:
        return jsonify({"error": "Not found"}), 404
    denied = require_page_action(collection, 'read')
    if denied:
        return denied
    branch_id = _get_current_user_branch(collection)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT id, collection, data, created_at, updated_at, version, branch_id '
            'FROM dynamic_data WHERE collection = %s AND id = %s AND branch_id = %s',
            (collection, item_id, branch_id),
        )
        row = cur.fetchone()
    if not row:
        return jsonify({"error": "Not found"}), 404
    return jsonify(row_to_record(row))


@dynamic_bp.route('/<collection>', methods=['POST'])
@login_required
def create_item(collection):
    if collection in RESERVED:
        return jsonify({"error": "Not found"}), 404
    denied = require_page_action(collection, 'create')
    if denied:
        return denied
    body = request.get_json(force=True)
    rid = body.get('id')
    created_at = body.get('createdAt')
    client_relations = body.get('_relations')
    data = {k: v for k, v in body.items() if k not in ('id', 'createdAt', '_relations', '_workflowComment')}
    branch_id = _get_current_user_branch(collection)

    # 检查分支锁定（包括 main 分支）
    lock_info = check_branch_lock(collection)
    if lock_info:
        return jsonify({"error": f"当前分支已被 {lock_info[1]} 锁定，无法进行修改操作"}), 403

    # Before webhook trigger
    try:
        from utils.webhook_engine import fire_webhooks
        user = getattr(flask_g, 'current_user', {}) if hasattr(flask_g, 'current_user') else {}
        before_result = fire_webhooks(
            'create', collection, rid, None, data,
            user.get('username', ''), branch_id=branch_id, timing='before'
        )
        if before_result['beforeBlocked']:
            return jsonify({
                'error': 'Before webhook blocked the operation',
                'webhookErrors': before_result['beforeErrors']
            }), 400
    except Exception as e:
        import logging
        logging.error(f'Before webhook trigger failed for create operation: {e}')

    with get_db() as conn:
        cur = conn.cursor()
        # Fetch schema once (fields drive autoSequence + PK detection)
        page_name, fields = get_page_info(cur, collection)
        pk_fields = [f['fieldName'] for f in (fields or []) if f.get('isPrimaryKey')]
        # 后端原子分配 autoSequence（忽略客户端传入值）
        for f in (fields or []):
            if f.get('controlType') == 'autoSequence':
                cfg = f.get('sequenceConfig') or {}
                prefix = cfg.get('prefix', '')
                pad = len(str(cfg.get('max', 999)))
                data[f['fieldName']] = allocate_sequence(
                    cur, collection, branch_id, f['fieldName'], prefix, pad, count=1)[0]
            elif f.get('controlType') == 'statusBadge' and data.get(f['fieldName']):
                # 记录创建时状态字段已有初始值（如 defaultValue）：盖变化时间戳作为超时判定基准
                data[f'_statusBadge_{f["fieldName"]}_changedAt'] = datetime.now(timezone.utc).isoformat()
        # 手填主键 advisory lock（排除 autoSequence 主键，其值由原子分配天然唯一）
        autoseq_names = {f['fieldName'] for f in (fields or []) if f.get('controlType') == 'autoSequence'}
        manual_pk = {f: data.get(f) for f in (pk_fields or []) if f not in autoseq_names}
        # 锁顺序：先 allocate_sequence 的 FOR UPDATE（计数行），再 advisory lock；
        # 全局一致顺序，避免与并发创建死锁。勿调换。
        acquire_pk_lock(cur, collection, manual_pk)
        # Check primary key uniqueness (within the same branch)
        if pk_fields:
            error = check_primary_key_unique(cur, collection, data, pk_fields, branch_id=branch_id)
            if error:
                return jsonify({"error": error}), 409
        # Run validation script if configured
        validation_script = get_validation_script(cur, collection)
        pending_relations = []
        if validation_script:
            from utils.script_runner import run_validation_script
            try:
                errors, warnings, pending_relations = run_validation_script(
                    validation_script, data, 'create', None, fields, collection, conn
                )
            except Exception as e:
                return jsonify({"error": f"校验脚本执行错误：{str(e)}"}), 400
            if errors:
                return jsonify({
                    "error": "校验失败",
                    "validationErrors": errors,
                    "validationWarnings": warnings,
                }), 400
        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, created_at, branch_id) VALUES (%s,%s,%s,%s,%s)',
            (rid, collection, psycopg2.extras.Json(data), created_at, branch_id),
        )
        # Apply relations queued by validation script
        if pending_relations:
            apply_pending_relations(cur, collection, rid, pending_relations, branch_id=branch_id)
        # Apply client-side relation changes in the SAME transaction (atomic with data create)
        if client_relations:
            for rel in client_relations:
                _apply_relation_update(
                    cur, collection, rid,
                    rel['fieldName'], rel['targetCollection'], rel['targetField'],
                    set(rel.get('ids', [])), branch_id=branch_id,
                )
    record_name = pick_display_name(data, fields) or rid
    log_operation('create', 'dynamic_data', rid, record_name,
                  f'新增{page_name}「{record_name}」', branch_id=branch_id)
    # Fire cross-collection triggers
    try:
        from utils.trigger_engine import fire_triggers
        with get_db() as tconn:
            tcur = tconn.cursor()
            user = getattr(flask_g, 'current_user', {}) if hasattr(flask_g, 'current_user') else {}
            fire_triggers('create', collection, rid, None, data,
                          user.get('username', ''), tcur, user.get('id'))
    except Exception:
        pass
    # Fire webhook triggers (after)
    try:
        from utils.webhook_engine import fire_webhooks
        user = getattr(flask_g, 'current_user', {}) if hasattr(flask_g, 'current_user') else {}
        fire_webhooks('create', collection, rid, None, data,
                       user.get('username', ''), branch_id=branch_id, timing='after')
    except Exception:
        pass
    body['_version'] = 1
    body.pop('_relations', None)
    return jsonify(body), 201


@dynamic_bp.route('/<collection>/<item_id>', methods=['PUT'])
@login_required
def update_item(collection, item_id):
    if collection in RESERVED:
        return jsonify({"error": "Not found"}), 404
    denied = require_page_action(collection, 'update')
    if denied:
        return denied
    body = request.get_json(force=True)
    created_at = body.get('createdAt')
    client_version = body.get('_version')
    client_relations = body.get('_relations')  # [{fieldName, targetCollection, targetField, ids}]
    data = {k: v for k, v in body.items() if k not in ('id', 'createdAt', '_version', 'updatedAt', '_relations', '_workflowComment')}
    branch_id = _get_current_user_branch(collection)

    # 检查分支锁定（包括 main 分支）
    lock_info = check_branch_lock(collection)
    if lock_info:
        return jsonify({"error": f"当前分支已被 {lock_info[1]} 锁定，无法进行修改操作"}), 403

    # Before webhook trigger
    try:
        from utils.webhook_engine import fire_webhooks
        user = getattr(flask_g, 'current_user', {}) if hasattr(flask_g, 'current_user') else {}
        # Fetch old data for before webhook payload
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                'SELECT data FROM dynamic_data WHERE collection = %s AND id = %s AND branch_id = %s',
                (collection, item_id, branch_id)
            )
            old_row = cur.fetchone()
            old_data = old_row[0] if old_row and old_row[0] else {}

        before_result = fire_webhooks(
            'update', collection, item_id, old_data, data,
            user.get('username', ''), branch_id=branch_id, timing='before'
        )
        if before_result['beforeBlocked']:
            return jsonify({
                'error': 'Before webhook blocked the operation',
                'webhookErrors': before_result['beforeErrors']
            }), 400
    except Exception as e:
        import logging
        logging.error(f'Before webhook trigger failed for update operation: {e}')

    with get_db() as conn:
        cur = conn.cursor()
        # Fetch old data FIRST for primary key check and optimistic locking
        cur.execute(
            'SELECT data, version FROM dynamic_data WHERE collection = %s AND id = %s AND branch_id = %s',
            (collection, item_id, branch_id)
        )
        old_row = cur.fetchone()
        if not old_row:
            return jsonify({"error": "记录不存在"}), 404
        old_data = old_row[0] if old_row[0] else {}
        db_version = old_row[1] if old_row[1] is not None else 1

        # Merge old_data with new data for primary key uniqueness check
        merged_data = {**old_data, **data}

        # Fetch schema once (fields needed for autoSequence detection + PK lock)
        page_name, fields = get_page_info(cur, collection)
        pk_fields = [f['fieldName'] for f in (fields or []) if f.get('isPrimaryKey')]
        # 手填主键 advisory lock（仅当请求实际修改了手填主键字段时加锁）
        autoseq_names = {f['fieldName'] for f in (fields or []) if f.get('controlType') == 'autoSequence'}
        manual_pk = {f: merged_data.get(f) for f in (pk_fields or [])
                     if f not in autoseq_names and f in data}
        acquire_pk_lock(cur, collection, manual_pk)
        # Check primary key uniqueness (exclude current record, within same branch)
        if pk_fields:
            error = check_primary_key_unique(cur, collection, merged_data, pk_fields, exclude_id=item_id, branch_id=branch_id)
            if error:
                return jsonify({"error": error}), 409
        # Run validation script if configured
        validation_script = get_validation_script(cur, collection)
        pending_relations = []
        if validation_script:
            from utils.script_runner import run_validation_script
            try:
                errors, warnings, pending_relations = run_validation_script(
                    validation_script, data, 'update', old_data, fields, collection, conn
                )
            except Exception as e:
                return jsonify({"error": f"校验脚本执行错误：{str(e)}"}), 400
            if errors:
                return jsonify({
                    "error": "校验失败",
                    "validationErrors": errors,
                    "validationWarnings": warnings,
                }), 400
        # Optimistic locking: check version if client provides it
        if client_version is not None and int(client_version) != db_version:
            return jsonify({
                "error": "数据已被其他用户修改，请刷新后重试",
                "code": "VERSION_CONFLICT",
                "_version": db_version,
            }), 409
        new_version = db_version + 1
        # Workflow validation: check status field transitions
        from utils.workflow import validate_transition, execute_actions
        user_role = getattr(flask_g, 'current_user', {}).get('role', 'guest') if hasattr(flask_g, 'current_user') else 'guest'
        for field_cfg in (fields or []):
            wf = field_cfg.get('workflowConfig')
            if wf and wf.get('enabled') and field_cfg['fieldName'] in data:
                old_val = old_data.get(field_cfg['fieldName'])
                new_val = merged_data[field_cfg['fieldName']]
                if old_val != new_val and old_val is not None:
                    allowed, error, actions = validate_transition(
                        fields, field_cfg['fieldName'], old_val, new_val, merged_data, user_role
                    )
                    if not allowed:
                        return jsonify({"error": error}), 400
                    execute_actions(actions, merged_data, collection, item_id, cur)
                    from utils.workflow_engine import on_transition, WorkflowError
                    try:
                        on_transition(cur, collection, item_id, field_cfg['fieldName'],
                                      old_val, new_val, old_data, merged_data,
                                      getattr(flask_g, 'current_user', {}).get('username', ''),
                                      user_role, comment=body.get('_workflowComment'),
                                      branch_id=branch_id)
                    except WorkflowError as we:
                        # 角色不符等可预期的编排拒绝：回滚整笔事务（状态字段也不落库），返回 403
                        conn.rollback()
                        return jsonify({"error": str(we)}), 403
                    except Exception:
                        # 编排意外失败（如 spawn 出错）：回滚以避免「状态已改、实例未推进」的分裂状态
                        conn.rollback()
                        import logging; logging.exception('workflow on_transition failed')
                        return jsonify({"error": "工作流推进失败，请重试"}), 500
        # statusBadge 字段：值真正变化时记录变化时间戳，供超时兜底任务判定
        for field_cfg in (fields or []):
            if field_cfg.get('controlType') != 'statusBadge':
                continue
            fname = field_cfg['fieldName']
            if fname not in data:
                continue
            if old_data.get(fname) != merged_data.get(fname):
                merged_data[f'_statusBadge_{fname}_changedAt'] = datetime.now(timezone.utc).isoformat()
        if created_at:
            cur.execute(
                'UPDATE dynamic_data SET data = %s, created_at = %s, updated_at = NOW(), version = %s '
                'WHERE collection = %s AND id = %s AND version = %s AND branch_id = %s',
                (psycopg2.extras.Json(merged_data), created_at, new_version, collection, item_id, db_version, branch_id),
            )
        else:
            cur.execute(
                'UPDATE dynamic_data SET data = %s, updated_at = NOW(), version = %s '
                'WHERE collection = %s AND id = %s AND version = %s AND branch_id = %s',
                (psycopg2.extras.Json(merged_data), new_version, collection, item_id, db_version, branch_id),
            )
        if cur.rowcount == 0:
            # Another request changed the version between our SELECT and UPDATE
            return jsonify({
                "error": "数据已被其他用户修改，请刷新后重试",
                "code": "VERSION_CONFLICT",
            }), 409
        # Apply relations queued by validation script
        if pending_relations:
            apply_pending_relations(cur, collection, item_id, pending_relations, branch_id=branch_id)
        # Apply client-side relation changes in the SAME transaction (atomic with data update)
        if client_relations:
            for rel in client_relations:
                _apply_relation_update(
                    cur, collection, item_id,
                    rel['fieldName'], rel['targetCollection'], rel['targetField'],
                    set(rel.get('ids', [])), branch_id=branch_id,
                )
    body['id'] = item_id
    body['_version'] = new_version
    body.pop('_relations', None)
    label_map = get_field_label_map(fields)
    record_name = pick_display_name(data, fields) or pick_display_name(old_data, fields) or item_id
    changed_labels = []
    field_changes = []
    for key, new_val in data.items():
        if key in label_map and old_data.get(key) != new_val:
            changed_labels.append(label_map[key])
            field_changes.append({
                'field': key,
                'label': label_map[key],
                'from': old_data.get(key),
                'to': new_val,
            })
    if changed_labels:
        desc = f'修改{page_name}「{record_name}」的 {", ".join(changed_labels)}'
    else:
        desc = f'修改{page_name}「{record_name}」'
    log_operation('update', 'dynamic_data', item_id, record_name, desc,
                  field_changes=field_changes if field_changes else None, branch_id=branch_id)
    # Notify on status field changes
    try:
        from utils.notifier import notify_status_change
        user = getattr(flask_g, 'current_user', {}) if hasattr(flask_g, 'current_user') else {}
        operator = user.get('username', '')
        for fc in field_changes:
            for field_cfg in (fields or []):
                if field_cfg['fieldName'] == fc['field'] and field_cfg.get('controlType') == 'select':
                    wf = field_cfg.get('workflowConfig')
                    if wf and wf.get('enabled'):
                        notify_status_change(
                            collection, item_id,
                            fc['label'], fc['from'], fc['to'], operator
                        )
                    break
    except Exception:
        pass
    # Fire cross-collection triggers
    try:
        from utils.trigger_engine import fire_triggers
        with get_db() as tconn:
            tcur = tconn.cursor()
            user = getattr(flask_g, 'current_user', {}) if hasattr(flask_g, 'current_user') else {}
            fire_triggers('update', collection, item_id, old_data, data,
                          user.get('username', ''), tcur, user.get('id'))
    except Exception:
        pass
    # Fire webhook triggers (after)
    try:
        from utils.webhook_engine import fire_webhooks
        user = getattr(flask_g, 'current_user', {}) if hasattr(flask_g, 'current_user') else {}
        fire_webhooks('update', collection, item_id, old_data, data,
                       user.get('username', ''), branch_id=branch_id, timing='after')
    except Exception:
        pass
    return jsonify(body)


def check_reference_dependencies(cur, collection, record_id, branch_id=None):
    """Check if any records in other collections reference this record via 'reference' fields.

    Returns error message string if dependencies exist, None otherwise.
    """
    cur.execute('SELECT id, name, fields FROM page_configs')
    rows = cur.fetchall()

    # 先收集所有需要检查的 (child_collection, field_name, page_name)
    checks = []
    for page_id, page_name, fields in rows:
        if not fields:
            continue
        child_collection = page_id.replace('page-', '')
        for field in fields:
            if field.get('controlType') != 'reference':
                continue
            ref_config = field.get('referenceConfig', {})
            if ref_config.get('targetCollection') != collection:
                continue
            checks.append((child_collection, field['fieldName'], page_name))

    if not checks:
        return None

    # 合并为一条 UNION ALL SQL，避免 N 次查询
    parts = []
    params = []
    for child_col, fn, pn in checks:
        parts.append(
            "SELECT %s AS page_name, count(*) AS cnt "
            "FROM dynamic_data WHERE collection = %s AND data->>%s = %s AND branch_id = %s"
        )
        params.extend([pn, child_col, fn, record_id, branch_id])

    sql = ' UNION ALL '.join(parts)
    cur.execute(sql, params)
    for row in cur.fetchall():
        if row[1] > 0:
            return f'无法删除：被「{row[0]}」的 {row[1]} 条记录引用'
    return None


@dynamic_bp.route('/<collection>/<item_id>', methods=['DELETE'])
@login_required
def delete_item(collection, item_id):
    if collection in RESERVED:
        return jsonify({"error": "Not found"}), 404
    denied = require_page_action(collection, 'delete')
    if denied:
        return denied
    branch_id = _get_current_user_branch(collection)

    # 检查分支锁定（包括 main 分支）
    lock_info = check_branch_lock(collection)
    if lock_info:
        return jsonify({"error": f"当前分支已被 {lock_info[1]} 锁定，无法进行修改操作"}), 403

    with get_db() as conn:
        cur = conn.cursor()
        # Check reference dependencies before deletion (within same branch)
        ref_error = check_reference_dependencies(cur, collection, item_id, branch_id=branch_id)
        if ref_error:
            return jsonify({"error": ref_error}), 409
        # Fetch record name for the log before deleting
        cur.execute(
            'SELECT data FROM dynamic_data WHERE collection = %s AND id = %s AND branch_id = %s',
            (collection, item_id, branch_id)
        )
        data_row = cur.fetchone()
        page_name, fields = get_page_info(cur, collection)
        if data_row and data_row[0]:
            record_name = pick_display_name(data_row[0], fields) or item_id
        else:
            record_name = item_id
        cur.execute(
            'DELETE FROM dynamic_data WHERE collection = %s AND id = %s AND branch_id = %s',
            (collection, item_id, branch_id)
        )
        # Clean up all relations involving this record (within same branch)
        cur.execute(
            'DELETE FROM data_relations WHERE ((collection = %s AND record_id = %s) OR (related_collection = %s AND related_id = %s)) AND branch_id = %s',
            (collection, item_id, collection, item_id, branch_id),
        )
        # Fire webhook triggers before commit
        try:
            from utils.webhook_engine import fire_webhooks
            user = getattr(flask_g, 'current_user', {}) if hasattr(flask_g, 'current_user') else {}
            fire_webhooks('delete', collection, item_id, data_row[0] if data_row else None, None,
                          user.get('username', ''), cur=cur, branch_id=branch_id)
        except Exception:
            pass
    log_operation('delete', 'dynamic_data', item_id, record_name,
                  f'删除{page_name}「{record_name}」', branch_id=branch_id)
    return jsonify({})


@dynamic_bp.route('/<collection>/batch-create', methods=['POST'])
@login_required
def batch_create_items(collection):
    """Batch create multiple records in a single transaction."""
    if collection in RESERVED:
        return jsonify({"error": "Not found"}), 404
    denied = require_page_action(collection, 'create')
    if denied:
        return denied

    body = request.get_json(force=True)
    records = body.get('records', [])
    options = body.get('options', {})

    if not records:
        return jsonify({"error": "records is required"}), 400

    skip_validation = options.get('skipValidation', False)
    continue_on_error = options.get('continueOnError', False)
    branch_id = _get_current_user_branch(collection)

    # 检查分支锁定（包括 main 分支）
    lock_info = check_branch_lock(collection)
    if lock_info:
        return jsonify({"error": f"当前分支已被 {lock_info[1]} 锁定，无法进行修改操作"}), 403

    with get_db() as conn:
        cur = conn.cursor()

        # 1. Batch fetch configuration (shared by all records)
        page_name, fields = get_page_info(cur, collection)
        pk_fields = get_primary_key_fields(cur, collection)
        validation_script = None if skip_validation else get_validation_script(cur, collection)

        # 2. Extract all IDs for uniqueness check
        all_ids = [r.get('id') for r in records if r.get('id')]

        # 3. Check for duplicate IDs within the batch
        id_set = set()
        duplicate_ids = []
        for rid in all_ids:
            if rid in id_set:
                duplicate_ids.append(rid)
            id_set.add(rid)

        if duplicate_ids and not continue_on_error:
            return jsonify({
                "error": "批量导入包含重复 ID",
                "failed": len(records),
                "errors": [{"index": i, "error": "ID 重复", "record": records[i]}
                           for i, r in enumerate(records) if r.get('id') in duplicate_ids]
            }), 409

        # 4. Check existing IDs in database (batch query, within same branch)
        if all_ids:
            cur.execute(
                'SELECT id FROM dynamic_data WHERE collection = %s AND id = ANY(%s) AND branch_id = %s',
                (collection, all_ids, branch_id)
            )
            existing_ids = set(row[0] for row in cur.fetchall())
        else:
            existing_ids = set()

        # 5. Validate and prepare records
        prepared_records = []
        errors = []
        sequence_values = {}

        for idx, record in enumerate(records):
            rid = record.get('id')
            data = record.get('data', {})
            relations = record.get('relations', {})

            # Skip records with duplicate IDs in batch
            if rid in duplicate_ids:
                errors.append({"index": idx, "error": "ID 重复", "record": record})
                continue

            # Records with existing IDs are upserted (UPDATE the data row);
            # previous behaviour was to skip them, but users expected re-import
            # to refresh data. Composite-PK uniqueness still applies to NEW rows
            # only — for an UPDATE on the same id we don't re-check the PK because
            # the row we'd "collide" with is the row we're about to overwrite.
            is_update = bool(rid and rid in existing_ids)

            # Check primary key uniqueness for composite keys (within same branch).
            # Pass exclude_id so the row about to be updated does not flag itself.
            if pk_fields:
                error = check_primary_key_unique(
                    cur, collection, data, pk_fields,
                    branch_id=branch_id,
                    exclude_id=rid if is_update else None,
                )
                if error:
                    errors.append({"index": idx, "error": error, "record": record})
                    continue

            # Run validation script if configured
            if validation_script:
                from utils.script_runner import run_validation_script
                try:
                    val_errors, warnings, pending_relations = run_validation_script(
                        validation_script, data, 'create', None, fields, collection, conn
                    )
                    if val_errors:
                        errors.append({
                            "index": idx,
                            "error": "校验失败",
                            "validationErrors": val_errors,
                            "record": record
                        })
                        continue
                    # Store pending relations for later
                    record['_pending_relations'] = pending_relations
                except Exception as e:
                    errors.append({
                        "index": idx,
                        "error": f"校验脚本执行错误：{str(e)}",
                        "record": record
                    })
                    continue

            prepared_records.append({
                "id": rid,
                "data": data,
                "relations": relations,
                "index": idx,
                "is_update": is_update,
            })

        # 6. Handle errors based on continue_on_error flag
        if errors and not continue_on_error:
            return jsonify({
                "error": "批量创建失败",
                "failed": len(errors),
                "errors": errors
            }), 400

        # 7. Batch upsert records using execute_values (with branch_id).
        # ON CONFLICT (id, branch_id) — matches the PRIMARY KEY on dynamic_data —
        # updates the data + bumps version/updated_at so existing records get
        # refreshed instead of skipped.
        if prepared_records:
            values = [
                (r['id'], collection, psycopg2.extras.Json(r['data']), branch_id)
                for r in prepared_records
            ]
            psycopg2.extras.execute_values(
                cur,
                'INSERT INTO dynamic_data (id, collection, data, branch_id) VALUES %s '
                'ON CONFLICT (id, branch_id) DO UPDATE SET '
                '  data = EXCLUDED.data, '
                '  updated_at = NOW(), '
                '  version = dynamic_data.version + 1',
                values
            )

            # 8. Batch insert relations (forward and reverse, with branch_id)
            all_relation_values = []
            all_reverse_values = []

            for record in prepared_records:
                rid = record['id']
                relations = record.get('relations', {})
                pending = record.get('_pending_relations', [])

                # Process relations from request
                for field_name, related_ids in relations.items():
                    if not isinstance(related_ids, list):
                        continue
                    for related_id in related_ids:
                        # Find field config to get target collection
                        field_config = next((f for f in fields if f['fieldName'] == field_name), None)
                        if not field_config:
                            continue
                        rel_config = field_config.get('relationConfig', {})
                        target_collection = rel_config.get('targetCollection')
                        target_field = rel_config.get('targetField')

                        if target_collection:
                            all_relation_values.append(
                                (collection, rid, field_name, target_collection, related_id, branch_id)
                            )
                            if target_field:
                                all_reverse_values.append(
                                    (target_collection, related_id, target_field, collection, rid, branch_id)
                                )

                # Process pending relations from validation script
                for rel in pending:
                    field_name = rel['fieldName']
                    target_collection = rel['targetCollection']
                    target_field = rel.get('targetField')
                    for related_id in rel['ids']:
                        all_relation_values.append(
                            (collection, rid, field_name, target_collection, related_id, branch_id)
                        )
                        if target_field:
                            all_reverse_values.append(
                                (target_collection, related_id, target_field, collection, rid, branch_id)
                            )

            # Batch insert forward relations (with branch_id)
            if all_relation_values:
                psycopg2.extras.execute_values(
                    cur,
                    'INSERT INTO data_relations (collection, record_id, field_name, related_collection, related_id, branch_id) '
                    'VALUES %s ON CONFLICT DO NOTHING',
                    all_relation_values
                )

            # Batch insert reverse relations (bidirectional sync, with branch_id)
            if all_reverse_values:
                psycopg2.extras.execute_values(
                    cur,
                    'INSERT INTO data_relations (collection, record_id, field_name, related_collection, related_id, branch_id) '
                    'VALUES %s ON CONFLICT DO NOTHING',
                    all_reverse_values
                )

        # 旁路 create_item 的批量写入后，重播种序列计数器，避免后续创建与导入记录重号。
        # 导入保留源 autoSequence 值（不覆盖），仅把计数器抬到 GREATEST(计数, 已写入 max)。
        from utils.sequences import reseed_sequences
        reseed_sequences(cur, collections=[collection])

    # Log the batch upsert (created + updated counts)
    updated_count = sum(1 for r in prepared_records if r.get('is_update'))
    created_count = len(prepared_records) - updated_count
    if prepared_records:
        if updated_count and created_count:
            summary = f'新增 {created_count} 条 / 更新 {updated_count} 条'
        elif updated_count:
            summary = f'更新 {updated_count} 条记录'
        else:
            summary = f'{created_count} 条记录'
        log_operation('create', 'dynamic_data', ','.join(r['id'] for r in prepared_records[:3]),
                      summary, f'批量导入{page_name}「{summary}」', branch_id=branch_id)

    # Fire webhook triggers for batch create / update
    try:
        from utils.webhook_engine import fire_webhooks
        user = getattr(flask_g, 'current_user', {}) if hasattr(flask_g, 'current_user') else {}
        for r in prepared_records:
            event = 'update' if r.get('is_update') else 'create'
            fire_webhooks(event, collection, r['id'], None, r,
                          user.get('username', ''), branch_id=branch_id)
    except Exception:
        pass

    result = {
        "success": True,
        "created": created_count,
        "updated": updated_count,
        "failed": len(errors),
        "sequenceValues": sequence_values
    }

    if errors:
        result["errors"] = errors

    return jsonify(result), 201


@dynamic_bp.route('/<collection>/batch-delete', methods=['POST'])
@login_required
def batch_delete_items(collection, **kwargs):
    """Batch delete multiple records in a single transaction."""
    if collection in RESERVED:
        return jsonify({"error": "Not found"}), 404
    denied = require_page_action(collection, 'delete')
    if denied:
        return denied
    ids = request.json.get('ids', [])
    if not ids:
        return jsonify({"error": "ids is required"}), 400

    branch_id = _get_current_user_branch(collection)

    # 检查分支锁定（包括 main 分支）
    lock_info = check_branch_lock(collection)
    if lock_info:
        return jsonify({"error": f"当前分支已被 {lock_info[1]} 锁定，无法进行修改操作"}), 403

    with get_db() as conn:
        cur = conn.cursor()
        page_name, fields = get_page_info(cur, collection)

        # Check reference dependencies for all IDs at once (within same branch)
        ref_fields = []
        cur.execute('SELECT id, name, fields FROM page_configs')
        rows = cur.fetchall()
        for page_id, pname, pfields in rows:
            if not pfields:
                continue
            child_collection = page_id.replace('page-', '')
            for field in pfields:
                if field.get('controlType') != 'reference':
                    continue
                ref_config = field.get('referenceConfig', {})
                if ref_config.get('targetCollection') == collection:
                    ref_fields.append((child_collection, field['fieldName'], pname))

        blocked_ids = {}
        for child_col, field_name, pname in ref_fields:
            cur.execute(
                "SELECT data->>%s FROM dynamic_data WHERE collection = %s AND data->>%s = ANY(%s) AND branch_id = %s",
                (field_name, child_col, field_name, ids, branch_id),
            )
            for (ref_id,) in cur.fetchall():
                if ref_id and ref_id in ids:
                    blocked_ids.setdefault(ref_id, []).append(pname)

        deletable_ids = [i for i in ids if i not in blocked_ids]

        deleted = 0
        record_names = []
        if deletable_ids:
            # Fetch record names for logging (within same branch)
            cur.execute(
                'SELECT id, data FROM dynamic_data WHERE collection = %s AND id = ANY(%s) AND branch_id = %s',
                (collection, deletable_ids, branch_id),
            )
            id_name_map = {}
            for row_id, data in cur.fetchall():
                id_name_map[row_id] = pick_display_name(data, fields) or row_id if data else row_id
            record_names = [id_name_map.get(i, i) for i in deletable_ids]

            # Batch delete records (within same branch)
            cur.execute(
                'DELETE FROM dynamic_data WHERE collection = %s AND id = ANY(%s) AND branch_id = %s',
                (collection, deletable_ids, branch_id),
            )
            deleted = cur.rowcount

            # Batch clean up relations (within same branch)
            cur.execute(
                'DELETE FROM data_relations WHERE ((collection = %s AND record_id = ANY(%s)) OR (related_collection = %s AND related_id = ANY(%s))) AND branch_id = %s',
                (collection, deletable_ids, collection, deletable_ids, branch_id),
            )

    if deleted > 0:
        summary = '、'.join(record_names[:3])
        if len(record_names) > 3:
            summary += f' 等{len(record_names)}条'
        log_operation('delete', 'dynamic_data', ','.join(deletable_ids[:3]), summary,
                      f'批量删除{page_name}「{summary}」', branch_id=branch_id)
        # Fire webhook triggers for batch delete
        try:
            from utils.webhook_engine import fire_webhooks
            user = getattr(flask_g, 'current_user', {}) if hasattr(flask_g, 'current_user') else {}
            for del_id in deletable_ids:
                fire_webhooks('delete', collection, del_id, None, None,
                              user.get('username', ''), branch_id=branch_id)
        except Exception:
            pass

    result = {"deleted": deleted}
    if blocked_ids:
        result["blocked"] = {rid: f'被「{"、".join(set(pages))}」引用' for rid, pages in blocked_ids.items()}
    return jsonify(result)
