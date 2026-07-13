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


def execute_task(task_dict, conn, dry_run=False):
    """
    执行 ETL 任务管道。

    按顺序执行 steps 中的每个步骤，数据在步骤间通过 context['records'] 流转。

    参数:
        task_dict: 任务字典，包含 steps 列表
        conn: 数据库连接
        dry_run: True 时不实际写入数据库

    返回:
        context 字典，包含 records, step_results, total, success, error, errors
    """
    context = {
        'records': [],
        'step_results': [],
        'total': 0,
        'success': 0,
        'error': 0,
        'errors': [],
    }

    steps = task_dict.get('steps', [])
    if not steps:
        return context

    for step in steps:
        step_id = step.get('id', '')
        step_name = step.get('name', '')
        step_type = step.get('type', '')
        config = step.get('config', {})
        on_error = step.get('onError', 'stop')

        try:
            _execute_step(step_type, config, context, conn, dry_run)
            context['step_results'].append({
                'stepId': step_id,
                'stepName': step_name,
                'status': 'success',
                'recordCount': len(context['records']),
            })
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


def _execute_step(step_type, config, context, conn, dry_run):
    """根据步骤类型分发执行。"""
    if step_type == 'http_request':
        _step_http_request(config, context)
    elif step_type == 'json_input':
        _step_json_input(config, context)
    elif step_type == 'file_upload':
        _step_file_upload(config, context, conn)
    elif step_type == 'script':
        _step_script(config, context)
    elif step_type == 'field_mapping':
        _step_field_mapping(config, context)
    elif step_type == 'filter':
        _step_filter(config, context)
    elif step_type == 'save_to_collection':
        _step_save_to_collection(config, context, conn, dry_run)
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


def _step_file_upload(config, context, conn):
    """文件上传步骤：读取配置时上传并固定的 Excel/CSV 文件，解析为记录列表。"""
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
    if ext == 'csv':
        df = pd.read_csv(storage_path)
    elif ext in ('xlsx', 'xls'):
        df = pd.read_excel(storage_path)
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


def _step_save_to_collection(config, context, conn, dry_run):
    """写入集合步骤：将数据写入系统 dynamic_data 表。"""
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

    for record in records:
        try:
            if dry_run:
                success += 1
                continue

            # 分离 id 和 createdAt
            record_data = {k: v for k, v in record.items() if k not in ('id', 'createdAt')}

            if mode == 'insert':
                rid = record.get('id') or f'rec-{uuid.uuid4().hex[:12]}'
                cur.execute(
                    'INSERT INTO dynamic_data (id, collection, data, created_at) VALUES (%s, %s, %s, %s)',
                    (rid, collection, psycopg2.extras.Json(record_data), now),
                )
                success += 1

            elif mode == 'upsert':
                if not match_field:
                    raise ValueError('upsert 模式需要指定匹配字段')
                match_val = record.get(match_field)
                if match_val is None:
                    # 无匹配值，直接 insert
                    rid = record.get('id') or f'rec-{uuid.uuid4().hex[:12]}'
                    cur.execute(
                        'INSERT INTO dynamic_data (id, collection, data, created_at) VALUES (%s, %s, %s, %s)',
                        (rid, collection, psycopg2.extras.Json(record_data), now),
                    )
                    success += 1
                else:
                    cur.execute(
                        "SELECT id FROM dynamic_data WHERE collection = %s AND data->>%s = %s LIMIT 1",
                        (collection, match_field, str(match_val)),
                    )
                    existing = cur.fetchone()
                    if existing:
                        cur.execute(
                            'UPDATE dynamic_data SET data = %s, updated_at = NOW(), version = version + 1 WHERE id = %s',
                            (psycopg2.extras.Json(record_data), existing[0]),
                        )
                    else:
                        rid = record.get('id') or f'rec-{uuid.uuid4().hex[:12]}'
                        cur.execute(
                            'INSERT INTO dynamic_data (id, collection, data, created_at) VALUES (%s, %s, %s, %s)',
                            (rid, collection, psycopg2.extras.Json(record_data), now),
                        )
                    success += 1

            elif mode == 'update':
                if not match_field:
                    raise ValueError('update 模式需要指定匹配字段')
                match_val = record.get(match_field)
                if match_val is not None:
                    cur.execute(
                        "SELECT id FROM dynamic_data WHERE collection = %s AND data->>%s = %s LIMIT 1",
                        (collection, match_field, str(match_val)),
                    )
                    existing = cur.fetchone()
                    if existing:
                        cur.execute(
                            'UPDATE dynamic_data SET data = %s, updated_at = NOW(), version = version + 1 WHERE id = %s',
                            (psycopg2.extras.Json(record_data), existing[0]),
                        )
                        success += 1
                    else:
                        errors += 1
                else:
                    errors += 1

        except Exception as e:
            errors += 1
            context['errors'].append(f'写入记录失败: {str(e)}')

    # 闭合 autoSequence 计数器不变式：ETL 导入的记录可能携带超过 main 分支计数器的
    # 编号，导入后重播种 main，避免后续 create_item 重号。INSERT 均未指定 branch_id
    # → 默认 'main'。仅在实际写入（非 dry_run）且有成功写入时执行。
    if not dry_run and success > 0:
        from utils.sequences import reseed_sequences
        reseed_sequences(cur, collections=[collection], branch_id='main')

    context['total'] = len(records)
    context['success'] = success
    context['error'] = errors
