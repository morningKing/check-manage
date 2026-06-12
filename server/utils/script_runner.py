"""Script sandbox execution helpers."""

import collections
import csv
import io
import json
import math
import multiprocessing
import queue as _queue
import re
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from xml.dom import minidom

# Optional data libs
try:
    import pandas as pd
except ImportError:  # pragma: no cover
    pd = None

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

FORMAT_CONTENT_TYPES = {
    'json': 'application/json',
    'xml': 'application/xml',
    'csv': 'text/csv',
    'txt': 'text/plain',
    'html': 'text/html',
}

FORMAT_EXTENSIONS = {
    'json': '.json',
    'xml': '.xml',
    'csv': '.csv',
    'txt': '.txt',
    'html': '.html',
}

FORBIDDEN_NAMES = {
    'open', 'exec', 'eval', 'compile', '__import__', 'getattr', 'setattr',
    'delattr', 'globals', 'locals', 'vars', 'dir', 'type', 'super',
    'breakpoint', 'exit', 'quit', 'input', 'print',
}

SCRIPT_TIMEOUT = 60
MENU_SCRIPT_TIMEOUT = 300
DATA_LIBS_HINT = (
    'Pre-injected data libraries: pd (pandas), np (numpy). '
    'Install with: pip install pandas numpy'
)


def _safe_builtins(include_any_all=False):
    allowed = {
        'len': len,
        'str': str,
        'int': int,
        'float': float,
        'bool': bool,
        'list': list,
        'dict': dict,
        'tuple': tuple,
        'set': set,
        'sorted': sorted,
        'reversed': reversed,
        'enumerate': enumerate,
        'zip': zip,
        'map': map,
        'filter': filter,
        'range': range,
        'min': min,
        'max': max,
        'sum': sum,
        'abs': abs,
        'round': round,
        'isinstance': isinstance,
        'hasattr': hasattr,
        '__import__': __import__,
        'None': None,
        'True': True,
        'False': False,
    }
    if include_any_all:
        allowed['any'] = any
        allowed['all'] = all
    return allowed


def _build_safe_globals(profile):
    globals_map = {
        '__builtins__': _safe_builtins(include_any_all=(profile == 'etl')),
        'json': json,
        're': re,
        'math': math,
        'collections': collections,
        'datetime': datetime,
        'timedelta': timedelta,
        'pd': pd,
        'np': np,
    }
    if profile in ('export', 'menu'):
        globals_map.update({
            'csv': csv,
            'io': io,
            'ET': ET,
            'minidom': minidom,
        })
    return globals_map


def _validate_script(script_code):
    """Reject unsafe script patterns before execution."""
    if re.search(r'^\s*import\s+', script_code, re.MULTILINE):
        raise ValueError('import statements are not allowed')
    if re.search(r'^\s*from\s+\S+\s+import', script_code, re.MULTILINE):
        raise ValueError('from...import statements are not allowed')

    for name in FORBIDDEN_NAMES:
        if re.search(r'\b' + re.escape(name) + r'\s*\(', script_code):
            raise ValueError(f'{name}() is not allowed')

    if re.search(r'__\w+__', script_code):
        raise ValueError('double underscore (双下划线) attributes are not allowed')


def validate_export_script_scope(scope, script_code):
    """Ensure an export script's body matches its declared scope.

    Menu-scope scripts run via ``run_menu_export_script``, whose sandbox injects
    only ``menu_data`` / ``menu_name`` / ``total_records`` — NOT the page-level
    ``data`` / ``fields`` / ``page_name``. Saving page-style code under
    ``scope='menu'`` therefore blows up at export time with an opaque
    ``NameError: name 'fields' is not defined``. Catch the mismatch at save
    time with an actionable message. Page/row scopes are unconstrained (they
    share the same injected variables).
    """
    if scope == 'menu' and not re.search(r'\bmenu_data\b', script_code or ''):
        raise ValueError(
            '菜单级(scope=menu)导出脚本必须遍历 menu_data 变量；当前脚本未引用 menu_data，'
            '看起来是页面级写法（使用了 data/fields/page_name），运行时会报 '
            "NameError: name 'fields' is not defined。请改用菜单级脚手架（for table in menu_data: ...，"
            "用 table['fields']/table['records']），或将「导出维度」改为「整页(page)」。"
        )


def _thread_exec(script_code, safe_globals, script_locals, timeout_seconds, timeout_message):
    error_holder = [None]

    def _execute():
        try:
            exec(script_code, safe_globals, script_locals)  # noqa: S102
        except Exception as exc:  # pragma: no cover
            error_holder[0] = exc

    thread = threading.Thread(target=_execute)
    thread.start()
    thread.join(timeout=timeout_seconds)

    if thread.is_alive():
        raise TimeoutError(timeout_message)
    if error_holder[0]:
        raise error_holder[0]
    return script_locals


