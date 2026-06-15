"""Open API 文件访问（数据页 file/image 字段上传的文件经 API Key 下载）。

此前文件下载只走 JWT（login_required_sse），API Key 客户端拿到记录里的文件 url 也下不了。
新增 /api/v1/files/<id>[/download]（api_key_required），并在记录响应里给文件字段补 apiUrl。
安全边界：仅当文件被**某个 api_public 集合**的记录引用时才可经 Open API 访问。

直连真实 DB（casemanage）+ 真实临时文件，与 test_workflow_integration 同模式（rebind get_db）。
"""
import os
import sys
import tempfile

import pytest
import psycopg2.extras

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import db as _db_module  # noqa: E402
from db import get_db  # noqa: E402
from auth import hash_api_key  # noqa: E402

API_KEY = 'cm_test_file_key_123'


@pytest.fixture
def client():
    from app import app as flask_app
    for mod_name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        if getattr(mod, 'get_db', None) is not None and (
            mod_name.startswith('routes.') or mod_name.startswith('utils.') or mod_name == 'auth'
        ):
            try:
                mod.get_db = _db_module.get_db
            except (AttributeError, TypeError):
                pass
    flask_app.config['TESTING'] = True
    return flask_app.test_client()


def _api_headers():
    return {'X-API-Key': API_KEY}


def _ensure_api_key(cur):
    cur.execute("DELETE FROM api_keys WHERE id = 'zzfile-key'")
    cur.execute("INSERT INTO api_keys (id, name, key_hash, is_active) VALUES (%s,%s,%s,TRUE)",
                ('zzfile-key', 'file-test', hash_api_key(API_KEY)))


def _make_file(cur, file_id, content=b'PDF-BYTES-1234', name='doc.pdf', mime='application/pdf'):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='_' + name)
    tmp.write(content)
    tmp.close()
    cur.execute("DELETE FROM data_files WHERE id = %s", (file_id,))
    cur.execute("INSERT INTO data_files (id, original_name, mime_type, size_bytes, storage_path) "
                "VALUES (%s,%s,%s,%s,%s)", (file_id, name, mime, len(content), tmp.name))
    return tmp.name


def _seed_collection(cur, coll, file_id, api_public=True, file_name='doc.pdf'):
    cur.execute("DELETE FROM dynamic_data WHERE collection = %s", (coll,))
    cur.execute("DELETE FROM page_configs WHERE id = %s", (f'page-{coll}',))
    cur.execute(
        "INSERT INTO page_configs (id, name, fields, api_public) VALUES (%s,%s,%s,%s)",
        (f'page-{coll}', coll,
         psycopg2.extras.Json([{'fieldName': 'attachment', 'controlType': 'file'}]),
         api_public),
    )
    file_obj = [{'uid': file_id, 'name': file_name, 'size': 14, 'type': 'application/pdf',
                 'url': f'/api/data-files/{file_id}/download'}]
    cur.execute("INSERT INTO dynamic_data (id, collection, data, branch_id) VALUES (%s,%s,%s,'main')",
                (f'{coll}-r1', coll, psycopg2.extras.Json({'attachment': file_obj})))


def _cleanup(colls=(), file_paths=()):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM api_keys WHERE id = 'zzfile-key'")
        for c in colls:
            cur.execute("DELETE FROM dynamic_data WHERE collection = %s", (c,))
            cur.execute("DELETE FROM page_configs WHERE id = %s", (f'page-{c}',))
        cur.execute("DELETE FROM data_files WHERE id LIKE 'zzfile-%'")
        conn.commit()
    for p in file_paths:
        try:
            os.remove(p)
        except OSError:
            pass


def test_download_file_in_public_collection(client):
    coll, fid = 'zzfilepub', 'zzfile-pub1'
    path = None
    try:
        with get_db() as conn:
            cur = conn.cursor()
            _ensure_api_key(cur)
            path = _make_file(cur, fid, content=b'HELLO-PDF-BYTES')
            _seed_collection(cur, coll, fid, api_public=True)
            conn.commit()
        r = client.get(f'/api/v1/files/{fid}/download', headers=_api_headers())
        assert r.status_code == 200, r.get_data(as_text=True)
        assert r.data == b'HELLO-PDF-BYTES'
        assert 'attachment' in (r.headers.get('Content-Disposition') or '')
    finally:
        _cleanup([coll], [path] if path else [])


def test_download_requires_api_key(client):
    r = client.get('/api/v1/files/anything/download')
    assert r.status_code == 401


def test_download_file_in_private_collection_is_404(client):
    """文件只被非公开集合引用 → 不可经 Open API 下载（安全边界）。"""
    coll, fid = 'zzfilepriv', 'zzfile-priv1'
    path = None
    try:
        with get_db() as conn:
            cur = conn.cursor()
            _ensure_api_key(cur)
            path = _make_file(cur, fid)
            _seed_collection(cur, coll, fid, api_public=False)
            conn.commit()
        r = client.get(f'/api/v1/files/{fid}/download', headers=_api_headers())
        assert r.status_code == 404, r.get_data(as_text=True)
    finally:
        _cleanup([coll], [path] if path else [])


def test_file_metadata_endpoint(client):
    coll, fid = 'zzfilemeta', 'zzfile-meta1'
    path = None
    try:
        with get_db() as conn:
            cur = conn.cursor()
            _ensure_api_key(cur)
            path = _make_file(cur, fid, name='report.pdf')
            _seed_collection(cur, coll, fid, api_public=True, file_name='report.pdf')
            conn.commit()
        r = client.get(f'/api/v1/files/{fid}', headers=_api_headers())
        assert r.status_code == 200, r.get_data(as_text=True)
        d = r.get_json()['data']
        assert d['name'] == 'report.pdf'
        assert d['downloadUrl'] == f'/api/v1/files/{fid}/download'
    finally:
        _cleanup([coll], [path] if path else [])


def test_record_response_enriches_file_apiurl(client):
    coll, fid = 'zzfileenrich', 'zzfile-enr1'
    path = None
    try:
        with get_db() as conn:
            cur = conn.cursor()
            _ensure_api_key(cur)
            path = _make_file(cur, fid)
            _seed_collection(cur, coll, fid, api_public=True)
            conn.commit()
        # 单条
        r = client.get(f'/api/v1/collections/{coll}/{coll}-r1', headers=_api_headers())
        assert r.status_code == 200, r.get_data(as_text=True)
        att = r.get_json()['data']['attachment']
        assert att[0]['apiUrl'] == f'/api/v1/files/{fid}/download'
        # 列表
        r2 = client.get(f'/api/v1/collections/{coll}', headers=_api_headers())
        assert r2.status_code == 200
        att2 = r2.get_json()['data'][0]['attachment']
        assert att2[0]['apiUrl'] == f'/api/v1/files/{fid}/download'
    finally:
        _cleanup([coll], [path] if path else [])
