"""Natural-language to MongoDB-style filter translator using LLM.

Builds a prompt with field schema, calls the OpenAI-compatible API,
and returns a parsed filter dict suitable for ``mongo_query.translate()``.

AI configuration is read from the ``ai_settings`` database table.
"""

import json
import threading
from datetime import datetime, timezone

import requests
from requests.adapters import HTTPAdapter

try:  # urllib3 ships with requests; guard import path differences
    from urllib3.util.retry import Retry
except Exception:  # pragma: no cover - very old urllib3
    Retry = None

from db import get_db

# Field types that cannot be meaningfully queried via natural language
_SKIP_TYPES = {'relation', 'reference', 'quoteSelect', 'file', 'image', 'richText'}

# ---------------------------------------------------------------------------
# Shared HTTP session — reuses TCP/TLS connections to the LLM endpoint across
# requests (connection pooling) instead of opening a fresh socket every call.
# A single module-level Session is safe to share across threads.
# ---------------------------------------------------------------------------
_session = None
_session_lock = threading.Lock()


def get_http_session():
    global _session
    if _session is None:
        with _session_lock:
            if _session is None:
                s = requests.Session()
                if Retry is not None:
                    retry = Retry(
                        total=2,
                        backoff_factor=0.3,
                        status_forcelist=(429, 500, 502, 503, 504),
                        allowed_methods=frozenset(['POST']),
                    )
                    adapter = HTTPAdapter(pool_connections=4, pool_maxsize=10, max_retries=retry)
                else:
                    adapter = HTTPAdapter(pool_connections=4, pool_maxsize=10)
                s.mount('https://', adapter)
                s.mount('http://', adapter)
                _session = s
    return _session


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------

def get_ai_settings():
    """Read AI settings from database, return camelCase dict."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT enabled, api_key, endpoint, model, timeout, max_tokens, updated_at, '
            'mem0_enabled, embedding_model FROM ai_settings WHERE id = 1'
        )
        row = cur.fetchone()

    if not row:
        return {
            'enabled': False,
            'apiKey': '',
            'endpoint': 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions',
            'model': 'qwen-plus',
            'timeout': 30,
            'maxTokens': 1024,
            'updatedAt': None,
            'mem0Enabled': False,
            'embeddingModel': 'text-embedding-v3',
        }

    return {
        'enabled': row[0],
        'apiKey': row[1] or '',
        'endpoint': row[2],
        'model': row[3],
        'timeout': row[4],
        'maxTokens': row[5],
        'updatedAt': row[6].isoformat() if row[6] else None,
        'mem0Enabled': bool(row[7]) if len(row) > 7 else False,
        'embeddingModel': (row[8] if len(row) > 8 else None) or 'text-embedding-v3',
    }


def update_ai_settings(enabled, api_key, endpoint, model, timeout, max_tokens,
                       mem0_enabled=False, embedding_model='text-embedding-v3'):
    """Persist AI settings and return updated dict."""
    now = datetime.now(timezone.utc)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'UPDATE ai_settings SET enabled = %s, api_key = %s, endpoint = %s, '
            'model = %s, timeout = %s, max_tokens = %s, mem0_enabled = %s, '
            'embedding_model = %s, updated_at = %s WHERE id = 1',
            (enabled, api_key, endpoint, model, timeout, max_tokens,
             mem0_enabled, embedding_model, now),
        )
    return get_ai_settings()


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

def _build_field_schema(fields):
    """Build a compact field-schema string for the system prompt."""
    lines = []
    for f in fields:
        ct = f.get('controlType', 'text')
        if ct in _SKIP_TYPES:
            continue
        name = f.get('fieldName', '')
        label = f.get('label', name)
        desc = f'{name} ({label}, {ct})'
        # Include select options so LLM knows valid values
        if ct == 'select':
            opts = f.get('options') or []
            if opts:
                pairs = [f"{o.get('value','')}={o.get('label','')}" for o in opts]
                desc += ' options: [' + ', '.join(pairs) + ']'
        lines.append(desc)
    return '\n'.join(lines)


def _build_system_prompt(field_schema, collection_name):
    return f"""你是一个查询翻译器。用户会用自然语言描述数据筛选条件，你需要输出 MongoDB 风格的 JSON filter。

## 数据集合: {collection_name}

## 字段 schema（fieldName, label, controlType）:
{field_schema}

## 操作符速查:
- 精确匹配: {{"fieldName": "value"}}
- 模糊匹配: {{"fieldName": {{"$regex": "关键词"}}}}
- 不等于: {{"fieldName": {{"$ne": "value"}}}}
- 大于/小于: $gt, $gte, $lt, $lte
- 在列表中: {{"fieldName": {{"$in": ["a","b"]}}}}
- 不在列表中: {{"fieldName": {{"$nin": ["a"]}}}}
- 逻辑或: {{"$or": [{{...}}, {{...}}]}}
- 逻辑与: {{"$and": [{{...}}, {{...}}]}}

## 规则:
1. 只输出合法 JSON，不要任何解释文字
2. 字段名必须用 fieldName（英文），不要用中文标签
3. select 类型字段的值必须用 option 的 value，不要用 label
4. 如果用户描述模糊，优先用 $regex 模糊匹配
5. 如果无法理解用户意图，输出 {{}}"""


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def nl_to_mongo_filter(question, fields, collection_name):
    """Translate a natural-language question into a MongoDB-style filter dict.

    Parameters
    ----------
    question : str
        User's natural-language query, e.g. "找出所有待评审的用例".
    fields : list[dict]
        The ``fields`` array from ``page_configs``.
    collection_name : str
        The collection (pageId without ``page-`` prefix).

    Returns
    -------
    dict
        A filter dict consumable by ``mongo_query.translate()``.

    Raises
    ------
    RuntimeError
        On API call failure or unparseable response.
    """
    cfg = get_ai_settings()

    if not cfg['enabled']:
        raise RuntimeError('AI 智能查询功能未启用，请在系统配置中开启')

    api_key = cfg['apiKey']
    if not api_key:
        raise RuntimeError('AI 服务未配置 API Key')

    field_schema = _build_field_schema(fields)
    system_prompt = _build_system_prompt(field_schema, collection_name)

    payload = json.dumps({
        'model': cfg['model'],
        'messages': [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': question},
        ],
        'temperature': 0.1,
        'max_tokens': cfg['maxTokens'],
    }).encode('utf-8')

    headers = {
        'Content-Type': 'application/json',
        'Authorization': f"Bearer {api_key}",
    }

    try:
        resp = get_http_session().post(
            cfg['endpoint'], data=payload, headers=headers, timeout=cfg['timeout']
        )
    except requests.RequestException as e:
        raise RuntimeError(f'AI 服务连接失败: {e}')

    if resp.status_code >= 400:
        raise RuntimeError(f'AI 服务请求失败 ({resp.status_code}): {resp.text}')

    try:
        body = resp.json()
    except ValueError:
        raise RuntimeError('AI 服务返回格式异常')

    # Extract assistant content
    try:
        content = body['choices'][0]['message']['content']
    except (KeyError, IndexError):
        raise RuntimeError('AI 服务返回格式异常')

    # Strip markdown code fences if present
    text = content.strip()
    if text.startswith('```'):
        # Remove opening fence (with optional language tag)
        text = text.split('\n', 1)[-1] if '\n' in text else text[3:]
        if text.endswith('```'):
            text = text[:-3]
        text = text.strip()

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        raise RuntimeError(f'AI 返回内容无法解析为 JSON: {text[:200]}')

    if not isinstance(result, dict):
        raise RuntimeError('AI 返回的 filter 必须是 JSON 对象')

    return result
