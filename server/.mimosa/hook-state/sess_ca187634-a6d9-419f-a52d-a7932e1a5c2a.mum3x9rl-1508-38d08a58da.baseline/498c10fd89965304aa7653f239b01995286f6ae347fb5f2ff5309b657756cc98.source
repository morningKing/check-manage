"""可配置的批任务子会话个数上限（ai_settings.max_batch_sessions）。

覆盖：reader 的回落/钳制、create/append 仓储层校验、UI 路由前置校验与列表
下发 maxSessions、AI 设置接口的读写与校验。
"""

import sys
import os
from unittest.mock import MagicMock, patch
from contextlib import contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest

from utils import batch_repo


def _db(row):
    """单行查询的假 get_db（row=None 表示空表）。"""
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchone.return_value = row
    conn.cursor.return_value = cur

    @contextmanager
    def fake():
        yield conn

    return fake


# ---------------------------------------------------------------------------
# reader：get_max_files_per_batch
# ---------------------------------------------------------------------------

def test_reader_returns_configured_value():
    with patch.object(batch_repo, 'get_db', _db((100,))):
        assert batch_repo.get_max_files_per_batch() == 100


def test_reader_falls_back_when_row_missing_or_null():
    with patch.object(batch_repo, 'get_db', _db(None)):
        assert batch_repo.get_max_files_per_batch() == 50
    with patch.object(batch_repo, 'get_db', _db((None,))):
        assert batch_repo.get_max_files_per_batch() == 50


def test_reader_falls_back_on_invalid_value():
    # 非整数字符串无法解析 → 回落默认
    with patch.object(batch_repo, 'get_db', _db(('abc',))):
        assert batch_repo.get_max_files_per_batch() == 50


def test_reader_clamps_non_positive_to_minimum():
    # 0/负数语义上等于「至少 1 个」，钳到最小值而不是回落默认
    for bad in (0, -5):
        with patch.object(batch_repo, 'get_db', _db((bad,))):
            assert batch_repo.get_max_files_per_batch() == 1, bad


def test_reader_clamps_to_minimum_one():
    with patch.object(batch_repo, 'get_db', _db((1,))):
        assert batch_repo.get_max_files_per_batch() == 1


def test_reader_falls_back_on_db_error():
    @contextmanager
    def boom():
        raise RuntimeError('db down')
        yield  # pragma: no cover

    with patch.object(batch_repo, 'get_db', boom):
        assert batch_repo.get_max_files_per_batch() == 50


# ---------------------------------------------------------------------------
# repo 层：create / append
# ---------------------------------------------------------------------------

def test_create_respects_configured_limit():
    ok_file = {'name': 'a.txt', 'path': 'batch-staging/u/abc/a.txt'}
    with patch.object(batch_repo, 'get_max_files_per_batch', return_value=3), \
         patch.object(batch_repo, 'get_db', side_effect=AssertionError('超限应在写库前拒绝')):
        try:
            batch_repo.create_batch('u1', name='n', prompt='p',
                                    template_id=None, files=[ok_file] * 4)
            raise AssertionError('4 个文件应被拒绝')
        except ValueError as e:
            assert 'max 3 files per batch' in str(e)


def test_append_respects_configured_total():
    """追加后总数超上限拒绝；上限调大后同批次可继续追加。"""
    import unittest.mock as mock

    files = [{'name': f'f{i}.txt', 'path': f'batch-staging/u/abc/f{i}.txt'}
             for i in range(2)]

    def fake_db():
        # fetchone 次序：total 行 → MAX(batch_seq) → 每个文件 INSERT..RETURNING 一行
        cursor = MagicMock()
        state = {'fetches': 0}

        def fetchone():
            state['fetches'] += 1
            if state['fetches'] == 1:
                return {'total': 2}
            if state['fetches'] == 2:
                return {'m': 1}
            return {'id': f's{state["fetches"]}'}

        cursor.fetchone.side_effect = fetchone
        # 代码里是 `with conn.cursor(...) as cur` —— 上下文管理器要返回自身
        cursor.__enter__.return_value = cursor
        conn = MagicMock()
        conn.cursor.return_value = cursor

        @contextmanager
        def fake():
            yield conn

        return fake

    def attempt(limit):
        with mock.patch.object(batch_repo, 'get_max_files_per_batch', return_value=limit), \
             mock.patch.object(batch_repo, 'get_db', fake_db()), \
             mock.patch.object(batch_repo, '_recompute_batch_status_for'), \
             mock.patch.object(batch_repo, 'get_batch_detail',
                               return_value={'batch': {}, 'sessions': []}):
            return batch_repo.append_to_batch('u1', 'b-1', files)

    # 2 + 2 = 4 > 3 → 拒绝
    with pytest.raises(ValueError, match='max 3 files per batch'):
        attempt(3)

    # 管理员把上限调到 10 后，同样的追加放行
    assert attempt(10) == {'batch': {}, 'sessions': []}


