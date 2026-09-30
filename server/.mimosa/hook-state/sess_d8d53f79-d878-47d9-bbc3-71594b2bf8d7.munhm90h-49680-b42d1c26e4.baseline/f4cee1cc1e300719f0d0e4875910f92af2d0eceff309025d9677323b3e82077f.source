"""Tests for /data-files upload/download/metadata routes."""
import io
import os
import pytest
import psycopg2.extras


@pytest.fixture
def setup_app(db_conn, tmp_path, monkeypatch):
    """Real DB + real disk storage rooted at tmp_path; clean up after."""
    monkeypatch.setenv('DATA_FILES_ROOT', str(tmp_path))
    # Need to reload config so DATA_FILES_ROOT reflects the env var
    import importlib, config, routes.data_files
    importlib.reload(config)
    importlib.reload(routes.data_files)

    # seed an admin user the route can attribute uploads to
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, username, password_hash, display_name, role) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
            ('test-user-data-files', 'datafile-admin', 'x', 'admin', 'admin'),
        )
    db_conn.commit()

    from auth import create_token
    from app import app
    admin = create_token({'id': 'test-user-data-files',
                          'username': 'datafile-admin', 'role': 'admin'})
    guest = create_token({'id': 'test-user-data-files',
                          'username': 'guest', 'role': 'guest'})

    yield (
        app.test_client(),
        {'Authorization': f'Bearer {admin}'},
        {'Authorization': f'Bearer {guest}'},
        admin,
    )

    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM data_files WHERE uploaded_by = 'test-user-data-files'")
        cur.execute("DELETE FROM users WHERE id = 'test-user-data-files'")
    db_conn.commit()


def test_upload_persists_and_returns_metadata(setup_app):
    client, admin_h, _, _ = setup_app
    payload = {'file': (io.BytesIO(b'hello world'), 'note.txt')}
    r = client.post('/data-files/upload',
                    data=payload, content_type='multipart/form-data',
                    headers=admin_h)
    assert r.status_code == 201, r.get_data(as_text=True)
    body = r.get_json()
    assert body['name'] == 'note.txt'
    assert body['size'] == len(b'hello world')
    assert body['url'] == f'/api/data-files/{body["id"]}/download'
    assert body['mimeType']  # some non-empty string


def test_guest_cannot_upload(setup_app):
    client, _, guest_h, _ = setup_app
    payload = {'file': (io.BytesIO(b'nope'), 'a.txt')}
    r = client.post('/data-files/upload',
                    data=payload, content_type='multipart/form-data',
                    headers=guest_h)
    assert r.status_code == 403


def test_upload_rejects_missing_file(setup_app):
    client, admin_h, _, _ = setup_app
    r = client.post('/data-files/upload', headers=admin_h,
                    data={}, content_type='multipart/form-data')
    assert r.status_code == 400


def test_download_streams_file(setup_app):
    client, admin_h, _, _ = setup_app
    r1 = client.post('/data-files/upload',
                     data={'file': (io.BytesIO(b'roundtrip'), 'r.bin')},
                     content_type='multipart/form-data', headers=admin_h)
    fid = r1.get_json()['id']
    r2 = client.get(f'/data-files/{fid}/download', headers=admin_h)
    assert r2.status_code == 200
    assert r2.get_data() == b'roundtrip'


def test_download_via_access_token_query_param(setup_app):
    """The download URL needs to work inside <img src=...> / <a href=...>
    where setting an Authorization header is impossible."""
    client, admin_h, _, admin_token = setup_app
    r1 = client.post('/data-files/upload',
                     data={'file': (io.BytesIO(b'imagebytes'), 'pic.png')},
                     content_type='multipart/form-data', headers=admin_h)
    fid = r1.get_json()['id']
    # No Authorization header, just ?access_token=
    r2 = client.get(f'/data-files/{fid}/download?access_token={admin_token}')
    assert r2.status_code == 200
    assert r2.get_data() == b'imagebytes'


def test_download_missing_file_returns_404(setup_app):
    client, admin_h, _, _ = setup_app
    r = client.get('/data-files/no-such-id/download', headers=admin_h)
    assert r.status_code == 404


