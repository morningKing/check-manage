"""
ETL 执行引擎

职责：
- 按顺序执行管道步骤，数据在步骤间流转
- 支持 HTTP 请求、JSON 输入、脚本转换、字段映射、过滤、写入集合
- 支持 dry_run 模式（测试运行，不实际写入数据库）
"""

import json
import uuid
import urllib.request
import urllib.error
from datetime import datetime, timezone
import psycopg2.extras
import pandas as pd

from utils.script_runner import run_etl_script

# HTTP 请求超时（秒）
HTTP_TIMEOUT = 30

# save_to_collection 批量写库的批大小。大小上参照前端 importPageRecords.ts 的
# BATCH_SIZE 惯例，但这是本文件里全新的常量，不是复用某个已有的共享常量。
SAVE_BATCH_SIZE = 1000


def execute_task(task_dict, conn, dry_run=False, sample_limit=None,
                  step_cb=None, progress_cb=None, cancel_check=None):
    """
    执行 ETL 任务管道。

    按顺序执行 steps 中的每个步骤，数据在步骤间通过 context['records'] 流转。

    参数:
        task_dict: 任务字典，包含 steps 列表
        conn: 数据库连接
        dry_run: True 时不实际写入数据库
        sample_limit: 不为空时，每步执行完都把 context['records'] 截到前 N 条
            （dry run 用，配合 _step_file_upload 的 nrows= 避免读全量文件）
        step_cb(step_name): 每步开始前调用一次，供调用方展示"当前在跑哪一步"
        progress_cb(current, total): 透传给 save_to_collection，每写完一批调用一次
        cancel_check() -> bool: 每步开始前检查一次；save_to_collection 内部
            每批之间也会检查。返回 True 时立即停止，不再执行后续步骤

    返回:
        context 字典，包含 records, step_results, total, success, error, errors, cancelled
    """
    context = {
        'records': [],
        'step_results': [],
        'total': 0,
        'success': 0,
        'error': 0,
        'errors': [],
        'cancelled': False,
    }

    steps = task_dict.get('steps', [])
    if not steps:
        return context

    for step in steps:
        if cancel_check and cancel_check():
            context['cancelled'] = True
            break

        step_id = step.get('id', '')
        step_name = step.get('name', '')
        step_type = step.get('type', '')
        config = step.get('config', {})
        on_error = step.get('onError', 'stop')

        if step_cb:
            step_cb(step_name)

        try:
            _execute_step(step_type, config, context, conn, dry_run,
                          sample_limit=sample_limit, progress_cb=progress_cb,
                          cancel_check=cancel_check)
            if sample_limit:
                context['records'] = context['records'][:sample_limit]
            context['step_results'].append({
                'stepId': step_id,
                'stepName': step_name,
                'status': 'success',
                'recordCount': len(context['records']),
                'sampleRecords': context['records'][:5],
            })
            if context.get('cancelled'):
                break
        except Exception as e:
            error_msg = f'步骤「{step_name}」执行失败: {str(e)}'
            context['errors'].append(error_msg)
            context['step_results'].append({
                'stepId': step_id,
                'stepName': step_name,
                'status': 'error',
                'error': str(e),
            })
            if on_error == 'stop':
                break
            # skip / continue: 继续执行下一步骤

    return context


def _execute_step(step_type, config, context, conn, dry_run,
                   sample_limit=None, progress_cb=None, cancel_check=None):
    """根据步骤类型分发执行。

    sample_limit 只有 file_upload 用得到（限制读取行数）；progress_cb/cancel_check
    只有 save_to_collection 用得到（Task 3 引入）；其余步骤类型忽略这几个参数。
    """
    if step_type == 'http_request':
        _step_http_request(config, context)
    elif step_type == 'json_input':
        _step_json_input(config, context)
    elif step_type == 'file_upload':
        _step_file_upload(config, context, conn, sample_limit=sample_limit)
    elif step_type == 'script':
        _step_script(config, context)
    elif step_type == 'field_mapping':
        _step_field_mapping(config, context)
    elif step_type == 'filter':
        _step_filter(config, context)
    elif step_type == 'save_to_collection':
        _step_save_to_collection(config, context, conn, dry_run,
                                  progress_cb=progress_cb, cancel_check=cancel_check)
    else:
        raise ValueError(f'未知的步骤类型: {step_type}')