# ---------------------------------------------------------------------------
# UI 路由：前置校验 + 列表下发 maxSessions
# ---------------------------------------------------------------------------

def _client():
    from app import app
    app.config['TESTING'] = True
    return app.test_client()


def _h(uid='u1', role='developer'):
    from auth import create_token
    return {'Authorization': 'Bearer ' + create_token({'id': uid, 'username': uid, 'role': role})}


def test_ui_create_rejects_beyond_configured_limit():
    files = [{'name': f'f{i}.txt', 'path': f'batch-staging/u/abc/f{i}.txt'}
             for i in range(3)]
    with patch('routes.ai_chat_batches.get_max_files_per_batch', return_value=2):
        resp = _client().post('/ai/chat/batches', headers=_h(), json={
            'name': 'n', 'prompt': 'p', 'files': files})
    assert resp.status_code == 400
    assert '最多 2 个' in resp.get_json()['error']


def test_ui_list_exposes_max_sessions():
    resp = _client().get('/ai/chat/batches', headers=_h())
    assert resp.status_code == 200
    body = resp.get_json()
    assert isinstance(body.get('maxSessions'), int)
    assert body['maxSessions'] >= 1


# ---------------------------------------------------------------------------
# AI 设置接口：maxBatchSessions 读写
# ---------------------------------------------------------------------------

def test_settings_roundtrip_max_batch_sessions():
    import utils.ai_query as aq

    row = (True, 'sk', 'https://x/v1', 'qwen-plus', 30, 1024, None,
           False, 'text-embedding-v3', 'p/m', 80)
    with patch.object(aq, 'get_db', _db(row)):
        assert aq.get_ai_settings()['maxBatchSessions'] == 80

    # 老库短行（无该列）回落默认
    with patch.object(aq, 'get_db', _db(row[:10])):
        assert aq.get_ai_settings()['maxBatchSessions'] == 50

    # update 透传参数
    conn = MagicMock()
    cur = conn.cursor()

    @contextmanager
    def fake_db():
        yield conn

    with patch.object(aq, 'get_db', fake_db):
        aq.update_ai_settings(True, 'sk', 'https://x/v1', 'm', 30, 1024,
                              max_batch_sessions=120)
    # update 末尾还会回读一次 SELECT，定位 UPDATE 那一条再断言
    updates = [c for c in cur.execute.call_args_list
               if 'UPDATE ai_settings' in c.args[0]]
    assert updates, '应执行设置写入'
    sql, params = updates[0].args
    assert 'max_batch_sessions = %s' in sql
    assert 120 in params


def test_settings_route_rejects_invalid_max_batch_sessions():
    """非法值在校验阶段 400（早于设置读取/写入）。"""
    resp = _client().put('/ai/settings', headers=_h(role='admin'), json={
        'enabled': True, 'apiKey': 'sk', 'endpoint': 'https://x/v1',
        'model': 'm', 'timeout': 30, 'maxTokens': 1024,
        'maxBatchSessions': 0})
    assert resp.status_code == 400
    assert '批任务子会话个数上限' in resp.get_json()['error']


def test_settings_route_accepts_valid_max_batch_sessions():
    """合法值透传到写入层（写库调用被替换，避免污染真实设置）。"""
    fake_settings = {'apiKey': '', 'updatedAt': None}
    with patch('routes.ai.get_ai_settings', return_value=dict(fake_settings)), \
         patch('routes.ai.update_ai_settings', return_value=dict(fake_settings)) as upd:
        resp = _client().put('/ai/settings', headers=_h(role='admin'), json={
            'enabled': True, 'apiKey': 'sk', 'endpoint': 'https://x/v1',
            'model': 'm', 'timeout': 30, 'maxTokens': 1024,
            'maxBatchSessions': 80})
    assert resp.status_code == 200
    assert upd.call_args.kwargs['max_batch_sessions'] == 80