def _subprocess_exec_worker(queue, profile, script_code, script_locals):
    safe_globals = _build_safe_globals(profile)
    local_ctx = dict(script_locals)
    try:
        exec(script_code, safe_globals, local_ctx)  # noqa: S102
        if profile in ('export', 'menu'):
            queue.put({
                'ok': True,
                'result': local_ctx.get('result'),
                'filename': local_ctx.get('filename'),
                'content_type': local_ctx.get('content_type'),
            })
        elif profile == 'etl':
            queue.put({
                'ok': True,
                'result': local_ctx.get('result'),
            })
        else:
            queue.put({'ok': True})
    except Exception as exc:  # pragma: no cover
        queue.put({
            'ok': False,
            'error_type': exc.__class__.__name__,
            'error_message': str(exc),
        })


def _process_exec(profile, script_code, script_locals, timeout_seconds, timeout_message):
    try:
        queue = multiprocessing.Queue()
        proc = multiprocessing.Process(
            target=_subprocess_exec_worker,
            args=(queue, profile, script_code, script_locals),
            daemon=True,
        )
        proc.start()

        # Drain the result queue BEFORE joining. A child that put a large item
        # on a multiprocessing.Queue cannot terminate until its feeder thread
        # has flushed that item to the underlying pipe; if we join before
        # reading, the feeder blocks on a full pipe (results > the OS pipe
        # buffer) and the child never exits — so proc.join() stalls for the
        # entire timeout. (Python docs warn about exactly this ordering.)
        # queue.get(timeout=) both drains the result and enforces the timeout:
        # a runaway/hung script never puts a result, so get() raises Empty.
        try:
            payload = queue.get(timeout=timeout_seconds)
        except _queue.Empty:
            proc.terminate()
            proc.join(timeout=1)
            raise TimeoutError(timeout_message)

        # Result drained → the child can now exit promptly.
        proc.join(timeout=5)
        if proc.is_alive():
            proc.terminate()

        if not payload.get('ok'):
            raise RuntimeError(f"{payload.get('error_type', 'ScriptError')}: {payload.get('error_message', '')}")
        return payload
    except (PermissionError, OSError):
        # Some restricted environments disallow multiprocessing IPC handles.
        local_ctx = _thread_exec(
            script_code=script_code,
            safe_globals=_build_safe_globals(profile),
            script_locals=dict(script_locals),
            timeout_seconds=timeout_seconds,
            timeout_message=timeout_message,
        )
        if profile in ('export', 'menu'):
            return {
                'ok': True,
                'result': local_ctx.get('result'),
                'filename': local_ctx.get('filename'),
                'content_type': local_ctx.get('content_type'),
            }
        if profile == 'etl':
            return {
                'ok': True,
                'result': local_ctx.get('result'),
            }
        return {'ok': True}


def run_export_script(script_code, data, fields, page_name, output_format='json'):
    _validate_script(script_code)

    payload = _process_exec(
        profile='export',
        script_code=script_code,
        script_locals={
            'data': data,
            'fields': fields,
            'page_name': page_name,
            'result': None,
            'filename': None,
            'content_type': None,
        },
        timeout_seconds=SCRIPT_TIMEOUT,
        timeout_message=f'script execution timeout (>{SCRIPT_TIMEOUT}s)',
    )

    result = payload.get('result')
    if result is None:
        raise ValueError('result must be assigned in script')

    if isinstance(result, str):
        result_bytes = result.encode('utf-8')
    elif isinstance(result, bytes):
        result_bytes = result
    else:
        raise ValueError(f'result must be str 或 bytes, got {type(result).__name__}')

    filename = payload.get('filename') or f'{page_name}{FORMAT_EXTENSIONS.get(output_format, ".dat")}'
    content_type = payload.get('content_type') or FORMAT_CONTENT_TYPES.get(output_format, 'application/octet-stream')

    return result_bytes, filename, content_type