def _resolve_path(data, path):
    """
    按点号路径从嵌套字典中提取值。
    例: _resolve_path({"data": {"items": [1,2]}}, "data.items") → [1, 2]
    """
    if not path:
        return data
    parts = path.strip().split('.')
    current = data
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


# ==================== 步骤实现 ====================


def _step_http_request(config, context):
    """HTTP 请求步骤：调用外部 API 获取数据。"""
    url = config.get('url', '')
    method = config.get('method', 'GET').upper()
    headers_list = config.get('headers', [])
    body = config.get('body', '')
    response_path = config.get('responsePath', '')

    if not url:
        raise ValueError('URL 不能为空')

    # 构建请求
    req_data = body.encode('utf-8') if body and method == 'POST' else None
    req = urllib.request.Request(url, data=req_data, method=method)

    # 设置 headers
    for h in headers_list:
        key = h.get('key', '').strip()
        value = h.get('value', '')
        if key:
            req.add_header(key, value)

    if method == 'POST' and not req.has_header('Content-Type'):
        req.add_header('Content-Type', 'application/json')

    # 发起请求
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            resp_body = resp.read().decode('utf-8')
    except urllib.error.HTTPError as e:
        raise ValueError(f'HTTP 请求失败: {e.code} {e.reason}')
    except urllib.error.URLError as e:
        raise ValueError(f'请求错误: {str(e.reason)}')

    # 解析 JSON
    try:
        resp_data = json.loads(resp_body)
    except json.JSONDecodeError:
        raise ValueError('响应不是有效的 JSON 格式')

    # 提取数据
    if response_path:
        records = _resolve_path(resp_data, response_path)
    else:
        records = resp_data

    if records is None:
        raise ValueError(f'响应路径「{response_path}」未找到数据')

    if isinstance(records, dict):
        records = [records]
    elif not isinstance(records, list):
        raise ValueError(f'提取的数据不是数组或对象，而是 {type(records).__name__}')

    context['records'] = records


def _step_json_input(config, context):
    """JSON 输入步骤：手动输入 JSON 数据。"""
    data_str = config.get('data', '[]')
    try:
        data = json.loads(data_str)
    except json.JSONDecodeError as e:
        raise ValueError(f'JSON 解析失败: {str(e)}')

    if isinstance(data, dict):
        data = [data]
    elif not isinstance(data, list):
        raise ValueError('JSON 数据必须是数组或对象')

    context['records'] = data


def _step_file_upload(config, context, conn, sample_limit=None):
    """文件上传步骤：读取配置时上传并固定的 Excel/CSV 文件，解析为记录列表。

    sample_limit 不为空时用 nrows= 真正只读文件的前 N 行（不是读全量再截断）——
    dry run 靠这个避免大文件读取本身拖慢试运行。
    """
    file_id = config.get('fileId')
    if not file_id:
        raise ValueError('未上传文件')

    cur = conn.cursor()
    cur.execute('SELECT original_name, storage_path FROM data_files WHERE id = %s', (file_id,))
    row = cur.fetchone()
    if not row:
        raise ValueError('文件不存在或已被删除')
    original_name, storage_path = row

    ext = original_name.lower().rsplit('.', 1)[-1] if '.' in original_name else ''
    read_kwargs = {'nrows': sample_limit} if sample_limit else {}
    if ext == 'csv':
        df = pd.read_csv(storage_path, **read_kwargs)
    elif ext in ('xlsx', 'xls'):
        df = pd.read_excel(storage_path, **read_kwargs)
    else:
        raise ValueError(f'不支持的文件格式: {ext}')

    # Excel 的日期列 pandas 会自动解析成 Timestamp，Timestamp 不是合法 JSON 类型，
    # 后续 save_to_collection 用 psycopg2.extras.Json 写库时会针对该记录整条失败
    # （静默失败：不报错，只是这条记录不落库）。转成字符串，NaT 会变成 NaN，
    # 交给下面既有的 NaN→None 那一步统一处理。
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].dt.strftime('%Y-%m-%d %H:%M:%S')

    # NaN（空单元格）转 None，否则写入 dynamic_data 时 JSON 序列化会产出非法值
    # 先转为 object dtype 确保 None 不会被转回 NaN
    df = df.astype(object).where(pd.notnull(df), None)
    context['records'] = df.to_dict('records')


