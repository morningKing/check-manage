"""
备份管理 API 路由

所有端点仅管理员可访问。
"""

import os
import json
import zipfile
import tempfile
from flask import Blueprint, request, jsonify, send_file
from db import get_db
from auth import require_permission
from utils.backup import (
    create_backup,
    restore_backup,
    delete_backup_file,
    get_backup_settings,
    update_backup_settings,
    get_backup_table_names,
    factory_reset,
    BACKUP_DIR,
)

backups_bp = Blueprint('backups', __name__)


def _row_to_dict(row):
    """将备份记录行转为 dict"""
    return {
        'id': row[0],
        'name': row[1],
        'type': row[2],
        'status': row[3],
        'fileSize': row[5] or 0,
        'tablesCount': row[6] or 0,
        'recordsCount': row[7] or 0,
        'createdBy': row[8],
        'createdAt': row[9].isoformat() if row[9] else None,
        'note': row[10],
        'backupScope': row[11] if len(row) > 11 else 'full',
        'backupTables': row[12] if len(row) > 12 else [],
    }


@backups_bp.route('/backups', methods=['GET'])
@require_permission('admin.backup')
def list_backups():
    """获取备份列表（按时间倒序）"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT id, name, type, status, file_path, file_size, '
            'tables_count, records_count, created_by, created_at, note, backup_scope, backup_tables '
            'FROM backups ORDER BY created_at DESC'
        )
        rows = cur.fetchall()
    return jsonify([_row_to_dict(r) for r in rows])


@backups_bp.route('/backups', methods=['POST'])
@require_permission('admin.backup')
def create_manual_backup():
    """创建手动备份"""
    from flask import g
    body = request.get_json(silent=True) or {}
    note = body.get('note')
    tables = body.get('tables')  # None = 全量备份
    created_by = g.current_user.get('username', 'admin')

    try:
        result = create_backup(backup_type='manual', created_by=created_by, tables=tables)
        # 更新备注
        if note:
            with get_db() as conn:
                cur = conn.cursor()
                cur.execute('UPDATE backups SET note = %s WHERE id = %s', (note, result['id']))
            result['note'] = note
        return jsonify(result), 201
    except Exception as e:
        return jsonify({'error': f'备份失败: {str(e)}'}), 500


@backups_bp.route('/backups/<backup_id>', methods=['DELETE'])
@require_permission('admin.backup')
def delete_backup(backup_id):
    """删除备份（文件+记录）"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT file_path FROM backups WHERE id = %s', (backup_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({'error': '备份不存在'}), 404
        delete_backup_file(row[0])
        cur.execute('DELETE FROM backups WHERE id = %s', (backup_id,))
    return jsonify({})


