import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _fake_db(row):
    """mock get_db，让 api_key_required 的 SELECT 返回指定行。"""
    cur = MagicMock()
    cur.fetchone.return_value = row
    conn = MagicMock()
    conn.cursor.return_value = cur
    from contextlib import contextmanager

    @contextmanager
    def fake_get_db():
        yield conn

    return fake_get_db


def test_api_key_info_carries_owner_user_id():
    """api_key_required 必须把 owner_user_id 放进 g.api_key_info。"""
    import auth as auth_mod
    from flask import Flask, g, jsonify

    app = Flask(__name__)

    @app.get('/probe')
    @auth_mod.api_key_required
    def probe():
        return jsonify(g.api_key_info)

    # (id, name, is_active, owner_user_id, owner_username, owner_role)
    with patch.object(auth_mod, 'get_db',
                      _fake_db(('ak-1', '集成密钥', True, 'user-42', 'alice', 'developer'))):
        resp = app.test_client().get('/probe', headers={'X-API-Key': 'cm_whatever'})

    assert resp.status_code == 200
    assert resp.get_json() == {
        'id': 'ak-1', 'name': '集成密钥', 'ownerUserId': 'user-42',
        'ownerUsername': 'alice', 'ownerRole': 'developer',
    }


def test_api_key_info_owner_can_be_none_for_legacy_keys():
    """存量密钥 owner_user_id 为 NULL 时不报错，ownerUserId 为 None。"""
    import auth as auth_mod
    from flask import Flask, g, jsonify

    app = Flask(__name__)

    @app.get('/probe')
    @auth_mod.api_key_required
    def probe():
        return jsonify(g.api_key_info)

    with patch.object(auth_mod, 'get_db', _fake_db(('ak-old', '老密钥', True, None, None, None))):
        resp = app.test_client().get('/probe', headers={'X-API-Key': 'cm_whatever'})

    assert resp.status_code == 200
    assert resp.get_json()['ownerUserId'] is None