def _step_script(config, context):
    """Python 脚本转换步骤。"""
    script_code = config.get('script', '')
    if not script_code.strip():
        return  # 空脚本，跳过

    result = run_etl_script(script_code, context['records'])
    if not isinstance(result, list):
        raise ValueError('脚本 result 必须是列表')
    context['records'] = result


def _step_field_mapping(config, context):
    """字段映射步骤：重命名字段。"""
    mappings = config.get('mappings', [])
    keep_unmapped = config.get('keepUnmapped', False)

    if not mappings:
        return

    mapping_dict = {m['source']: m['target'] for m in mappings if m.get('source') and m.get('target')}
    new_records = []

    for record in context['records']:
        if keep_unmapped:
            new_record = dict(record)
            for src, tgt in mapping_dict.items():
                if src in new_record:
                    val = new_record.pop(src)
                    new_record[tgt] = val
        else:
            new_record = {}
            for src, tgt in mapping_dict.items():
                if src in record:
                    new_record[tgt] = record[src]
        new_records.append(new_record)

    context['records'] = new_records


def _step_filter(config, context):
    """条件过滤步骤：按 Python 表达式过滤记录。"""
    expression = config.get('expression', '').strip()
    if not expression:
        return

    filtered = []
    safe_builtins = {
        'len': len, 'str': str, 'int': int, 'float': float, 'bool': bool,
        'list': list, 'dict': dict, 'tuple': tuple, 'set': set,
        'abs': abs, 'round': round, 'min': min, 'max': max, 'sum': sum,
        'isinstance': isinstance, 'hasattr': hasattr,
        'True': True, 'False': False, 'None': None,
    }

    for record in context['records']:
        try:
            result = eval(expression, {'__builtins__': safe_builtins, 'record': record})
            if result:
                filtered.append(record)
        except Exception:
            pass  # 表达式出错的记录跳过

    context['records'] = filtered