@backups_bp.route('/backups/<backup_id>/download', methods=['GET'])
@require_permission('admin.backup')
def download_backup(backup_id):
    """下载备份 ZIP 文件"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT file_path, name FROM backups WHERE id = %s', (backup_id,))
        row = cur.fetchone()
    if not row:
        return jsonify({'error': '备份不存在'}), 404
    file_path = row[0]
    if not file_path or not os.path.isfile(file_path):
        return jsonify({'error': '备份文件不存在'}), 404
    filename = f'{row[1]}.zip'
    return send_file(file_path, as_attachment=True, download_name=filename)


@backups_bp.route('/backups/<backup_id>/restore', methods=['POST'])
@require_permission('admin.backup')
def restore_from_backup(backup_id):
    """从已有备份还原。

    body 字段:
      tables: list[str] | None
        None = 全量还原(所有备份内的表一并替换)
        [...] = 选择性还原
      mode: 'upsert' (默认) | 'replace'
        仅在选择性还原下生效。
        - 'upsert' 安全:不会让其他表的外键变孤儿。
        - 'replace' 兼容旧行为,会 DELETE 表;选择性 replace 触发依赖检查,
          若发现风险且未带 confirmUnsafe=true,返回 409 + warnings。
      confirmUnsafe: bool
        replace 模式专用的二次确认。
    """
    from utils.backup import compute_restore_warnings
    body = request.get_json(silent=True) or {}
    tables = body.get('tables')
    mode = (body.get('mode') or 'upsert').lower()
    confirm_unsafe = bool(body.get('confirmUnsafe'))
    if mode not in ('upsert', 'replace'):
        return jsonify({'error': f'invalid mode: {mode!r}'}), 400

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT file_path FROM backups WHERE id = %s', (backup_id,))
        row = cur.fetchone()
    if not row:
        return jsonify({'error': '备份不存在'}), 404
    file_path = row[0]
    if not file_path or not os.path.isfile(file_path):
        return jsonify({'error': '备份文件不存在'}), 404

    # Selective replace → check orphan risk before doing anything
    warnings = []
    if tables and mode == 'replace':
        # Compute warnings against the BASE table names (strip the
        # `dynamic_data:collection-foo` suffix some entries may carry).
        base_tables = [t.split(':', 1)[0] for t in tables]
        warnings = compute_restore_warnings(base_tables, mode='replace')
        if warnings and not confirm_unsafe:
            return jsonify({
                'error': '检测到选择性 replace 还原会让其他表产生孤儿外键引用',
                'code': 'RESTORE_ORPHAN_RISK',
                'warnings': warnings,
                'hint': '改用 upsert 模式(只覆盖备份里有的行)或带 confirmUnsafe=true 强制 replace。',
            }), 409

    try:
        manifest = restore_backup(file_path, tables=tables, mode=mode)
        return jsonify({
            'message': '还原成功',
            'manifest': manifest,
            'mode': 'replace' if not tables else mode,  # full restore is always replace-like
            'warnings': warnings,
        })
    except Exception as e:
        return jsonify({'error': f'还原失败: {str(e)}'}), 500


@backups_bp.route('/backups/upload-restore', methods=['POST'])
@require_permission('admin.backup')
def upload_and_restore():
    """上传外部 ZIP 并还原"""
    if 'file' not in request.files:
        return jsonify({'error': '请上传备份文件'}), 400
    file = request.files['file']
    if not file.filename or not file.filename.endswith('.zip'):
        return jsonify({'error': '请上传 ZIP 格式的备份文件'}), 400

    # 保存到临时文件
    tmp_fd, tmp_path = tempfile.mkstemp(suffix='.zip')
    try:
        os.close(tmp_fd)
        file.save(tmp_path)
        manifest = restore_backup(tmp_path)
        return jsonify({
            'message': '还原成功',
            'manifest': manifest,
        })
    except Exception as e:
        return jsonify({'error': f'还原失败: {str(e)}'}), 500
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


@backups_bp.route('/backups/settings', methods=['GET'])
@require_permission('admin.backup')
def get_settings():
    """获取定时备份设置"""
    settings = get_backup_settings()
    return jsonify(settings)


@backups_bp.route('/backups/settings', methods=['PUT'])
@require_permission('admin.backup')
def update_settings():
    """更新定时备份设置"""
    body = request.get_json(force=True)
    enabled = body.get('enabled', False)
    interval = body.get('interval', 'daily')
    retention_count = body.get('retentionCount', 10)

    if interval not in ('daily', 'weekly', 'monthly'):
        return jsonify({'error': '无效的备份周期'}), 400
    if not isinstance(retention_count, int) or retention_count < 1:
        return jsonify({'error': '保留数量必须为正整数'}), 400

    settings = update_backup_settings(enabled, interval, retention_count)
    return jsonify(settings)


@backups_bp.route('/backups/tables', methods=['GET'])
@require_permission('admin.backup')
def list_backup_tables():
    """获取可备份的表列表，包括动态数据的 collection 分组"""
    from utils.backup import get_backup_table_names

    base_tables = get_backup_table_names()

    # 获取动态数据表中的所有 collection
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT DISTINCT collection FROM dynamic_data ORDER BY collection'
        )
        collections = [row[0] for row in cur.fetchall()]

        # 获取分支名称映射
        cur.execute('SELECT id, name FROM collection_versions')
        branch_name_map = {row[0]: row[1] for row in cur.fetchall()}

        # 获取每个 collection 的记录数（按分支分组）
        collection_stats = {}
        for col in collections:
            cur.execute(
                'SELECT branch_id, COUNT(*) FROM dynamic_data WHERE collection = %s GROUP BY branch_id ORDER BY branch_id',
                (col,),
            )
            branch_counts = []
            for row in cur.fetchall():
                bid = row[0]
                label = '主分支' if bid == 'main' else branch_name_map.get(bid, f'未知分支({bid[:8]})')
                branch_counts.append({'branch': label, 'count': row[1]})
            collection_stats[col] = branch_counts

        # 获取每个 collection 对应的页面名称
        cur.execute(
            'SELECT SUBSTRING(id FROM 6) AS col, name FROM page_configs WHERE id LIKE \'page-%\''
        )
        page_names = {row[0]: row[1] for row in cur.fetchall()}

    # 构建结果：基础表 + 动态数据 collection 分组
    result = []
    for t in base_tables:
        if t['name'] == 'dynamic_data':
            # 动态数据表展开为 collection 列表
            result.append({
                'name': 'dynamic_data',
                'label': t['label'],
                'isGroup': True,
                'children': [
                    {
                        'name': f'dynamic_data:{col}',
                        'label': page_names.get(col, col),
                        'branchCounts': collection_stats.get(col, []),
                    }
                    for col in collections
                ]
            })
        else:
            result.append({
                'name': t['name'],
                'label': t['label'],
                'isGroup': False,
            })

    return jsonify(result)


# ==================== 备份数据对比 ====================


def _load_version_collection(version_id, collection):
    """从版本快照中读取指定集合的 dynamic_data 和 data_relations 记录。

    返回 (records, relations_map, error)。
    """
    with get_db() as conn:
        cur = conn.cursor()
        # 检查版本是否存在
        cur.execute(
            'SELECT id FROM collection_versions WHERE id = %s AND collection = %s',
            (version_id, collection),
        )
        if not cur.fetchone():
            return None, None, '版本不存在或不属于该集合'

        # 加载快照数据
        cur.execute(
            'SELECT record_id, record_data FROM version_snapshots WHERE version_id = %s',
            (version_id,),
        )
        snapshot_rows = cur.fetchall()

        # 加载关联数据
        cur.execute(
            'SELECT record_id, field_name, related_id FROM version_relations WHERE version_id = %s',
            (version_id,),
        )
        rel_rows = cur.fetchall()

    # 构建记录列表
    result = []
    for rid, data in snapshot_rows:
        flat = {'id': rid}
        if isinstance(data, dict):
            flat.update(data)
        result.append(flat)

    # 构建关联映射
    rel_map = {}
    for record_id, field_name, related_id in rel_rows:
        rel_map.setdefault(record_id, {}).setdefault(field_name, []).append(related_id)

    return result, rel_map, None


def _load_backup_collection(backup_id, collection):
    """从备份 ZIP 中读取指定集合的 dynamic_data 和 data_relations 记录。

    返回 (records, relations_map, error)。
    records: list[dict]，每条记录的 data 平铺加上 id。
    relations_map: {record_id: {field_name: sorted([related_id, ...])}}
    """
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT file_path FROM backups WHERE id = %s', (backup_id,))
        row = cur.fetchone()
    if not row:
        return None, None, '备份不存在'
    file_path = row[0]
    if not file_path or not os.path.isfile(file_path):
        return None, None, '备份文件不存在'

    try:
        with zipfile.ZipFile(file_path, 'r') as zf:
            names = zf.namelist()
            if 'dynamic_data.json' not in names:
                return [], {}, None
            all_records = json.loads(zf.read('dynamic_data.json'))
            all_relations = json.loads(zf.read('data_relations.json')) if 'data_relations.json' in names else []
    except Exception as e:
        return None, None, f'读取备份文件失败: {str(e)}'

    # 筛选目标集合，展开 data JSONB 为平铺字段
    result = []
    for rec in all_records:
        if rec.get('collection') != collection:
            continue
        flat = {'id': rec['id'], 'createdAt': rec.get('created_at')}
        data = rec.get('data')
        if isinstance(data, dict):
            flat.update(data)
        result.append(flat)

    # 构建关联映射
    rel_map = {}
    for rel in all_relations:
        if rel.get('collection') != collection:
            continue
        rid = rel.get('record_id')
        fn = rel.get('field_name')
        related = rel.get('related_id')
        if rid and fn and related:
            rel_map.setdefault(rid, {}).setdefault(fn, []).append(related)
    # 排序以便稳定比较
    for rid in rel_map:
        for fn in rel_map[rid]:
            rel_map[rid][fn].sort()

    return result, rel_map, None


def _load_current_collection(collection, branch_id=None):
    """从数据库读取指定集合的当前 dynamic_data 和 data_relations 记录。

    Parameters:
        collection: str — 集合名称
        branch_id: str | None — 分支ID，None 表示查询所有分支（兼容旧调用）

    返回 (records, relations_map)。
    """
    with get_db() as conn:
        cur = conn.cursor()
        if branch_id:
            cur.execute(
                'SELECT id, data, created_at FROM dynamic_data WHERE collection = %s AND branch_id = %s',
                (collection, branch_id),
            )
        else:
            cur.execute(
                'SELECT id, data, created_at FROM dynamic_data WHERE collection = %s',
                (collection,),
            )
        rows = cur.fetchall()

        if branch_id:
            cur.execute(
                'SELECT record_id, field_name, related_id FROM data_relations WHERE collection = %s AND branch_id = %s '
                'ORDER BY record_id, field_name, related_id',
                (collection, branch_id),
            )
        else:
            cur.execute(
                'SELECT record_id, field_name, related_id FROM data_relations WHERE collection = %s '
                'ORDER BY record_id, field_name, related_id',
                (collection,),
            )
        rel_rows = cur.fetchall()

    result = []
    for row in rows:
        flat = {'id': row[0], 'createdAt': row[2].isoformat() if row[2] else None}
        if isinstance(row[1], dict):
            flat.update(row[1])
        result.append(flat)

    rel_map = {}
    for record_id, field_name, related_id in rel_rows:
        rel_map.setdefault(record_id, {}).setdefault(field_name, []).append(related_id)

    return result, rel_map


def _merge_relations(records, rel_map, relation_fields):
    """将关联数据合并到记录中，使关联字段可被 diff 比较。

    relation_fields: [{fieldName, ...}, ...] — controlType == 'relation' 的字段列表
    rel_map: {record_id: {field_name: [related_id, ...]}}
    """
    for rec in records:
        rid = rec['id']
        rec_rels = rel_map.get(rid, {})
        for rf in relation_fields:
            fn = rf['fieldName']
            rec[fn] = rec_rels.get(fn, [])


def _resolve_relation_labels(records, relation_fields):
    """将关联 ID 列表替换为可读标签列表（用逗号分隔的字符串）。

    同时在记录中保留原始 ID 列表以便展开详情。
    """
    # 按 targetCollection 分组加载
    collection_records = {}
    for rf in relation_fields:
        rc = rf.get('relationConfig') or {}
        tc = rc.get('targetCollection')
        if tc and tc not in collection_records:
            try:
                with get_db() as conn:
                    cur = conn.cursor()
                    cur.execute(
                        'SELECT id, data FROM dynamic_data WHERE collection = %s',
                        (tc,),
                    )
                    rows = cur.fetchall()
                collection_records[tc] = rows
            except Exception:
                collection_records[tc] = []

    # 构建 ID → 显示值映射
    label_maps = {}
    for rf in relation_fields:
        rc = rf.get('relationConfig') or {}
        tc = rc.get('targetCollection')
        display_field = rc.get('displayField', '')
        if tc not in label_maps:
            id_map = {}
            for row_id, row_data in collection_records.get(tc, []):
                label = row_data.get(display_field, row_id) if isinstance(row_data, dict) else row_id
                id_map[row_id] = label or row_id
            label_maps[tc] = id_map

    # 替换记录中的 ID 列表为标签列表
    for rec in records:
        for rf in relation_fields:
            fn = rf['fieldName']
            rc = rf.get('relationConfig') or {}
            tc = rc.get('targetCollection')
            ids = rec.get(fn)
            if isinstance(ids, list) and tc in label_maps:
                rec[fn] = sorted([label_maps[tc].get(rid, rid) for rid in ids])
            elif isinstance(ids, list):
                rec[fn] = sorted(ids)
            else:
                rec[fn] = []


def _compute_diff(base_records, target_records, field_names):
    """计算两组记录的差异。

    base_records: 基准数据（"旧"）
    target_records: 对比数据（"新"）
    field_names: 要比较的字段名列表

    返回 {added, removed, modified, unchanged} 各为列表。
    modified 项含 fields: [{fieldName, oldValue, newValue}]
    """
    base_map = {r['id']: r for r in base_records}
    target_map = {r['id']: r for r in target_records}

    base_ids = set(base_map.keys())
    target_ids = set(target_map.keys())

    added = []
    for rid in sorted(target_ids - base_ids):
        added.append(target_map[rid])

    removed = []
    for rid in sorted(base_ids - target_ids):
        removed.append(base_map[rid])

    modified = []
    unchanged_count = 0
    for rid in sorted(base_ids & target_ids):
        old = base_map[rid]
        new = target_map[rid]
        changed_fields = []
        for fn in field_names:
            old_val = old.get(fn)
            new_val = new.get(fn)
            if old_val != new_val:
                changed_fields.append({
                    'fieldName': fn,
                    'oldValue': old_val,
                    'newValue': new_val,
                })
        if changed_fields:
            modified.append({
                'id': rid,
                'record': new,
                'oldRecord': old,
                'fields': changed_fields,
            })
        else:
            unchanged_count += 1

    return {
        'added': added,
        'removed': removed,
        'modified': modified,
        'unchangedCount': unchanged_count,
    }


@backups_bp.route('/backups/diff', methods=['POST'])
@require_permission('admin.backup')
def diff_collection():
    """对比两个数据源中指定集合的差异。

    Body:
        collection: str         — 集合名称
        baseSource: "current" | backupId
        targetSource: "current" | backupId
        branchId: str           — 分支ID（可选，默认 'main'）
    """
    body = request.get_json(force=True)
    collection = body.get('collection')
    base_source = body.get('baseSource')
    target_source = body.get('targetSource')
    branch_id = body.get('branchId', 'main')  # 新增：默认使用 main 分支

    if not collection or not base_source or not target_source:
        return jsonify({'error': '缺少必要参数'}), 400
    if base_source == target_source:
        return jsonify({'error': '基准和对比数据源不能相同'}), 400

    # 加载基准数据
    if base_source == 'current':
        base_records, base_rels = _load_current_collection(collection, branch_id)
    elif base_source.startswith('ver-'):
        # 版本 ID
        base_records, base_rels, err = _load_version_collection(base_source, collection)
        if err:
            return jsonify({'error': f'基准数据加载失败: {err}'}), 400
    else:
        base_records, base_rels, err = _load_backup_collection(base_source, collection)
        if err:
            return jsonify({'error': f'基准数据加载失败: {err}'}), 400

    # 加载对比数据
    if target_source == 'current':
        target_records, target_rels = _load_current_collection(collection, branch_id)
    elif target_source.startswith('ver-'):
        # 版本 ID
        target_records, target_rels, err = _load_version_collection(target_source, collection)
        if err:
            return jsonify({'error': f'对比数据加载失败: {err}'}), 400
    else:
        target_records, target_rels, err = _load_backup_collection(target_source, collection)
        if err:
            return jsonify({'error': f'对比数据加载失败: {err}'}), 400

    # 获取字段配置
    page_id = f'page-{collection}'
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT fields FROM page_configs WHERE id = %s', (page_id,))
        row = cur.fetchone()
    fields = row[0] if row and row[0] else []
    field_names = [f['fieldName'] for f in fields]

    # 将关联数据合并到记录中以参与对比
    relation_fields = [f for f in fields if f.get('controlType') == 'relation']
    if relation_fields:
        _merge_relations(base_records, base_rels, relation_fields)
        _merge_relations(target_records, target_rels, relation_fields)
        _resolve_relation_labels(base_records, relation_fields)
        _resolve_relation_labels(target_records, relation_fields)

    diff = _compute_diff(base_records, target_records, field_names)
    diff['fields'] = fields

    return jsonify(diff)


# ==================== 恢复出厂设置 ====================


@backups_bp.route('/backups/factory-reset', methods=['POST'])
@require_permission('admin.backup')
def do_factory_reset():
    """
    恢复出厂设置

    删除所有动态业务数据，保留系统配置。
    执行前自动创建备份（标记为 pre-reset）。

    Body:
        confirmText: str - 必须为 "RESET"

    Returns:
        {
            "message": "恢复出厂设置成功",
            "deletedTables": ["dynamic_data", ...],
            "deletedRecords": { "dynamic_data": 1234, ... },
            "backupId": "backup-xxx",
            "timestamp": "2026-04-20T..."
        }
    """
    from flask import g
    body = request.get_json(silent=True) or {}
    confirm_text = body.get('confirmText')

    # 安全验证
    if confirm_text != 'RESET':
        return jsonify({'error': '确认文字不正确'}), 400

    try:
        operator = g.current_user.get('username', 'admin')
        result = factory_reset(created_by=operator)
        return jsonify({
            'message': '恢复出厂设置成功',
            'deletedTables': result['deletedTables'],
            'deletedRecords': result['deletedRecords'],
            'backupId': result['backupId'],
            'timestamp': result['timestamp'],
        })
    except Exception as e:
        return jsonify({'error': f'恢复出厂设置失败: {str(e)}'}), 500
