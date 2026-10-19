# -*- coding: utf-8 -*-
"""智能写作词表管理（后台配置/导入/导出）路由测试。

SE_DATA_DIR 打到 pytest tmp_path：真文件、真原子写，不碰 dev 的
suggest-editor-server/.data。覆盖：权限门（developer 403 / admin 200）、
保存/读取往返、导入 append 去重与 replace 覆盖、导出附件头、文档级 owner 门
与非法 id 404、非 UTF-8 上传拒绝。
"""
import sys, os
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest

from app import app
from auth import create_token
from db import get_db


@pytest.fixture()
def vocab_env(tmp_path, monkeypatch):
    """把词表根目录指到 tmp，并造两个真实用户 + 两把 token。"""
    import routes.suggest_editor as se
    monkeypatch.setattr(se, 'SE_DATA_DIR', str(tmp_path))
    suffix = os.urandom(4).hex()
    admin_id, dev_id = f'se-vocab-admin-{suffix}', f'se-vocab-dev-{suffix}'
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                        "VALUES (%s,%s,'x',%s,'admin')", (admin_id, 'seva-' + suffix, admin_id))
            cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                        "VALUES (%s,%s,'x',%s,'developer')", (dev_id, 'sev-' + suffix, dev_id))
        conn.commit()
    client = app.test_client()
    app.config['TESTING'] = True
    yield {
        'client': client,
        'admin': {'Authorization': f"Bearer {create_token({'id': admin_id, 'username': 'seva', 'role': 'admin'})}"},
        'dev': {'Authorization': f"Bearer {create_token({'id': dev_id, 'username': 'sev', 'role': 'developer'})}"},
        'admin_id': admin_id,
        'dev_id': dev_id,
    }
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM se_document_states WHERE document_id IN "
                        "(SELECT id FROM se_documents WHERE owner_user_id IN (%s,%s))",
                        (admin_id, dev_id))
            cur.execute("DELETE FROM se_documents WHERE owner_user_id IN (%s,%s)",
                        (admin_id, dev_id))
            cur.execute("DELETE FROM users WHERE id IN (%s,%s)", (admin_id, dev_id))
        conn.commit()


def _import(client, headers, url, content, mode='append', filename='词表.txt'):
    import io
    return client.post(url, headers=headers, data={
        'mode': mode,
        'file': (io.BytesIO(content.encode('utf-8')), filename),
    }, content_type='multipart/form-data')


# ------------------------------- 全局词表 -------------------------------


def test_global_vocab_requires_admin_permission(vocab_env):
    client, dev_headers = vocab_env['client'], vocab_env['dev']
    assert client.get('/suggest-editor/vocabularies/global', headers=dev_headers).status_code == 403
    assert client.put('/suggest-editor/vocabularies/global', headers=dev_headers,
                      json={'content': 'x'}).status_code == 403
    assert _import(client, dev_headers, '/suggest-editor/vocabularies/global/import',
                   '词条').status_code == 403
    assert client.get('/suggest-editor/vocabularies/global/export',
                      headers=dev_headers).status_code == 403


def test_global_vocab_put_get_roundtrip(vocab_env):
    client, admin_headers = vocab_env['client'], vocab_env['admin']
    content = '# 注释行\n人工智能|AI\n综上所述\n'
    r = client.put('/suggest-editor/vocabularies/global', headers=admin_headers,
                   json={'content': content})
    assert r.status_code == 200
    assert r.get_json()['total'] == 2  # 注释不计入词条
    r = client.get('/suggest-editor/vocabularies/global', headers=admin_headers)
    assert r.get_json()['content'] == content

    # PUT 非 string / 超限
    assert client.put('/suggest-editor/vocabularies/global', headers=admin_headers,
                      json={'content': 123}).status_code == 400
    assert client.put('/suggest-editor/vocabularies/global', headers=admin_headers,
                      json={'content': 'x' * 200_001}).status_code == 400


def test_global_vocab_import_append_dedup_and_replace(vocab_env):
    client, admin_headers = vocab_env['client'], vocab_env['admin']
    client.put('/suggest-editor/vocabularies/global', headers=admin_headers,
               json={'content': '人工智能|AI\n综上所述\n'})

    # append：新词条追加；同词条正文（人工智能）去重——别名不同也算重名；
    # 注释行与空行忽略
    r = _import(client, admin_headers, '/suggest-editor/vocabularies/global/import',
                '值得注意的是\n人工智能|新别名\n# 注释\n\n', mode='append')
    body = r.get_json()
    assert r.status_code == 200
    assert body['added'] == 1 and body['skipped'] == 1 and body['total'] == 3

    content = client.get('/suggest-editor/vocabularies/global',
                         headers=admin_headers).get_json()['content']
    assert '人工智能|AI' in content  # 原条目不动（重名保留先出现的）
    assert '值得注意的是' in content

    # replace：整文件覆盖
    r = _import(client, admin_headers, '/suggest-editor/vocabularies/global/import',
                '只有这一条\n', mode='replace')
    assert r.get_json() == {'ok': True, 'total': 1, 'added': 1, 'skipped': 0}
    content = client.get('/suggest-editor/vocabularies/global',
                         headers=admin_headers).get_json()['content']
    assert content == '只有这一条\n'

    # 非法 mode / 非 UTF-8 文件
    assert _import(client, admin_headers, '/suggest-editor/vocabularies/global/import',
                   'x', mode='nope').status_code == 400
    import io
    r = client.post('/suggest-editor/vocabularies/global/import', headers=admin_headers,
                    data={'mode': 'append', 'file': (io.BytesIO(b'\xff\xfe\xfd'), 'bad.txt')},
                    content_type='multipart/form-data')
    assert r.status_code == 400 and 'UTF-8' in r.get_json()['error']


