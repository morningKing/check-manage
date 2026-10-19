# -*- coding: utf-8 -*-
"""检查规则端点测试：全局（admin 门）与文档级（属主门）+ CSV 导入导出 + 播种。"""
import io
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import pytest

from app import app
from auth import create_token
from db import get_db

SAMPLE_RULE = {'id': 'r1', 'name': '型号纠错', 'enabled': True, 'severity': 'error',
               'maps': [{'wrong': 'S-5700', 'right': 'S5700'}]}


@pytest.fixture()
def rules_env(tmp_path, monkeypatch):
    import routes.suggest_editor as se
    monkeypatch.setattr(se, 'SE_DATA_DIR', str(tmp_path))
    suffix = os.urandom(4).hex()
    admin_id, dev_id = f'se-rules-admin-{suffix}', f'se-rules-dev-{suffix}'
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                        "VALUES (%s,%s,'x',%s,'admin')", (admin_id, 'sera-' + suffix, admin_id))
            cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                        "VALUES (%s,%s,'x',%s,'developer')", (dev_id, 'serd-' + suffix, dev_id))
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
            cur.execute("DELETE FROM se_document_states WHERE document_id IN "
                        "(SELECT id FROM se_documents WHERE owner_user_id IN (%s,%s))", (admin_id, dev_id))
            cur.execute("DELETE FROM se_documents WHERE owner_user_id IN (%s,%s)", (admin_id, dev_id))
            cur.execute("DELETE FROM users WHERE id IN (%s,%s)", (admin_id, dev_id))
        conn.commit()


def test_global_rules_require_admin_and_seed_example(rules_env):
    client, dev, admin = rules_env['client'], rules_env['dev'], rules_env['admin']
    assert client.get('/suggest-editor/rules/global', headers=dev).status_code == 403
    r = client.get('/suggest-editor/rules/global', headers=admin)
    assert r.status_code == 200
    rules = r.get_json()['rules']
    assert any('9800M12' in (rule.get('presence', [{}])[0].get('triggers', ['']) or [''])[0]
               for rule in rules if rule.get('presence'))
    # 播种幂等：再 GET 内容一致
    assert client.get('/suggest-editor/rules/global', headers=admin).get_json() == {'rules': rules}


def test_global_rules_put_roundtrip_and_reject_bad(rules_env):
    client, admin = rules_env['client'], rules_env['admin']
    r = client.put('/suggest-editor/rules/global', headers=admin, json={'rules': [SAMPLE_RULE]})
    assert r.get_json() == {'ok': True, 'count': 1}
    assert client.get('/suggest-editor/rules/global', headers=admin).get_json()['rules'] == [SAMPLE_RULE]
    bad = dict(SAMPLE_RULE, patterns=[{'extract': '(', 'valid': 'x', 'message': 'm'}])
    r = client.put('/suggest-editor/rules/global', headers=admin, json={'rules': [bad]})
    assert r.status_code == 400 and '正则' in r.get_json()['error']


def test_global_rules_import_export(rules_env):
    client, admin = rules_env['client'], rules_env['admin']
    client.put('/suggest-editor/rules/global', headers=admin, json={'rules': [SAMPLE_RULE]})
    csv_text = ('type,name,severity,enabled,wrong,right\n'
                'map,另一个纠错,warn,true,X-1,X1\n')
    # 非法 mode 必须显式 400，不能静默按 append
    r = client.post('/suggest-editor/rules/global/import', headers=admin,
                    data={'mode': 'bogus', 'file': (io.BytesIO(csv_text.encode('utf-8-sig')), 'r.csv')},
                    content_type='multipart/form-data')
    assert r.status_code == 400
    # Excel 另存 CSV 的 UTF-8 BOM 应被 utf-8-sig 解码吸收
    r = client.post('/suggest-editor/rules/global/import', headers=admin,
                    data={'mode': 'append', 'file': (io.BytesIO(csv_text.encode('utf-8-sig')), 'r.csv')},
                    content_type='multipart/form-data')
    assert r.get_json() == {'ok': True, 'added': 1, 'skipped': 0, 'total': 2}
    r = client.get('/suggest-editor/rules/global/export', headers=admin)
    assert 'attachment' in r.headers.get('Content-Disposition', '')
    assert 'type,name,severity' in r.data.decode('utf-8')


def test_document_rules_owner_scope(rules_env):
    client, env = rules_env['client'], rules_env
    r = client.post('/suggest-editor/documents', headers=env['dev'], json={'title': '规则文档'})
    doc_id = r.get_json()['document']['id']
    assert client.put(f'/suggest-editor/documents/{doc_id}/rules', headers=env['dev'],
                      json={'rules': [SAMPLE_RULE]}).get_json() == {'ok': True, 'count': 1}
    assert client.get(f'/suggest-editor/documents/{doc_id}/rules',
                      headers=env['dev']).get_json()['rules'] == [SAMPLE_RULE]
    # 非属主（含 admin）404
    assert client.get(f'/suggest-editor/documents/{doc_id}/rules',
                      headers=env['admin']).status_code == 404
    assert client.put(f'/suggest-editor/documents/{doc_id}/rules', headers=env['admin'],
                      json={'rules': [SAMPLE_RULE]}).status_code == 404


def test_rules_file_written_at_expected_paths(rules_env):
    from routes.suggest_editor import SE_DATA_DIR, _doc_rules_path, _global_rules_path
    client, env = rules_env['client'], rules_env
    client.put('/suggest-editor/rules/global', headers=env['admin'], json={'rules': [SAMPLE_RULE]})
    assert os.path.isfile(_global_rules_path())
    r = client.post('/suggest-editor/documents', headers=env['dev'], json={'title': '路径'})
    doc_id = r.get_json()['document']['id']
    client.put(f'/suggest-editor/documents/{doc_id}/rules', headers=env['dev'], json={'rules': [SAMPLE_RULE]})
    assert os.path.isfile(_doc_rules_path(doc_id))