@pytest.fixture
def page_with_file_constraint(db_conn):
    """A page_configs row with a 'file' field restricted to .pdf/.docx."""
    coll = 'test-file-constraint'
    fields = [
        {'fieldName': 'attachment', 'label': '附件', 'controlType': 'file',
         'fileConfig': {'allowedExtensions': ['.pdf', '.docx']}},
        {'fieldName': 'unrestricted', 'label': '任意文件', 'controlType': 'file'},
    ]
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO page_configs (id, name, fields) VALUES (%s, %s, %s) "
            "ON CONFLICT (id) DO UPDATE SET fields = EXCLUDED.fields",
            (f'page-{coll}', coll, psycopg2.extras.Json(fields)),
        )
    db_conn.commit()
    yield coll
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM page_configs WHERE id = %s", (f'page-{coll}',))
    db_conn.commit()


def test_upload_rejects_disallowed_extension(setup_app, page_with_file_constraint):
    client, admin_h, _, _ = setup_app
    payload = {
        'file': (io.BytesIO(b'MZ...'), 'virus.exe'),
        'collection': page_with_file_constraint,
        'fieldName': 'attachment',
    }
    r = client.post('/data-files/upload', data=payload,
                    content_type='multipart/form-data', headers=admin_h)
    assert r.status_code == 400, r.get_data(as_text=True)
    assert '.exe' in r.get_json()['error']


def test_upload_accepts_allowed_extension(setup_app, page_with_file_constraint):
    client, admin_h, _, _ = setup_app
    payload = {
        'file': (io.BytesIO(b'%PDF-1.4'), 'report.pdf'),
        'collection': page_with_file_constraint,
        'fieldName': 'attachment',
    }
    r = client.post('/data-files/upload', data=payload,
                    content_type='multipart/form-data', headers=admin_h)
    assert r.status_code == 201, r.get_data(as_text=True)


def test_upload_extension_check_is_case_insensitive(setup_app, page_with_file_constraint):
    client, admin_h, _, _ = setup_app
    payload = {
        'file': (io.BytesIO(b'PK...'), 'contract.DOCX'),
        'collection': page_with_file_constraint,
        'fieldName': 'attachment',
    }
    r = client.post('/data-files/upload', data=payload,
                    content_type='multipart/form-data', headers=admin_h)
    assert r.status_code == 201, r.get_data(as_text=True)


def test_upload_unrestricted_field_allows_any_extension(setup_app, page_with_file_constraint):
    """A field with no fileConfig (or an empty allowedExtensions) is unrestricted."""
    client, admin_h, _, _ = setup_app
    payload = {
        'file': (io.BytesIO(b'anything'), 'notes.xyz'),
        'collection': page_with_file_constraint,
        'fieldName': 'unrestricted',
    }
    r = client.post('/data-files/upload', data=payload,
                    content_type='multipart/form-data', headers=admin_h)
    assert r.status_code == 201, r.get_data(as_text=True)


def test_upload_without_field_name_is_unrestricted(setup_app, page_with_file_constraint):
    """Backward compatibility: old callers that never send fieldName aren't blocked."""
    client, admin_h, _, _ = setup_app
    payload = {
        'file': (io.BytesIO(b'anything'), 'notes.xyz'),
        'collection': page_with_file_constraint,
    }
    r = client.post('/data-files/upload', data=payload,
                    content_type='multipart/form-data', headers=admin_h)
    assert r.status_code == 201, r.get_data(as_text=True)


def test_metadata_endpoint(setup_app):
    client, admin_h, _, _ = setup_app
    r1 = client.post('/data-files/upload',
                     data={'file': (io.BytesIO(b'm'), 'tiny.txt')},
                     content_type='multipart/form-data', headers=admin_h)
    fid = r1.get_json()['id']
    r2 = client.get(f'/data-files/{fid}', headers=admin_h)
    assert r2.status_code == 200
    m = r2.get_json()
    assert m['id'] == fid and m['name'] == 'tiny.txt' and m['size'] == 1
    assert m['url'].endswith(f'/data-files/{fid}/download')
