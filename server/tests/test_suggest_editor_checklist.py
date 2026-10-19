# -*- coding: utf-8 -*-
"""检查要点（全局，AI 分析 prompt 注入）路由测试。

与词表测试同构：权限门 / 首次 GET 自动播种默认清单 / PUT 往返 / 导入去重与
覆盖 / 导出附件头。SE_DATA_DIR 打到 tmp_path。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest

from app import app
from auth import create_token
from db import get_db


@pytest.fixture()
def checklist_env(tmp_path, monkeypatch):
    import routes.suggest_editor as se
    monkeypatch.setattr(se, 'SE_DATA_DIR', str(tmp_path))
    suffix = os.urandom(4).hex()
    admin_id, dev_id = f'se-ck-admin-{suffix}', f'se-ck-dev-{suffix}'
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                        "VALUES (%s,%s,'x',%s,'admin')", (admin_id, 'seck-' + suffix, admin_id))
            cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                        "VALUES (%s,%s,'x',%s,'developer')", (dev_id, 'seckd-' + suffix, dev_id))
        conn.commit()
    client = app.test_client()
    app.config['TESTING'] = True
    yield {
        'client': client,
        'admin': {'Authorization': f"Bearer {create_token({'id': admin_id, 'username': 'seck', 'role': 'admin'})}"},
        'dev': {'Authorization': f"Bearer {create_token({'id': dev_id, 'username': 'seckd', 'role': 'developer'})}"},
        'admin_id': admin_id,
        'dev_id': dev_id,
    }
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE id IN (%s,%s)", (admin_id, dev_id))
        conn.commit()


def _import(client, headers, content, mode='append'):
    import io
    return client.post('/suggest-editor/checklists/global/import', headers=headers, data={
        'mode': mode,
        'file': (io.BytesIO(content.encode('utf-8')), '检查要点.txt'),
    }, content_type='multipart/form-data')


def test_checklist_requires_admin_permission(checklist_env):
    client, dev = checklist_env['client'], checklist_env['dev']
    assert client.get('/suggest-editor/checklists/global', headers=dev).status_code == 403
    assert client.put('/suggest-editor/checklists/global', headers=dev,
                      json={'content': 'x'}).status_code == 403
    assert _import(client, dev, '要点').status_code == 403
    assert client.get('/suggest-editor/checklists/global/export', headers=dev).status_code == 403


def test_checklist_first_get_seeds_default(checklist_env):
    client, admin = checklist_env['client'], checklist_env['admin']
    r = client.get('/suggest-editor/checklists/global', headers=admin)
    assert r.status_code == 200
    content = r.get_json()['content']
    # 默认清单面向技术文档：业务/数据、逻辑/流程、一致性/可执行性、表达
    assert '数据口径一致' in content and '因果成立' in content and '边界与例外' in content
    # 已落盘：再 GET 内容稳定；文件可被 Node readChecklistEntries 直接读取
    again = client.get('/suggest-editor/checklists/global', headers=admin).get_json()['content']
    assert again == content


def test_checklist_put_roundtrip_and_validation(checklist_env):
    client, admin = checklist_env['client'], checklist_env['admin']
    r = client.put('/suggest-editor/checklists/global', headers=admin,
                   json={'content': '# 注释\n自定义要点：单位一律用 SI 制\n'})
    assert r.get_json()['total'] == 1
    assert '自定义要点' in client.get('/suggest-editor/checklists/global',
                                     headers=admin).get_json()['content']
    assert client.put('/suggest-editor/checklists/global', headers=admin,
                      json={'content': 123}).status_code == 400
    assert client.put('/suggest-editor/checklists/global', headers=admin,
                      json={'content': 'x' * 200_001}).status_code == 400


def test_checklist_import_append_dedup_replace_export(checklist_env):
    client, admin = checklist_env['client'], checklist_env['admin']
    client.put('/suggest-editor/checklists/global', headers=admin,
               json={'content': '因果成立：原有要点\n'})

    # 追加按整行去重：完全相同的行跳过，描述变化视为新要点（更新请用覆盖模式）
    r = _import(client, admin, '新增要点：接口超时口径\n因果成立：原有要点\n', mode='append')
    assert r.get_json() == {'ok': True, 'total': 2, 'added': 1, 'skipped': 1}

    r = _import(client, admin, '只剩一条\n', mode='replace')
    assert r.get_json()['total'] == 1

    r = client.get('/suggest-editor/checklists/global/export', headers=admin)
    assert r.status_code == 200
    assert 'attachment' in r.headers.get('Content-Disposition', '')
    assert r.data.decode('utf-8') == '只剩一条\n'
