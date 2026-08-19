"""
Unit tests for utils/ai_schema_designer.py.

Mirrors test_ai_query.py's mock pattern (patch get_ai_settings/get_http_session
at module level) since draft_page_schema() reuses the exact same HTTP-calling
shape as nl_to_mongo_filter(). Also covers _sanitize_draft()'s defensive
normalization of untrusted LLM output.
"""
import sys
import os
import json
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
import requests

import utils.ai_schema_designer as asd


SETTINGS = {
    'enabled': True,
    'apiKey': 'sk-test',
    'endpoint': 'https://example.test/v1/chat/completions',
    'model': 'qwen-plus',
    'timeout': 30,
    'maxTokens': 1024,
}

VALID_DRAFT = {
    'name': '订货表',
    'description': '记录客户订货信息',
    'collectionSlug': 'purchase-orders',
    'menuName': '订货表',
    'menuPath': '/purchase-orders',
    'fields': [
        {'fieldName': 'customer', 'label': '客户', 'controlType': 'text', 'required': True},
        {'fieldName': 'quantity', 'label': '数量', 'controlType': 'number', 'required': True},
        {'fieldName': 'status', 'label': '状态', 'controlType': 'select', 'required': True,
         'options': [{'label': '待处理', 'value': 'pending'}, {'label': '已发货', 'value': 'shipped'}]},
    ],
}


def _resp(status=200, content=None, is_json=True):
    r = MagicMock()
    r.status_code = status
    text = content if content is not None else json.dumps(VALID_DRAFT)
    r.text = text
    if is_json:
        r.json.return_value = {'choices': [{'message': {'content': text}}]}
    else:
        r.json.side_effect = ValueError('not json')
    return r


# ---------------------------------------------------------------------------
# _sanitize_draft
# ---------------------------------------------------------------------------

def test_sanitize_draft_happy_path_passthrough():
    out = asd._sanitize_draft(VALID_DRAFT)
    assert out['name'] == '订货表'
    assert out['collectionSlug'] == 'purchase-orders'
    assert out['menuPath'] == '/purchase-orders'
    assert len(out['fields']) == 3
    assert out['fields'][0]['fieldName'] == 'customer'


def test_sanitize_draft_downgrades_unsafe_control_type():
    raw = dict(VALID_DRAFT, fields=[
        {'fieldName': 'target', 'label': '目标', 'controlType': 'relation', 'required': False},
    ])
    out = asd._sanitize_draft(raw)
    assert out['fields'][0]['controlType'] == 'text'


def test_sanitize_draft_downgrades_select_missing_options_to_text():
    raw = dict(VALID_DRAFT, fields=[
        {'fieldName': 'status', 'label': '状态', 'controlType': 'select', 'required': True},
    ])
    out = asd._sanitize_draft(raw)
    assert out['fields'][0]['controlType'] == 'text'
    assert 'options' not in out['fields'][0]


def test_sanitize_draft_dedups_duplicate_field_names():
    raw = dict(VALID_DRAFT, fields=[
        {'fieldName': 'name', 'label': '名称一', 'controlType': 'text'},
        {'fieldName': 'name', 'label': '名称二', 'controlType': 'text'},
    ])
    out = asd._sanitize_draft(raw)
    names = [f['fieldName'] for f in out['fields']]
    assert len(names) == len(set(names))
    assert 'name' in names and 'name2' in names


def test_sanitize_draft_falls_back_illegal_field_name():
    raw = dict(VALID_DRAFT, fields=[
        {'fieldName': '客户名称', 'label': '客户名称', 'controlType': 'text'},
    ])
    out = asd._sanitize_draft(raw)
    assert asd._FIELD_NAME_RE.match(out['fields'][0]['fieldName'])


def test_sanitize_draft_falls_back_illegal_collection_slug():
    raw = dict(VALID_DRAFT, collectionSlug='订货表')
    out = asd._sanitize_draft(raw)
    assert asd._SLUG_RE.match(out['collectionSlug'])


def test_sanitize_draft_falls_back_illegal_menu_path():
    raw = dict(VALID_DRAFT, menuPath='/订货表')
    out = asd._sanitize_draft(raw)
    assert out['menuPath'].startswith('/')
    assert asd._SLUG_RE.match(out['menuPath'][1:])