def run_validation_script(script_code, record, action, old_data, fields, collection, conn):
    _validate_script(script_code)

    errors = []
    warnings = []
    pending_relations = []

    def add_error(msg):
        errors.append(str(msg))

    def add_warning(msg):
        warnings.append(str(msg))

    def _row_to_record(row):
        rec = {'id': row[0]}
        if row[1]:
            rec.update(row[1])
        return rec

    def query(collection_name):
        cur = conn.cursor()
        cur.execute(
            'SELECT id, data FROM dynamic_data WHERE collection = %s ORDER BY created_at',
            (collection_name,),
        )
        return [_row_to_record(r) for r in cur.fetchall()]

    def query_one(collection_name, record_id):
        cur = conn.cursor()
        cur.execute(
            'SELECT id, data FROM dynamic_data WHERE collection = %s AND id = %s',
            (collection_name, record_id),
        )
        row = cur.fetchone()
        return _row_to_record(row) if row else None

    def find_by(collection_name, field_name, value):
        cur = conn.cursor()
        cur.execute(
            'SELECT id, data FROM dynamic_data WHERE collection = %s AND data->>%s = %s',
            (collection_name, field_name, str(value)),
        )
        return [_row_to_record(r) for r in cur.fetchall()]

    def get_relations(collection_name, record_id):
        cur = conn.cursor()
        cur.execute(
            'SELECT field_name, related_id FROM data_relations '
            'WHERE collection = %s AND record_id = %s ORDER BY field_name',
            (collection_name, record_id),
        )
        result = {}
        for field_name, related_id in cur.fetchall():
            result.setdefault(field_name, []).append(related_id)
        return result

    def set_relations(field_name, target_collection, target_field, ids):
        pending_relations.append({
            'fieldName': field_name,
            'targetCollection': target_collection,
            'targetField': target_field,
            'ids': list(ids) if not isinstance(ids, list) else ids,
        })

    _thread_exec(
        script_code=script_code,
        safe_globals=_build_safe_globals('validation'),
        script_locals={
            'record': record,
            'action': action,
            'old_data': old_data,
            'fields': fields,
            'collection': collection,
            'add_error': add_error,
            'add_warning': add_warning,
            'query': query,
            'query_one': query_one,
            'find_by': find_by,
            'get_relations': get_relations,
            'set_relations': set_relations,
        },
        timeout_seconds=SCRIPT_TIMEOUT,
        timeout_message=f'validation script timeout (>{SCRIPT_TIMEOUT}s)',
    )

    return errors, warnings, pending_relations


def run_etl_script(script_code, records):
    _validate_script(script_code)

    payload = _process_exec(
        profile='etl',
        script_code=script_code,
        script_locals={
            'records': records,
            'result': None,
        },
        timeout_seconds=SCRIPT_TIMEOUT,
        timeout_message=f'ETL script timeout (>{SCRIPT_TIMEOUT}s)',
    )

    result = payload.get('result')
    if result is None:
        raise ValueError('result must be assigned in script')
    return result


def run_menu_export_script(script_code, menu_data, menu_name, output_format='json'):
    _validate_script(script_code)

    total_records = sum(table.get('recordCount', 0) for table in menu_data)
    payload = _process_exec(
        profile='menu',
        script_code=script_code,
        script_locals={
            'menu_data': menu_data,
            'menu_name': menu_name,
            'total_records': total_records,
            'result': None,
            'filename': None,
            'content_type': None,
        },
        timeout_seconds=MENU_SCRIPT_TIMEOUT,
        timeout_message=f'menu export script timeout (>{MENU_SCRIPT_TIMEOUT}s)',
    )

    result = payload.get('result')
    if result is None:
        raise ValueError('result must be assigned in script')

    files = []

    if isinstance(result, list):
        for item in result:
            if not isinstance(item, dict):
                raise ValueError('each item in result list 必须是 dict')
            if 'filename' not in item or 'content' not in item:
                raise ValueError('each result list item must include filename 和 content')

            content = item['content']
            filename = item['filename']
            content_type = item.get('content_type') or guess_content_type(filename)

            if isinstance(content, str):
                content_bytes = content.encode('utf-8')
            elif isinstance(content, bytes):
                content_bytes = content
            else:
                raise ValueError(f'content must be str 或 bytes, got {type(content).__name__}')

            files.append((content_bytes, filename, content_type))

    elif isinstance(result, (str, bytes)):
        if isinstance(result, str):
            result_bytes = result.encode('utf-8')
        else:
            result_bytes = result

        filename = payload.get('filename') or f'{menu_name}{FORMAT_EXTENSIONS.get(output_format, ".dat")}'
        content_type = payload.get('content_type') or FORMAT_CONTENT_TYPES.get(output_format, 'application/octet-stream')
        files.append((result_bytes, filename, content_type))

    else:
        raise ValueError(f'result must be str、bytes 或 list, got {type(result).__name__}')

    return files


def guess_content_type(filename):
    ext = filename.rpartition('.')[2].lower() if '.' in filename else ''
    return FORMAT_CONTENT_TYPES.get(ext, 'application/octet-stream')
