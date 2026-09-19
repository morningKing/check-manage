"""routes/ai_skills.py —— 全局技能管理路由（此前无路由级测试）。

权限模型：全部端点要求 admin.ai_settings（developer/guest 应 403）。
"""
import io
import sys, os
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from app import app

SKILL = {'id': 'sk1', 'name': 'pdf-export', 'description': 'd', 'enabled': True,
         'uploader_name': 'admin', 'file_size': 10,
         'created_at': None, 'updated_at': None}


def _client():
    app.config['TESTING'] = True
    return app.test_client()


def _zip_bytes():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('pdf-export/SKILL.md', '---\nname: pdf-export\n---\nbody')
    buf.seek(0)
    return buf


def test_list_requires_admin_capability(admin_headers, dev_headers):
    c = _client()
    assert c.get('/ai/skills', headers=admin_headers).status_code == 200
    assert c.get('/ai/skills', headers=dev_headers).status_code == 403
    assert c.get('/ai/skills').status_code == 401


def test_list_maps_row_to_camel_case(admin_headers, mock_conn, mock_cursor):
    mock_cursor.fetchall.return_value = [SKILL]
    with patch_list_installed([SKILL]):
        r = _client().get('/ai/skills', headers=admin_headers)
    assert r.status_code == 200
    s = r.get_json()['skills'][0]
    assert s['name'] == 'pdf-export' and 'uploadedBy' in s and 'fileSize' in s


def test_get_missing_returns_404(admin_headers):
    with patch_skill_module('get_global_skill', lambda sid: None):
        r = _client().get('/ai/skills/nope', headers=admin_headers)
    assert r.status_code == 404
    assert r.get_json()['error'] == '技能不存在'


def test_upload_requires_zip_and_calls_installer(admin_headers):
    calls = {}

    def fake_install(path, description, user_id, root):
        calls['user_id'] = user_id
        return SKILL

    with patch_skill_module('install_skill_from_zip', fake_install):
        r = _client().post('/ai/skills', headers=admin_headers,
                           data={'file': (_zip_bytes(), 's.zip'),
                                 'description': 'd'},
                           content_type='multipart/form-data')
    assert r.status_code == 201
    assert calls['user_id'] == 'user-admin'


def test_upload_without_file_400(admin_headers):
    r = _client().post('/ai/skills', headers=admin_headers, data={})
    assert r.status_code == 400


def test_upload_invalid_zip_maps_valueerror_to_400(admin_headers):
    def boom(*a):
        raise ValueError('技能 zip 缺少 SKILL.md')
    with patch_skill_module('install_skill_from_zip', boom):
        r = _client().post('/ai/skills', headers=admin_headers,
                           data={'file': (_zip_bytes(), 's.zip')},
                           content_type='multipart/form-data')
    assert r.status_code == 400
    assert 'SKILL.md' in r.get_json()['error']


def test_update_partial_fields(admin_headers):
    seen = {}

    def fake_update(sid, description=None, enabled=None):
        seen.update(skill_id=sid, description=description, enabled=enabled)
        return SKILL

    with patch_skill_module('update_global_skill', fake_update):
        r = _client().put('/ai/skills/sk1', headers=admin_headers,
                          json={'enabled': False})
    assert r.status_code == 200
    assert seen['enabled'] is False and seen['description'] is None


def test_delete_missing_404(admin_headers):
    with patch_skill_module('delete_global_skill', lambda sid, ws: False):
        r = _client().delete('/ai/skills/nope', headers=admin_headers)
    assert r.status_code == 404


def test_skill_files_routes(admin_headers):
    with patch_skill_module('get_global_skill', lambda sid: SKILL), \
         patch_skill_module('list_skill_files', lambda sid, ws: ['SKILL.md']), \
         patch_skill_module('read_skill_file',
                            lambda sid, p, ws: {'name': 'SKILL.md', 'content': 'x'}):
        c = _client()
        assert c.get('/ai/skills/sk1/files',
                     headers=admin_headers).get_json()['files'] == ['SKILL.md']
        r = c.get('/ai/skills/sk1/files/SKILL.md', headers=admin_headers)
        assert r.status_code == 200
        assert r.get_json()['content'] == 'x'
        with patch_skill_module('get_global_skill', lambda sid: None):
            r = c.get('/ai/skills/nope/files', headers=admin_headers)
        assert r.status_code == 404


# ── helpers ──────────────────────────────────────────────────────────────

def patch_skill_module(name, repl):
    import routes.ai_skills as mod
    from unittest.mock import patch
    return patch.object(mod, name, repl)


def patch_list_installed(rows):
    import routes.ai_skills as mod
    from unittest.mock import patch
    return patch.object(mod, 'list_global_skills', lambda: rows)