def test_global_vocab_export(vocab_env):
    client, admin_headers = vocab_env['client'], vocab_env['admin']
    client.put('/suggest-editor/vocabularies/global', headers=admin_headers,
               json={'content': '导出我\n'})
    r = client.get('/suggest-editor/vocabularies/global/export', headers=admin_headers)
    assert r.status_code == 200
    disposition = r.headers.get('Content-Disposition', '')
    # 中文文件名走 RFC 5987 filename*（ASCII fallback 是占位符）
    assert 'attachment' in disposition and 'filename*=UTF-8' in disposition
    from urllib.parse import unquote
    assert '词表-全局.txt' in unquote(disposition)
    assert r.data.decode('utf-8') == '导出我\n'

    # 从未配置过（文件不存在）也能导出空词表
    client.put('/suggest-editor/vocabularies/global', headers=admin_headers,
               json={'content': ''})


# ------------------------------- 文档级词表 -------------------------------


def test_document_vocab_owner_gate_and_roundtrip(vocab_env):
    client, env = vocab_env['client'], vocab_env
    r = client.post('/suggest-editor/documents', headers=env['dev'], json={'title': '词表文档'})
    doc_id = r.get_json()['document']['id']

    # owner 写读往返
    assert client.put(f'/suggest-editor/documents/{doc_id}/vocabulary', headers=env['dev'],
                      json={'content': '文档词|doc\n'}).status_code == 200
    r = client.get(f'/suggest-editor/documents/{doc_id}/vocabulary', headers=env['dev'])
    assert r.get_json()['content'] == '文档词|doc\n'

    # 非属主（admin 也无旁路）：读/写/导出全 404
    assert client.get(f'/suggest-editor/documents/{doc_id}/vocabulary',
                      headers=env['admin']).status_code == 404
    assert client.put(f'/suggest-editor/documents/{doc_id}/vocabulary', headers=env['admin'],
                      json={'content': 'x'}).status_code == 404
    assert client.get(f'/suggest-editor/documents/{doc_id}/vocabulary/export',
                      headers=env['admin']).status_code == 404

    # 导入 + 导出
    assert _import(client, env['dev'], f'/suggest-editor/documents/{doc_id}/vocabulary/import',
                   '追加词\n', mode='append').status_code == 200
    r = client.get(f'/suggest-editor/documents/{doc_id}/vocabulary/export', headers=env['dev'])
    assert r.status_code == 200
    assert '追加词' in r.data.decode('utf-8')

    # 非法 doc id（正则白名单外的形态，含编码穿越形态）→ 404，不落任何文件
    for bad in ('%2e%2e', 'zzz', 'ab'):
        assert client.get(f'/suggest-editor/documents/{bad}/vocabulary',
                          headers=env['dev']).status_code == 404

    # 落盘位置正确（tmp ws/<docId>/词表.txt），供 Node 侧热同步
    from routes.suggest_editor import _doc_vocab_path
    path = _doc_vocab_path(doc_id)
    assert path and os.path.isfile(path)
    assert '追加词' in Path(path).read_text(encoding='utf-8')


def test_document_vocab_file_visible_to_node_shape(vocab_env):
    """写出的文件路径必须与 Node workspace.ts 的 workspaceDir 同构（ws/<id>/词表.txt）。"""
    client, env = vocab_env['client'], vocab_env
    r = client.post('/suggest-editor/documents', headers=env['admin'], json={'title': '路径同构'})
    doc_id = r.get_json()['document']['id']
    client.put(f'/suggest-editor/documents/{doc_id}/vocabulary', headers=env['admin'],
               json={'content': '对齐词\n'})
    from routes.suggest_editor import SE_DATA_DIR, _doc_vocab_path
    path = _doc_vocab_path(doc_id)
    expected = os.path.join(str(SE_DATA_DIR), 'ws', doc_id, '词表.txt')
    assert os.path.realpath(path) == os.path.realpath(expected)
