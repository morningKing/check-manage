# -*- coding: utf-8 -*-
"""文本模板（/ 快捷插入）端点测试：登录可读 + 默认播种、admin 写入、校验边界。"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import pytest

from app import app
from auth import create_token
from db import get_db

SAMPLE = {
    'id': 'snip-1',
    'name': '业务描述',
    'description': '背景/目标/范围',
    'keywords': 'yewu 业务',
    'contentHtml': '<p>业务背景：___</p><p>建设目标：___</p>',
}


@pytest.fixture()
def snippets_env(tmp_path, monkeypatch):
    import routes.suggest_editor as se
    monkeypatch.setattr(se, 'SE_DATA_DIR', str(tmp_path))
    suffix = os.urandom(4).hex()
    admin_id, dev_id = f'se-snip-admin-{suffix}', f'se-snip-dev-{suffix}'
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                        "VALUES (%s,%s,'x',%s,'admin')", (admin_id, 'sesa-' + suffix, admin_id))
            cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                        "VALUES (%s,%s,'x',%s,'developer')", (dev_id, 'sesd-' + suffix, dev_id))
        conn.commit()
    client = app.test_client()
    app.config['TESTING'] = True
    yield {
        'client': client,
        'admin': {'Authorization': f"Bearer {create_token({'id': admin_id, 'username': 'a', 'role': 'admin'})}"},
        'dev': {'Authorization': f"Bearer {create_token({'id': dev_id, 'username': 'd', 'role': 'developer'})}"},
        'data_dir': str(tmp_path),
    }
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE id IN (%s,%s)", (admin_id, dev_id))
        conn.commit()


def test_get_requires_login_and_seeds_default(snippets_env):
    client, dev, admin = snippets_env['client'], snippets_env['dev'], snippets_env['admin']
    assert client.get('/suggest-editor/snippets/global').status_code == 401
    r = client.get('/suggest-editor/snippets/global', headers=dev)
    assert r.status_code == 200
    snippets = r.get_json()['snippets']
    assert len(snippets) == 1 and snippets[0]['id'] == 'snip-example'
    assert snippets[0]['contentHtml'].strip()
    # 播种落盘且幂等
    path = Path(snippets_env['data_dir']) / '文本模板-全局.json'
    assert path.is_file()
    assert client.get('/suggest-editor/snippets/global', headers=admin).get_json() == {'snippets': snippets}


def test_put_requires_admin_and_roundtrip(snippets_env):
    client, dev, admin = snippets_env['client'], snippets_env['dev'], snippets_env['admin']
    assert client.put('/suggest-editor/snippets/global', headers=dev,
                      json={'snippets': [SAMPLE]}).status_code == 403
    r = client.put('/suggest-editor/snippets/global', headers=admin, json={'snippets': [SAMPLE]})
    assert r.status_code == 200 and r.get_json() == {'ok': True, 'count': 1}
    assert client.get('/suggest-editor/snippets/global', headers=admin).get_json()['snippets'] == [SAMPLE]
    # 空列表是合法状态：清空后不再回落到默认模板
    assert client.put('/suggest-editor/snippets/global', headers=admin,
                      json={'snippets': []}).get_json() == {'ok': True, 'count': 0}
    assert client.get('/suggest-editor/snippets/global', headers=admin).get_json()['snippets'] == []


@pytest.mark.parametrize('mutate,keyword', [
    (lambda s: s.pop('contentHtml'), 'contentHtml'),
    (lambda s: s.update(contentHtml='   '), 'contentHtml'),
    (lambda s: s.update(contentHtml='x' * 5001), '5000'),
    (lambda s: s.update(name=''), 'name'),
    (lambda s: s.update(id=''), 'id'),
    (lambda s: s.update(description='d' * 201), 'description'),
])
def test_put_validation_rejects_bad_snippet(snippets_env, mutate, keyword):
    client, admin = snippets_env['client'], snippets_env['admin']
    bad = dict(SAMPLE)
    mutate(bad)
    r = client.put('/suggest-editor/snippets/global', headers=admin, json={'snippets': [bad]})
    assert r.status_code == 400 and keyword in r.get_json()['error']


def test_put_rejects_duplicate_id_and_bad_shape(snippets_env):
    client, admin = snippets_env['client'], snippets_env['admin']
    r = client.put('/suggest-editor/snippets/global', headers=admin,
                   json={'snippets': [SAMPLE, dict(SAMPLE)]})
    assert r.status_code == 400 and '重复' in r.get_json()['error']
    assert client.put('/suggest-editor/snippets/global', headers=admin,
                      json={'items': []}).status_code == 400
    assert client.put('/suggest-editor/snippets/global', headers=admin,
                      json={'snippets': ['not-an-object']}).status_code == 400


def test_corrupt_file_reports_500_and_put_repairs(snippets_env):
    client, admin = snippets_env['client'], snippets_env['admin']
    path = Path(snippets_env['data_dir']) / '文本模板-全局.json'
    path.write_text('{broken', encoding='utf-8', newline='')
    r = client.get('/suggest-editor/snippets/global', headers=admin)
    assert r.status_code == 500 and 'JSON' in r.get_json()['error']
    assert client.put('/suggest-editor/snippets/global', headers=admin,
                      json={'snippets': [SAMPLE]}).status_code == 200
    assert client.get('/suggest-editor/snippets/global', headers=admin).get_json()['snippets'] == [SAMPLE]
    # 落盘内容是合法 JSON 且中文不转义
    raw = path.read_text(encoding='utf-8')
    assert json.loads(raw)['snippets'][0]['name'] == '业务描述'
