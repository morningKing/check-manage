"""
JWT 认证模块单元测试

测试 token 创建/解析、login_required 和 admin_required 装饰器。
"""

import sys
import os
import pytest
import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from auth import create_token, decode_token, hash_api_key
from config import JWT_SECRET
import jwt


# ==================== create_token ====================

class TestCreateToken:
    def test_creates_valid_token(self):
        user = {'id': 'u1', 'username': 'admin', 'role': 'admin'}
        token = create_token(user)
        assert isinstance(token, str)
        assert len(token) > 0

    def test_payload_contains_fields(self):
        user = {'id': 'u1', 'username': 'testuser', 'role': 'developer'}
        token = create_token(user)
        payload = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
        assert payload['userId'] == 'u1'
        assert payload['username'] == 'testuser'
        assert payload['role'] == 'developer'
        assert 'exp' in payload

    def test_token_has_expiry(self):
        user = {'id': 'u1', 'username': 'admin', 'role': 'admin'}
        token = create_token(user)
        payload = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
        exp = datetime.datetime.fromtimestamp(payload['exp'], tz=datetime.timezone.utc)
        now = datetime.datetime.now(datetime.timezone.utc)
        # 过期时间应在 23~25 小时之间
        diff_hours = (exp - now).total_seconds() / 3600
        assert 23 <= diff_hours <= 25


# ==================== decode_token ====================

class TestDecodeToken:
    def test_valid_token(self):
        user = {'id': 'u1', 'username': 'admin', 'role': 'admin'}
        token = create_token(user)
        payload = decode_token(token)
        assert payload is not None
        assert payload['username'] == 'admin'

    def test_expired_token(self):
        payload = {
            'userId': 'u1',
            'username': 'admin',
            'role': 'admin',
            'exp': datetime.datetime.utcnow() - datetime.timedelta(hours=1),
        }
        token = jwt.encode(payload, JWT_SECRET, algorithm='HS256')
        assert decode_token(token) is None

    def test_invalid_token(self):
        assert decode_token('invalid.token.here') is None

    def test_wrong_secret(self):
        payload = {
            'userId': 'u1',
            'username': 'admin',
            'role': 'admin',
            'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=1),
        }
        token = jwt.encode(payload, 'wrong-secret', algorithm='HS256')
        assert decode_token(token) is None

    def test_empty_token(self):
        assert decode_token('') is None


# ==================== hash_api_key ====================

class TestHashApiKey:
    def test_deterministic(self):
        key = 'cm_test_key_123'
        assert hash_api_key(key) == hash_api_key(key)

    def test_different_keys_different_hashes(self):
        assert hash_api_key('key1') != hash_api_key('key2')

    def test_returns_hex_string(self):
        result = hash_api_key('test')
        assert len(result) == 64  # SHA-256 hex digest
        assert all(c in '0123456789abcdef' for c in result)


# ==================== login_required / admin_required (via Flask test client) ====================

class TestAuthDecorators:
    @pytest.fixture
    def test_app(self):
        from flask import Flask, jsonify, g
        from auth import login_required, admin_required

        app = Flask(__name__)
        app.config['TESTING'] = True

        @app.route('/protected')
        @login_required
        def protected():
            return jsonify({'user': g.current_user['username']})

        @app.route('/admin-only')
        @admin_required
        def admin_only():
            return jsonify({'user': g.current_user['username']})

        return app

    def test_login_required_no_token(self, test_app):
        client = test_app.test_client()
        resp = client.get('/protected')
        assert resp.status_code == 401

    def test_login_required_invalid_token(self, test_app):
        client = test_app.test_client()
        resp = client.get('/protected', headers={'Authorization': 'Bearer invalid'})
        assert resp.status_code == 401

    def test_login_required_valid_token(self, test_app):
        token = create_token({'id': 'u1', 'username': 'admin', 'role': 'admin'})
        client = test_app.test_client()
        resp = client.get('/protected', headers={'Authorization': f'Bearer {token}'})
        assert resp.status_code == 200
        assert resp.get_json()['user'] == 'admin'

    def test_login_required_no_bearer_prefix(self, test_app):
        token = create_token({'id': 'u1', 'username': 'admin', 'role': 'admin'})
        client = test_app.test_client()
        resp = client.get('/protected', headers={'Authorization': token})
        assert resp.status_code == 401

    def test_admin_required_admin_role(self, test_app):
        token = create_token({'id': 'u1', 'username': 'admin', 'role': 'admin'})
        client = test_app.test_client()
        resp = client.get('/admin-only', headers={'Authorization': f'Bearer {token}'})
        assert resp.status_code == 200

    def test_admin_required_not_admin(self, test_app):
        token = create_token({'id': 'u2', 'username': 'dev', 'role': 'developer'})
        client = test_app.test_client()
        resp = client.get('/admin-only', headers={'Authorization': f'Bearer {token}'})
        assert resp.status_code == 403

    def test_admin_required_guest(self, test_app):
        token = create_token({'id': 'u3', 'username': 'guest', 'role': 'guest'})
        client = test_app.test_client()
        resp = client.get('/admin-only', headers={'Authorization': f'Bearer {token}'})
        assert resp.status_code == 403
