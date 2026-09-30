"""Natural-language to page-config draft translator using LLM.

Given a one-line business description (e.g. "我要创建一张订货表"), asks the
LLM to draft a page name/description, a suggested collection slug + menu
route, and a field list — then defensively normalizes the result before
handing it back, since LLM output can't be trusted to be well-formed.

Reuses the same LLM-calling machinery as ``utils/ai_query.py`` (AI 智能查询:
natural language → MongoDB filter) rather than standing up a second HTTP
client: same ``ai_settings`` config, same pooled ``requests.Session``, same
"system prompt + strict JSON + strip markdown fences + json.loads" shape.
"""

import json
import re

import requests

from utils.ai_query import get_ai_settings, get_http_session

# Control types safe for an AI-drafted first cut: no relation/reference/
# quoteSelect (need an existing target collection the LLM can't know about),
# no statusBadge/compositeText/workflowConfig (too config-heavy for a draft).
SAFE_CONTROL_TYPES = {
    'text', 'textarea', 'number', 'select', 'multiSelect', 'radio', 'checkbox',
    'date', 'datetime', 'richText', 'markdown', 'autoTimestamp', 'autoSequence',
    'file', 'image',
}
_OPTIONS_TYPES = {'select', 'multiSelect', 'radio', 'checkbox'}
_MAX_FIELDS = 12
_MIN_FIELDS = 1

_SLUG_RE = re.compile(r'^[a-z0-9]+(-[a-z0-9]+)*$')
_FIELD_NAME_RE = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

def _build_system_prompt():
    return """你是一个数据建模助手。用户会用一句自然语言描述一个业务实体（如"我要创建一张订货表"），
你需要输出一份可直接用于建表的 JSON 草案，不要输出任何解释文字，只输出合法 JSON。

## 输出形状
{
  "name": "订货表",
  "description": "记录客户订货信息",
  "collectionSlug": "purchase-orders",
  "menuName": "订货表",
  "menuPath": "/purchase-orders",
  "fields": [
    {"fieldName": "orderNo", "label": "订单号", "controlType": "autoSequence",
     "required": true, "sequenceConfig": {"prefix": "PO-", "max": 9999}},
    {"fieldName": "customer", "label": "客户", "controlType": "text", "required": true},
    {"fieldName": "quantity", "label": "数量", "controlType": "number", "required": true},
    {"fieldName": "orderDate", "label": "下单日期", "controlType": "date", "required": true},
    {"fieldName": "status", "label": "状态", "controlType": "select", "required": true,
     "options": [{"label": "待处理", "value": "pending"}, {"label": "已发货", "value": "shipped"}]}
  ]
}

## 规则
1. controlType 只能是以下之一：text, textarea, number, select, multiSelect, radio,
   checkbox, date, datetime, richText, markdown, autoTimestamp, autoSequence, file, image。
   不要使用 relation / reference / quoteSelect / statusBadge / compositeText，这些需要
   选择系统里已存在的其它数据集合或复杂配置，你并不知道系统里有哪些集合。
2. select / multiSelect / radio / checkbox 类型必须带 options 数组，每项含
   label（中文显示）和 value（英文短标识，如 pending/shipped）。
3. fieldName 用英文 camelCase（如 orderNo、customerName），label 用中文，
   fieldName 之间互不相同。
4. 字段数量控制在 4 到 12 个之间，覆盖这个业务实体最核心的信息。
5. collectionSlug 和 menuPath（去掉开头的 /）必须是英文 kebab-case（小写字母、
   数字、连字符），不要包含中文或空格；menuPath 以 / 开头。
6. name/menuName 用中文，与用户描述的实体名一致。
7. 只输出 JSON 本身，不要 markdown 代码块围栏之外的任何文字。"""


# ---------------------------------------------------------------------------
# Defensive normalization — LLM output is untrusted input
# ---------------------------------------------------------------------------

def _slugify_fallback(text: str, index: int) -> str:
    """Best-effort ASCII slug from arbitrary text; falls back to a numbered
    placeholder when nothing usable survives (e.g. pure-Chinese input) —
    good enough since the frontend preview lets the admin edit it anyway."""
    ascii_only = re.sub(r'[^a-zA-Z0-9]+', '-', text or '').strip('-').lower()
    return ascii_only if ascii_only else f'table-{index}'


