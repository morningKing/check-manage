# -*- coding: utf-8 -*-
"""导入种子正文：属主门、一次性写入、消费清空、大小限制。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import pytest

from app import app
from auth import create_token
from db import get_db


@pytest.fixture()
def seed_env():
    suffix = os.urandom(4).hex()
    owner, other = f'seed-owner-{suffix}', f'seed-other-{suffix}'
    doc_id = f'seed-doc-{suffix}'
    with get_db() as conn:
        with conn.cursor() as cur:
            for uid, name in ((owner, 'seedo-'), (other, 'seedx-')):
                cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                            "VALUES (%s,%s,'x',%s,'developer')", (uid, name + suffix, uid))
            cur.execute("INSERT INTO se_documents (id, title, owner_user_id) VALUES (%s,%s,%s)",
                        (doc_id, 'seed 测试', owner))
        conn.commit()
    app.config['TESTING'] = True
    yield {
        'client': app.test_client(),
        'owner': {'Authorization': f"Bearer {create_token({'id': owner, 'username': 'o', 'role': 'developer'})}"},
        'other': {'Authorization': f"Bearer {create_token({'id': other, 'username': 'x', 'role': 'developer'})}"},
        'doc_id': doc_id,
    }
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM se_document_states WHERE document_id = %s", (doc_id,))
            cur.execute("DELETE FROM se_documents WHERE id = %s", (doc_id,))
            cur.execute("DELETE FROM users WHERE id IN (%s,%s)", (owner, other))
        conn.commit()


def test_seed_write_read_and_apply(seed_env):
    c, h = seed_env['client'], seed_env['owner']
    doc_id = seed_env['doc_id']
    html = '<h1>导入标题</h1><p>正文</p>'
    assert c.put(f'/suggest-editor/documents/{doc_id}/seed', headers=h,
                 json={'contentHtml': html}).status_code == 200
    got = c.get(f'/suggest-editor/documents/{doc_id}', headers=h).get_json()['document']
    assert got['seedContentHtml'] == html
    listed = c.get('/suggest-editor/documents', headers=h).get_json()['documents']
    assert all('seedContentHtml' not in d for d in listed)
    assert c.post(f'/suggest-editor/documents/{doc_id}/seed/applied', headers=h).status_code == 200
    got2 = c.get(f'/suggest-editor/documents/{doc_id}', headers=h).get_json()['document']
    assert got2['seedContentHtml'] is None


def test_seed_rejects_non_owner_conflict_and_oversize(seed_env):
    c, h = seed_env['client'], seed_env['owner']
    doc_id = seed_env['doc_id']
    assert c.put(f'/suggest-editor/documents/{doc_id}/seed',
                 headers=seed_env['other'], json={'contentHtml': '<p>x</p>'}).status_code == 404
    assert c.put(f'/suggest-editor/documents/{doc_id}/seed', headers=h,
                 json={'contentHtml': 'x' * (5 * 1024 * 1024 + 1)}).status_code == 400
    assert c.put(f'/suggest-editor/documents/{doc_id}/seed', headers=h,
                 json={'contentHtml': '<p>x</p>'}).status_code == 200
    assert c.put(f'/suggest-editor/documents/{doc_id}/seed', headers=h,
                 json={'contentHtml': '<p>y</p>'}).status_code == 409
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO se_document_states (document_id, state) VALUES (%s,%s)",
                        (doc_id, b''))
        conn.commit()
    c.post(f'/suggest-editor/documents/{doc_id}/seed/applied', headers=h)
    assert c.put(f'/suggest-editor/documents/{doc_id}/seed', headers=h,
                 json={'contentHtml': '<p>z</p>'}).status_code == 409
