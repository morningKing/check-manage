# -*- coding: utf-8 -*-
"""自然语言 → 规则 转换的纯函数与路由测试（AI 调用一律 mock，不依赖 opencode）。"""
import sys, os
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest

from app import app
from auth import create_token
from db import get_db
from utils.suggest_editor_rule_ai import (
    RuleConversionError, RuleParseError, _convert_model, normalize_rules, parse_rule_json,
)


# ------------------------------- 解析 -------------------------------


def test_parse_plain_json():
    rules = parse_rule_json('{"rules": [{"id": "r1", "name": "x", "maps": [{"wrong": "a", "right": "b"}]}]}')
    assert rules[0]['id'] == 'r1'


def test_parse_fenced_and_wrapped_text():
    text = '好的，规则如下：\n```json\n{"rules": [{"id": "r1", "name": "x"}]}\n```\n以上。'
    rules = parse_rule_json(text)
    assert rules == [{'id': 'r1', 'name': 'x'}]


def test_parse_rejects_empty_or_non_json():
    with pytest.raises(RuleParseError):
        parse_rule_json('')
    with pytest.raises(RuleParseError):
        parse_rule_json('没有 JSON')
    with pytest.raises(RuleParseError):
        parse_rule_json('{"rules": [')  # 不完整
    with pytest.raises(RuleParseError):
        parse_rule_json('{"foo": 1}')  # 结构不对


# ------------------------------- 归一化 -------------------------------


def test_normalize_fills_id_name_severity_and_dedupes():
    rules = normalize_rules([
        {'name': 'A', 'maps': [{'wrong': 'x', 'right': 'y'}]},
        {'id': 'dup', 'name': 'B', 'severity': 'fatal', 'maps': [{'wrong': 'p', 'right': 'q'}]},
        {'id': 'dup', 'name': 'C', 'aiPrompt': '检查语气'},
    ], '描述')
    ids = [r['id'] for r in rules]
    assert ids[0].startswith('rule-') and len(ids) == len(set(ids))
    assert all(r['severity'] in ('info', 'warn', 'error') for r in rules)
    assert rules[1]['severity'] == 'warn'  # 非法值回退
    assert rules[2]['id'] != 'dup'


def test_normalize_rejects_empty_and_invalid_rules():
    with pytest.raises(RuleParseError):
        normalize_rules([], '描述')
    with pytest.raises(RuleParseError) as ei:
        normalize_rules([{'name': '坏正则', 'patterns': [{'extract': '(', 'valid': 'x', 'message': 'm'}]}], '描述')
    assert '未通过校验' in str(ei.value)


def test_convert_model_prefers_agent_model(monkeypatch):
    monkeypatch.setenv('AGENT_MODEL', 'opencode-go/deepseek-v4.1-flash')
    assert _convert_model() == 'opencode-go/deepseek-v4.1-flash'
    monkeypatch.delenv('AGENT_MODEL', raising=False)
    with patch('utils.suggest_editor_rule_ai.get_default_chat_model', return_value='platform/model'):
        assert _convert_model() == 'platform/model'


# --------------------------- 上游失败的可诊断性 ---------------------------


def test_convert_reports_unreachable_serve(monkeypatch):
    """serve 没起来：立即报“不可达”并带 proxy 排查提示，不再空等 90 秒。"""
    import utils.suggest_editor_rule_ai as ra
    monkeypatch.setenv('AGENT_MODEL', 'test/model')

    class Unreachable:
        def __init__(self, *args, **kwargs):
            pass

        def list_providers(self):
            raise RuntimeError('connection refused')

    monkeypatch.setattr(ra, 'OpenCodeClient', Unreachable)
    with pytest.raises(RuleConversionError) as ei:
        ra.convert_nl_to_rules('型号 a 要写成 b')
    text = str(ei.value)
    assert 'opencode serve 不可达' in text
    assert 'proxy.py' in text and '[3/5]' in text


class _FakeClient:
    """只实现转换用到的方法；get_messages 由各用例覆盖。"""
    created = 0
    deleted = 0

    def __init__(self, *args, **kwargs):
        pass

    def create_session(self, **kwargs):
        _FakeClient.created += 1
        return 'ses_test'

    def send_prompt_async(self, *args, **kwargs):
        return None

    def get_messages(self, *args, **kwargs):
        return []

    def delete_session(self, *args, **kwargs):
        _FakeClient.deleted += 1


