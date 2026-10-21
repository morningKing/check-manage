# -*- coding: utf-8 -*-
"""附件端点：上传/读取/属主隔离/删除级联/类型与大小校验。"""
import io
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import pytest

from app import app
from auth import create_token
from db import get_db

PNG = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
       b'\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01'
       b'\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82')


@pytest.fixture()
def att_env(tmp_path, monkeypatch):
    import config
    import routes.suggest_editor as se
    monkeypatch.setattr(config, 'SE_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(se, 'SE_DATA_DIR', str(tmp_path))
    suffix = os.urandom(4).hex()
    owner, other = f'att-owner-{suffix}', f'att-other-{suffix}'
    doc_id = f'att-doc-{suffix}'
    with get_db() as conn:
        with conn.cursor() as cur:
            for uid, name in ((owner, 'atto-'), (other, 'attx-')):
                cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                            "VALUES (%s,%s,'x',%s,'developer')", (uid, name + suffix, uid))
            cur.execute("INSERT INTO se_documents (id, title, owner_user_id) VALUES (%s,%s,%s)",
                        (doc_id, '附件测试', owner))
        conn.commit()
    client = app.test_client()
    app.config['TESTING'] = True
    yield {
        'client': client,
        'owner': {'Authorization': f"Bearer {create_token({'id': owner, 'username': 'o', 'role': 'developer'})}"},
        'other': {'Authorization': f"Bearer {create_token({'id': other, 'username': 'x', 'role': 'developer'})}"},
        'doc_id': doc_id,
        'tmp': str(tmp_path),
    }
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM se_attachments WHERE document_id = %s", (doc_id,))
            cur.execute("DELETE FROM se_documents WHERE id = %s", (doc_id,))
            cur.execute("DELETE FROM users WHERE id IN (%s,%s)", (owner, other))
        conn.commit()


def test_upload_and_read(att_env):
    c = att_env['client']
    r = c.post(f"/suggest-editor/documents/{att_env['doc_id']}/attachments",
               headers=att_env['owner'],
               data={'file': (io.BytesIO(PNG), '图.png')},
               content_type='multipart/form-data')
    assert r.status_code == 201
    body = r.get_json()['attachment']
    assert body['url'] == f"/suggest-editor/attachments/{body['id']}"
    got = c.get(body['url'], headers=att_env['owner'])
    assert got.status_code == 200 and got.data == PNG
    assert got.headers['Content-Type'].startswith('image/png')
    assert c.get(body['url'], headers=att_env['other']).status_code == 404
    assert c.get(body['url']).status_code == 401
    # access_token 查询参数可用（<img> 场景）
    tok = att_env['owner']['Authorization'].split(' ', 1)[1]
    assert c.get(f"{body['url']}?access_token={tok}").status_code == 200


def test_upload_rejects_non_image_and_oversize(att_env):
    c = att_env['client']
    url = f"/suggest-editor/documents/{att_env['doc_id']}/attachments"
    bad = c.post(url, headers=att_env['owner'],
                 data={'file': (io.BytesIO(b'not an image'), 'x.png')},
                 content_type='multipart/form-data')
    assert bad.status_code == 400
    big = c.post(url, headers=att_env['owner'],
                 data={'file': (io.BytesIO(b'\x89PNG\r\n\x1a\n' + b'x' * (10 * 1024 * 1024)), 'big.png')},
                 content_type='multipart/form-data')
    assert big.status_code == 400


def test_non_owner_cannot_upload(att_env):
    c = att_env['client']
    url = f"/suggest-editor/documents/{att_env['doc_id']}/attachments"
    r = c.post(url, headers=att_env['other'],
               data={'file': (io.BytesIO(PNG), 'a.png')},
               content_type='multipart/form-data')
    assert r.status_code == 404


def test_delete_document_cleans_attachment_dir(att_env):
    c = att_env['client']
    r = c.post(f"/suggest-editor/documents/{att_env['doc_id']}/attachments",
               headers=att_env['owner'],
               data={'file': (io.BytesIO(PNG), 'a.png')},
               content_type='multipart/form-data')
    att_id = r.get_json()['attachment']['id']
    assert Path(att_env['tmp'], 'attachments', att_env['doc_id']).is_dir()
    assert c.delete(f"/suggest-editor/documents/{att_env['doc_id']}",
                    headers=att_env['owner']).status_code == 200
    assert not Path(att_env['tmp'], 'attachments', att_env['doc_id']).exists()
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM se_attachments WHERE id = %s", (att_id,))
            assert cur.fetchone()[0] == 0