def _sanitize_field(raw: dict, index: int, used_names: set) -> dict | None:
    if not isinstance(raw, dict):
        return None
    label = str(raw.get('label') or '').strip() or f'字段{index}'
    field_name = str(raw.get('fieldName') or '').strip()
    if not _FIELD_NAME_RE.match(field_name):
        field_name = _slugify_fallback(field_name, index).replace('-', '_') or f'field{index}'
    base_name = field_name
    n = 1
    while field_name in used_names:
        n += 1
        field_name = f'{base_name}{n}'
    used_names.add(field_name)

    control_type = raw.get('controlType')
    if control_type not in SAFE_CONTROL_TYPES:
        control_type = 'text'

    field = {
        'fieldName': field_name,
        'label': label,
        'controlType': control_type,
        'required': bool(raw.get('required')),
    }

    if control_type in _OPTIONS_TYPES:
        options = raw.get('options')
        clean_options = []
        if isinstance(options, list):
            for o in options:
                if isinstance(o, dict) and o.get('label') and o.get('value') is not None:
                    clean_options.append({'label': str(o['label']), 'value': str(o['value'])})
        if not clean_options:
            # No usable options → a select with no choices is worse than a
            # plain text field; downgrade rather than ship something unusable.
            field['controlType'] = 'text'
        else:
            field['options'] = clean_options

    if control_type == 'autoSequence':
        seq = raw.get('sequenceConfig')
        prefix = seq.get('prefix') if isinstance(seq, dict) else ''
        field['sequenceConfig'] = {
            'prefix': str(prefix or '')[:10],
            'max': 9999,
        }

    return field


def _sanitize_draft(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise RuntimeError('AI 返回的草案不是合法的 JSON 对象')

    name = str(raw.get('name') or '').strip() or '新数据页'
    description = str(raw.get('description') or '').strip()

    collection_slug = str(raw.get('collectionSlug') or '').strip().lower()
    if not _SLUG_RE.match(collection_slug):
        collection_slug = _slugify_fallback(name, 1) or 'table-1'

    menu_name = str(raw.get('menuName') or '').strip() or name
    menu_path = str(raw.get('menuPath') or '').strip()
    if not menu_path.startswith('/'):
        menu_path = '/' + menu_path
    path_slug = menu_path[1:].lower()
    if not _SLUG_RE.match(path_slug):
        menu_path = '/' + collection_slug

    raw_fields = raw.get('fields')
    if not isinstance(raw_fields, list) or len(raw_fields) < _MIN_FIELDS:
        raise RuntimeError('AI 未返回任何可用字段，请换一种描述再试一次')

    used_names: set = set()
    fields = []
    for i, f in enumerate(raw_fields[:_MAX_FIELDS], start=1):
        sanitized = _sanitize_field(f, i, used_names)
        if sanitized:
            fields.append(sanitized)

    if not fields:
        raise RuntimeError('AI 未返回任何可用字段，请换一种描述再试一次')

    return {
        'name': name,
        'description': description,
        'collectionSlug': collection_slug,
        'menuName': menu_name,
        'menuPath': menu_path,
        'fields': fields,
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def draft_page_schema(description: str) -> dict:
    """Translate a natural-language business description into a page-config
    draft (name/description/collectionSlug/menuName/menuPath/fields).

    Raises
    ------
    RuntimeError
        AI not enabled / no API key / network failure / unparseable response
        / no usable fields in the response.
    """
    cfg = get_ai_settings()

    if not cfg['enabled']:
        raise RuntimeError('AI 建表功能未启用，请在系统配置中开启')

    api_key = cfg['apiKey']
    if not api_key:
        raise RuntimeError('AI 服务未配置 API Key')

    payload = json.dumps({
        'model': cfg['model'],
        'messages': [
            {'role': 'system', 'content': _build_system_prompt()},
            {'role': 'user', 'content': description},
        ],
        'temperature': 0.2,
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

    try:
        content = body['choices'][0]['message']['content']
    except (KeyError, IndexError):
        raise RuntimeError('AI 服务返回格式异常')

    text = content.strip()
    if text.startswith('```'):
        text = text.split('\n', 1)[-1] if '\n' in text else text[3:]
        if text.endswith('```'):
            text = text[:-3]
        text = text.strip()

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        raise RuntimeError(f'AI 返回内容无法解析为 JSON: {text[:200]}')

    return _sanitize_draft(result)
