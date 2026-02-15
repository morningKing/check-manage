"""
导出脚本沙箱执行引擎

职责：
- 在受限环境中执行管理员编写的 Python 导出脚本
- 预注入常用模块和数据变量
- 限制危险操作（文件访问、系统调用等）
- 执行超时保护
"""

import json
import csv
import io
import re
import math
import collections
import xml.etree.ElementTree as ET
from xml.dom import minidom
from datetime import datetime, timedelta
import threading

# 默认 MIME 类型映射
FORMAT_CONTENT_TYPES = {
    'json': 'application/json',
    'xml': 'application/xml',
    'csv': 'text/csv',
    'txt': 'text/plain',
    'html': 'text/html',
}

# 默认文件扩展名
FORMAT_EXTENSIONS = {
    'json': '.json',
    'xml': '.xml',
    'csv': '.csv',
    'txt': '.txt',
    'html': '.html',
}

# 禁止的关键字
FORBIDDEN_NAMES = {
    'open', 'exec', 'eval', 'compile', '__import__', 'getattr', 'setattr',
    'delattr', 'globals', 'locals', 'vars', 'dir', 'type', 'super',
    'breakpoint', 'exit', 'quit', 'input', 'print',
}

# 脚本执行超时（秒）
SCRIPT_TIMEOUT = 10


def _validate_script(script_code):
    """检查脚本中是否包含危险操作"""
    # 禁止 import 语句
    if re.search(r'^\s*import\s+', script_code, re.MULTILINE):
        raise ValueError('脚本中不允许使用 import 语句，请使用预注入的模块')
    if re.search(r'^\s*from\s+\S+\s+import', script_code, re.MULTILINE):
        raise ValueError('脚本中不允许使用 from...import 语句，请使用预注入的模块')

    # 禁止危险内置函数
    for name in FORBIDDEN_NAMES:
        if re.search(r'\b' + name + r'\s*\(', script_code):
            raise ValueError(f'脚本中不允许使用 {name}()')

    # 禁止访问双下划线属性
    if re.search(r'__\w+__', script_code):
        raise ValueError('脚本中不允许访问双下划线属性')


def run_export_script(script_code, data, fields, page_name, output_format='json'):
    """
    执行导出脚本

    参数：
    - script_code: Python 代码字符串
    - data: 数据记录列表 list[dict]
    - fields: 字段配置列表 list[dict]
    - page_name: 页面名称
    - output_format: 输出格式标识

    返回：(文件内容 bytes, 文件名 str, content_type str)
    """
    # 1. 校验脚本安全性
    _validate_script(script_code)

    # 2. 构建安全的执行环境
    safe_globals = {
        # 预注入的模块
        'json': json,
        'csv': csv,
        'io': io,
        're': re,
        'math': math,
        'collections': collections,
        'ET': ET,
        'minidom': minidom,
        'datetime': datetime,
        'timedelta': timedelta,
        # 安全的内置函数
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
        'None': None,
        'True': True,
        'False': False,
    }

    # 3. 注入数据变量
    script_locals = {
        'data': data,
        'fields': fields,
        'page_name': page_name,
        'result': None,
        'filename': None,
        'content_type': None,
    }

    # 4. 带超时执行
    error_holder = [None]

    def _execute():
        try:
            exec(script_code, safe_globals, script_locals)  # noqa: S102
        except Exception as e:
            error_holder[0] = e

    thread = threading.Thread(target=_execute)
    thread.start()
    thread.join(timeout=SCRIPT_TIMEOUT)

    if thread.is_alive():
        raise TimeoutError(f'脚本执行超时（>{SCRIPT_TIMEOUT}秒）')

    if error_holder[0]:
        raise error_holder[0]

    # 5. 提取结果
    result = script_locals.get('result')
    if result is None:
        raise ValueError('脚本未设置 result 变量')

    # 转为 bytes
    if isinstance(result, str):
        result_bytes = result.encode('utf-8')
    elif isinstance(result, bytes):
        result_bytes = result
    else:
        raise ValueError(f'result 必须是 str 或 bytes，得到 {type(result).__name__}')

    filename = script_locals.get('filename') or f'{page_name}{FORMAT_EXTENSIONS.get(output_format, ".dat")}'
    content_type = script_locals.get('content_type') or FORMAT_CONTENT_TYPES.get(output_format, 'application/octet-stream')

    return result_bytes, filename, content_type