def test_sanitize_draft_truncates_excess_fields():
    raw = dict(VALID_DRAFT, fields=[
        {'fieldName': f'f{i}', 'label': f'字段{i}', 'controlType': 'text'} for i in range(20)
    ])
    out = asd._sanitize_draft(raw)
    assert len(out['fields']) == asd._MAX_FIELDS


def test_sanitize_draft_raises_on_zero_fields():
    raw = dict(VALID_DRAFT, fields=[])
    with pytest.raises(RuntimeError, match='未返回任何可用字段'):
        asd._sanitize_draft(raw)


def test_sanitize_draft_raises_on_non_dict():
    with pytest.raises(RuntimeError):
        asd._sanitize_draft('not a dict')


def test_sanitize_draft_keeps_autosequence_config():
    raw = dict(VALID_DRAFT, fields=[
        {'fieldName': 'orderNo', 'label': '订单号', 'controlType': 'autoSequence',
         'required': True, 'sequenceConfig': {'prefix': 'PO-'}},
    ])
    out = asd._sanitize_draft(raw)
    assert out['fields'][0]['controlType'] == 'autoSequence'
    assert out['fields'][0]['sequenceConfig']['prefix'] == 'PO-'


# ---------------------------------------------------------------------------
# draft_page_schema (HTTP-calling shape, mirrors test_ai_query.py)
# ---------------------------------------------------------------------------

def test_draft_page_schema_happy_path():
    sess = MagicMock()
    sess.post.return_value = _resp()
    with patch.object(asd, 'get_ai_settings', return_value=SETTINGS), \
         patch.object(asd, 'get_http_session', return_value=sess):
        out = asd.draft_page_schema('我要创建一张订货表')
    assert out['collectionSlug'] == 'purchase-orders'
    args, kwargs = sess.post.call_args
    assert args[0] == SETTINGS['endpoint']
    assert kwargs['headers']['Authorization'] == 'Bearer sk-test'


def test_draft_page_schema_not_enabled_raises():
    with patch.object(asd, 'get_ai_settings', return_value=dict(SETTINGS, enabled=False)):
        with pytest.raises(RuntimeError, match='未启用'):
            asd.draft_page_schema('desc')


def test_draft_page_schema_missing_api_key_raises_before_http():
    sess = MagicMock()
    with patch.object(asd, 'get_ai_settings', return_value=dict(SETTINGS, apiKey='')), \
         patch.object(asd, 'get_http_session', return_value=sess):
        with pytest.raises(RuntimeError, match='API Key'):
            asd.draft_page_schema('desc')
    sess.post.assert_not_called()


def test_draft_page_schema_connection_error_maps_to_runtimeerror():
    sess = MagicMock()
    sess.post.side_effect = requests.ConnectionError('boom')
    with patch.object(asd, 'get_ai_settings', return_value=SETTINGS), \
         patch.object(asd, 'get_http_session', return_value=sess):
        with pytest.raises(RuntimeError, match='连接失败'):
            asd.draft_page_schema('desc')


def test_draft_page_schema_http_error_status_maps_to_runtimeerror():
    sess = MagicMock()
    sess.post.return_value = _resp(status=500, content='upstream down')
    with patch.object(asd, 'get_ai_settings', return_value=SETTINGS), \
         patch.object(asd, 'get_http_session', return_value=sess):
        with pytest.raises(RuntimeError, match=r'请求失败 \(500\)'):
            asd.draft_page_schema('desc')


def test_draft_page_schema_strips_markdown_fence():
    sess = MagicMock()
    fenced = '```json\n' + json.dumps(VALID_DRAFT) + '\n```'
    sess.post.return_value = _resp(content=fenced)
    with patch.object(asd, 'get_ai_settings', return_value=SETTINGS), \
         patch.object(asd, 'get_http_session', return_value=sess):
        out = asd.draft_page_schema('desc')
    assert out['collectionSlug'] == 'purchase-orders'


def test_draft_page_schema_unparseable_json_raises():
    sess = MagicMock()
    sess.post.return_value = _resp(content='not json at all')
    with patch.object(asd, 'get_ai_settings', return_value=SETTINGS), \
         patch.object(asd, 'get_http_session', return_value=sess):
        with pytest.raises(RuntimeError, match='无法解析为 JSON'):
            asd.draft_page_schema('desc')
