# -*- coding: utf-8 -*-
"""提炼器 verifier 建议（设计 §10）。打桩 AI 设置与 HTTP 会话。"""
import json as _json
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _patch_ai(monkeypatch, content):
    import utils.action_check_extractor as ex
    monkeypatch.setattr(ex, 'get_ai_settings',
                        lambda: {'enabled': True, 'apiKey': 'k', 'endpoint': 'http://x',
                                 'model': 'm', 'timeout': 5, 'maxTokens': 1024})
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {'choices': [{'message': {'content': content}}]}
    monkeypatch.setattr(ex, 'get_http_session', lambda: MagicMock(post=lambda *a, **kw: resp))


def test_extract_suggests_verifier_check(monkeypatch):
    _patch_ai(monkeypatch, _json.dumps([
        {'name': '结论含标记', 'check_type': 'verifier', 'rubric': '回复必须包含 DONE-MARK'},
        {'name': '克隆仓库', 'check_type': 'tool', 'tool': 'bash',
         'args_pattern': 'git clone', 'min_count': 1},
    ], ensure_ascii=False))
    from utils.action_check_extractor import extract_action_checks
    checks = extract_action_checks('完成任务并汇报')
    v = [c for c in checks if c['check_type'] == 'verifier']
    assert len(v) == 1 and v[0]['effect_spec']['rubric'] == '回复必须包含 DONE-MARK'


def test_extract_verifier_without_rubric_dropped(monkeypatch):
    _patch_ai(monkeypatch, _json.dumps([{'name': '坏建议', 'check_type': 'verifier'}],
                                       ensure_ascii=False))
    from utils.action_check_extractor import extract_action_checks
    # validate_checks 拒绝（rubric 必填）→ ValueError 从 extract_action_checks
    # 直接冒泡（提炼器不吞坏建议，整体失败是既有行为；路由层把 ValueError/
    # RuntimeError 都转 502，见 routes/ai_chat_batches.py extract 路由）。
    # 已实测函数级异常类型是 ValueError，按实现为准断言。
    with pytest.raises(ValueError):
        extract_action_checks('x')
