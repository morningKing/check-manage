"""
Unit tests for the LLM call in utils/ai_query.py.

Covers the connection-pool refactor (A): a shared requests.Session is reused
across calls, and HTTP/connection errors map to the same RuntimeError contract
the route relies on (see routes/ai.py: 'API Key' → 503, else 500/422).
"""
import sys
import os
import json
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import requests
import utils.ai_query as aq


FIELDS = [{'fieldName': 'status', 'label': '状态', 'controlType': 'text'}]
SETTINGS = {
    'enabled': True,
    'apiKey': 'sk-test',
    'endpoint': 'https://example.test/v1/chat/completions',
    'model': 'qwen-plus',
    'timeout': 30,
    'maxTokens': 1024,
}


def _resp(status=200, content='{"status": "done"}', is_json=True):
    r = MagicMock()
    r.status_code = status
    r.text = content
    if is_json:
        r.json.return_value = {'choices': [{'message': {'content': content}}]}
    else:
        r.json.side_effect = ValueError('not json')
    return r


def test_get_http_session_is_a_pooled_singleton():
    aq._session = None  # reset module singleton
    s1 = aq.get_http_session()
    s2 = aq.get_http_session()
    assert s1 is s2                      # reused across calls (pooling)
    assert isinstance(s1, requests.Session)
    # both schemes mounted with a pooling adapter
    assert s1.get_adapter('https://x') is s1.get_adapter('https://y')


def test_happy_path_parses_filter():
    sess = MagicMock()
    sess.post.return_value = _resp(content='{"status": "done"}')
    with patch.object(aq, 'get_ai_settings', return_value=SETTINGS), \
         patch.object(aq, 'get_http_session', return_value=sess):
        out = aq.nl_to_mongo_filter('已完成的', FIELDS, 'orders')
    assert out == {'status': 'done'}
    # called the configured endpoint via the shared session
    args, kwargs = sess.post.call_args
    assert args[0] == SETTINGS['endpoint']
    assert kwargs['timeout'] == 30
    assert kwargs['headers']['Authorization'] == 'Bearer sk-test'


def test_connection_error_maps_to_runtimeerror():
    sess = MagicMock()
    sess.post.side_effect = requests.ConnectionError('boom')
    with patch.object(aq, 'get_ai_settings', return_value=SETTINGS), \
         patch.object(aq, 'get_http_session', return_value=sess):
        with pytest.raises(RuntimeError, match='连接失败'):
            aq.nl_to_mongo_filter('q', FIELDS, 'orders')


def test_http_error_status_maps_to_runtimeerror():
    sess = MagicMock()
    sess.post.return_value = _resp(status=500, content='upstream down')
    with patch.object(aq, 'get_ai_settings', return_value=SETTINGS), \
         patch.object(aq, 'get_http_session', return_value=sess):
        with pytest.raises(RuntimeError, match=r'请求失败 \(500\)'):
            aq.nl_to_mongo_filter('q', FIELDS, 'orders')


def test_missing_api_key_raises_before_any_http():
    sess = MagicMock()
    cfg = dict(SETTINGS, apiKey='')
    with patch.object(aq, 'get_ai_settings', return_value=cfg), \
         patch.object(aq, 'get_http_session', return_value=sess):
        with pytest.raises(RuntimeError, match='API Key'):
            aq.nl_to_mongo_filter('q', FIELDS, 'orders')
    sess.post.assert_not_called()


def test_strips_markdown_fence_in_response():
    sess = MagicMock()
    fenced = '```json\n{"status": "done"}\n```'
    sess.post.return_value = _resp(content=fenced)
    with patch.object(aq, 'get_ai_settings', return_value=SETTINGS), \
         patch.object(aq, 'get_http_session', return_value=sess):
        out = aq.nl_to_mongo_filter('q', FIELDS, 'orders')
    assert out == {'status': 'done'}
