"""UI 批任务创建/追加的暂存路径归属校验（D1 修复回归）。

历史缺陷：create/append 只查 {name, path} 形状，绝对路径或
batch-staging/<他人>/... 会被 worker 原样拷进自己的工作区（跨用户任意读）。
"""
import sys, os
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from app import app
from auth import create_token

USER = 'user-u1'


def _setup(tmp_path, monkeypatch):
    import utils.workspace as ws_mod
    staging = tmp_path / 'batch-staging' / USER / 'us-1'
    staging.mkdir(parents=True, exist_ok=True)
    (staging / 'input.csv').write_text('a,b\n1,2', encoding='utf-8')
    monkeypatch.setattr(ws_mod, 'batch_roots', lambda: [str(tmp_path)])
    app.config['TESTING'] = True
    headers = {'Authorization': 'Bearer ' + create_token(
        {'id': USER, 'username': 'u1', 'role': 'developer'})}
    return app.test_client(), headers


def _patch_create(monkeypatch):
    import routes.ai_chat_batches as mod
    monkeypatch.setattr(mod, 'create_batch',
                        lambda uid, **kw: {'batchId': 'b1'})
    import utils.batch_engine as eng
    monkeypatch.setattr(eng, 'get_worker', lambda: MagicMock())


def _patch_append(monkeypatch):
    import routes.ai_chat_batches as mod
    monkeypatch.setattr(mod, 'append_to_batch',
                        lambda uid, bid, files, **kw: {'batchId': bid})
    import utils.batch_engine as eng
    monkeypatch.setattr(eng, 'get_worker', lambda: MagicMock())


GOOD = {'name': 'input.csv',
        'path': f'batch-staging/{USER}/us-1/input.csv'}


def test_create_accepts_own_staging_path(tmp_path, monkeypatch):
    client, headers = _setup(tmp_path, monkeypatch)
    _patch_create(monkeypatch)
    r = client.post('/ai/chat/batches', headers=headers,
                    json={'name': 'n', 'prompt': 'p', 'files': [GOOD]})
    assert r.status_code == 201


def test_create_rejects_absolute_path(tmp_path, monkeypatch):
    client, headers = _setup(tmp_path, monkeypatch)
    r = client.post('/ai/chat/batches', headers=headers,
                    json={'name': 'n', 'prompt': 'p',
                          'files': [{'name': 'x', 'path': 'C:/Windows/win.ini'}]})
    assert r.status_code == 400
    assert '路径无效' in r.get_json()['error']


def test_create_rejects_traversal(tmp_path, monkeypatch):
    client, headers = _setup(tmp_path, monkeypatch)
    r = client.post('/ai/chat/batches', headers=headers,
                    json={'name': 'n', 'prompt': 'p', 'files': [
                        {'name': 'x', 'path': f'batch-staging/{USER}/../victim/secret.txt'}]})
    assert r.status_code == 400


def test_create_rejects_other_users_staging(tmp_path, monkeypatch):
    client, headers = _setup(tmp_path, monkeypatch)
    r = client.post('/ai/chat/batches', headers=headers,
                    json={'name': 'n', 'prompt': 'p', 'files': [
                        {'name': 'x', 'path': 'batch-staging/someone-else/us/secret.txt'}]})
    assert r.status_code == 400


def test_create_rejects_missing_file(tmp_path, monkeypatch):
    client, headers = _setup(tmp_path, monkeypatch)
    r = client.post('/ai/chat/batches', headers=headers,
                    json={'name': 'n', 'prompt': 'p', 'files': [
                        {'name': 'x', 'path': f'batch-staging/{USER}/us-1/gone.csv'}]})
    assert r.status_code == 400
    assert '过期或不存在' in r.get_json()['error']


def test_append_rejects_absolute_path(tmp_path, monkeypatch):
    client, headers = _setup(tmp_path, monkeypatch)
    _patch_append(monkeypatch)
    r = client.post('/ai/chat/batches/b9/append', headers=headers,
                    json={'files': [{'name': 'x', 'path': '/etc/passwd'}]})
    assert r.status_code == 400


def test_append_accepts_own_staging_path(tmp_path, monkeypatch):
    client, headers = _setup(tmp_path, monkeypatch)
    _patch_append(monkeypatch)
    r = client.post('/ai/chat/batches/b9/append', headers=headers,
                    json={'files': [GOOD]})
    assert r.status_code == 200
    assert r.get_json()['batchId'] == 'b9'