def _write_batch(cur, collection, mode, match_field, batch, now):
    """写一批记录，返回 (success_count, skipped_count)。

    任何异常直接向上抛，不在这里捕获——PostgreSQL 事务一旦出错，同一个连接
    后续的语句会全部失败（"current transaction is aborted"），调用方必须先
    ROLLBACK 才能继续用这个连接处理下一批，所以异常处理和 rollback 的职责
    留给调用方（_step_save_to_collection），这里只管纯粹的写库逻辑。
    """
    if mode == 'insert':
        values = []
        for record in batch:
            record_data = {k: v for k, v in record.items() if k not in ('id', 'createdAt')}
            rid = record.get('id') or f'rec-{uuid.uuid4().hex[:12]}'
            values.append((rid, collection, psycopg2.extras.Json(record_data), now))
        psycopg2.extras.execute_values(
            cur,
            'INSERT INTO dynamic_data (id, collection, data, created_at) VALUES %s',
            values,
        )
        return len(batch), 0

    if not match_field:
        raise ValueError(f'{mode} 模式需要指定匹配字段')

    # 批量找出这一批里已存在的匹配记录：一次查询代替原来"每条记录一次 SELECT"，
    # 这是分批写库相对逐条写库的核心收益之一。
    match_values = [str(r[match_field]) for r in batch if r.get(match_field) is not None]
    existing_map = {}
    if match_values:
        cur.execute(
            "SELECT id, data->>%s AS mv FROM dynamic_data WHERE collection = %s AND data->>%s = ANY(%s)",
            (match_field, collection, match_field, match_values),
        )
        for eid, mv in cur.fetchall():
            existing_map[mv] = eid

    insert_values = []
    updated = 0
    skipped = 0
    for record in batch:
        record_data = {k: v for k, v in record.items() if k not in ('id', 'createdAt')}
        match_val = record.get(match_field)
        existing_id = existing_map.get(str(match_val)) if match_val is not None else None

        if existing_id:
            # 每条记录的 data 值都不同，UPDATE ... SET data = %s 无法用单条
            # execute_values 语句一次性表达"多行各自不同的值"，仍然逐条执行——
            # 但已经从"每条记录 1 次 SELECT + 1 次 UPDATE"降到"每批 1 次
            # SELECT + 每条命中记录 1 次 UPDATE"，且不再是同步阻塞的单条请求。
            cur.execute(
                'UPDATE dynamic_data SET data = %s, updated_at = NOW(), version = version + 1 WHERE id = %s',
                (psycopg2.extras.Json(record_data), existing_id),
            )
            updated += 1
        elif mode == 'upsert':
            rid = record.get('id') or f'rec-{uuid.uuid4().hex[:12]}'
            insert_values.append((rid, collection, psycopg2.extras.Json(record_data), now))
        else:
            # update 模式且没有匹配到已存在记录：计为失败（与原逐条实现一致）
            skipped += 1

    if insert_values:
        psycopg2.extras.execute_values(
            cur,
            'INSERT INTO dynamic_data (id, collection, data, created_at) VALUES %s',
            insert_values,
        )

    return updated + len(insert_values), skipped


def _step_save_to_collection(config, context, conn, dry_run, progress_cb=None, cancel_check=None):
    """写入集合步骤：将数据分批写入系统 dynamic_data 表。

    每批写完立即 commit（不再是整任务一个大事务）：一是让 progress_cb 汇报的
    进度对其它连接（轮询接口）立即可见，二是让取消/崩溃时已经成功写入的批次
    不会丢失——这是与用户确认过的行为变化，见设计文档「数据模型改动」一节。

    progress_cb(current, total): 每批写完（或 dry_run 下每批"模拟写完"）调用一次
    cancel_check() -> bool: 每批之间检查一次；返回 True 时停止后续批次，
        context['cancelled'] 置 True，本批（已经写完的）不受影响
    """
    collection = config.get('collection', '')
    mode = config.get('mode', 'insert')
    match_field = config.get('matchField', '')

    if not collection:
        raise ValueError('目标集合不能为空')

    records = context['records']
    if not records:
        return

    cur = conn.cursor()
    now = datetime.now(timezone.utc)
    success = 0
    errors = 0

    for start in range(0, len(records), SAVE_BATCH_SIZE):
        batch = records[start:start + SAVE_BATCH_SIZE]

        if dry_run:
            success += len(batch)
        else:
            try:
                batch_success, batch_skipped = _write_batch(cur, collection, mode, match_field, batch, now)
                success += batch_success
                errors += batch_skipped
                conn.commit()
            except Exception as e:
                conn.rollback()
                errors += len(batch)
                context['errors'].append(f'批量写入失败（{len(batch)} 条）: {str(e)}')

        if progress_cb:
            progress_cb(success + errors, len(records))
        if cancel_check and cancel_check():
            context['cancelled'] = True
            break

    if not dry_run and success > 0:
        from utils.sequences import reseed_sequences
        reseed_sequences(cur, collections=[collection], branch_id='main')
        conn.commit()

    context['total'] = len(records)
    context['success'] = success
    context['error'] = errors