def test_convert_fails_fast_on_model_error(monkeypatch):
    """模型侧错误（鉴权/额度/模型不存在）立即 502，并带出上游错误名与说明。"""
    import utils.suggest_editor_rule_ai as ra
    monkeypatch.setenv('AGENT_MODEL', 'test/model')

    class ModelErrorClient(_FakeClient):
        def get_messages(self, *args, **kwargs):
            return [{
                'info': {
                    'role': 'assistant',
                    'error': {'name': 'ProviderAuthError', 'data': {'message': 'invalid api key'}},
                },
                'parts': [],
            }]

    monkeypatch.setattr(ra, 'OpenCodeClient', ModelErrorClient)
    monkeypatch.setattr(ra, '_ensure_serve_reachable', lambda: None)
    monkeypatch.setattr(ra.time, 'sleep', lambda _seconds: None)
    with pytest.raises(RuleConversionError) as ei:
        ra.convert_nl_to_rules('型号 a 要写成 b')
    text = str(ei.value)
    assert '模型调用失败' in text and 'invalid api key' in text
    assert 'opencode serve=' in text


def test_convert_timeout_message_has_diagnostics(monkeypatch):
    """真超时：错误里带 serve 地址/模型/轮询失败次数与重试提示。"""
    import utils.suggest_editor_rule_ai as ra
    monkeypatch.setenv('AGENT_MODEL', 'test/model')

    class SilentClient(_FakeClient):
        def get_messages(self, *args, **kwargs):
            return []

    clock = {'now': 1000.0}
    monkeypatch.setattr(ra, 'OpenCodeClient', SilentClient)
    monkeypatch.setattr(ra, '_ensure_serve_reachable', lambda: None)
    monkeypatch.setattr(ra.time, 'time', lambda: clock['now'])
    monkeypatch.setattr(ra.time, 'sleep',
                        lambda seconds: clock.__setitem__('now', clock['now'] + seconds))

    with pytest.raises(RuleConversionError) as ei:
        ra.convert_nl_to_rules('型号 a 要写成 b')
    text = str(ei.value)
    assert 'AI 生成超时' in text
    assert 'opencode serve=' in text and '重试' in text
    assert _FakeClient.deleted >= 1


# ------------------------------- 路由 -------------------------------


@pytest.fixture()
def deliver_env():
    suffix = os.urandom(4).hex()
    admin_id, dev_id = f'ai-rule-admin-{suffix}', f'ai-rule-dev-{suffix}'
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                        "VALUES (%s,%s,'x',%s,'admin')", (admin_id, 'aira-' + suffix, admin_id))
            cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                        "VALUES (%s,%s,'x',%s,'developer')", (dev_id, 'aird-' + suffix, dev_id))
        conn.commit()
    client = app.test_client()
    app.config['TESTING'] = True
    yield {
        'client': client,
        'admin': {'Authorization': f"Bearer {create_token({'id': admin_id, 'username': 'a', 'role': 'admin'})}"},
        'dev': {'Authorization': f"Bearer {create_token({'id': dev_id, 'username': 'd', 'role': 'developer'})}"},
        'admin_id': admin_id, 'dev_id': dev_id,
    }
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE id IN (%s,%s)", (admin_id, dev_id))
        conn.commit()


def test_convert_requires_admin(deliver_env):
    client, dev = deliver_env['client'], deliver_env['dev']
    assert client.post('/suggest-editor/rules/convert', headers=dev,
                       json={'description': 'x'}).status_code == 403


def test_convert_validates_description(deliver_env):
    client, admin = deliver_env['client'], deliver_env['admin']
    assert client.post('/suggest-editor/rules/convert', headers=admin, json={}).status_code == 400
    assert client.post('/suggest-editor/rules/convert', headers=admin,
                       json={'description': 'x' * 2001}).status_code == 400


def test_convert_success_returns_rules(deliver_env):
    client, admin = deliver_env['client'], deliver_env['admin']
    fake = [{'id': 'rule-x', 'name': 'x', 'enabled': True, 'severity': 'warn',
             'maps': [{'wrong': 'a', 'right': 'b'}]}]
    with patch('utils.suggest_editor_rule_ai.convert_nl_to_rules', return_value=fake) as m:
        r = client.post('/suggest-editor/rules/convert', headers=admin,
                        json={'description': '型号 a 要写成 b'})
    assert r.status_code == 200 and r.get_json()['rules'] == fake
    m.assert_called_once_with('型号 a 要写成 b')


def test_convert_maps_errors_to_422_and_502(deliver_env):
    client, admin = deliver_env['client'], deliver_env['admin']
    with patch('utils.suggest_editor_rule_ai.convert_nl_to_rules',
               side_effect=RuleParseError('AI 返回的 JSON 解析失败')):
        r = client.post('/suggest-editor/rules/convert', headers=admin, json={'description': 'x'})
    assert r.status_code == 422 and 'JSON' in r.get_json()['error']
    with patch('utils.suggest_editor_rule_ai.convert_nl_to_rules',
               side_effect=RuleConversionError('生成超时')):
        r = client.post('/suggest-editor/rules/convert', headers=admin, json={'description': 'x'})
    assert r.status_code == 502 and '超时' in r.get_json()['error']
