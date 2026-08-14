"""admin 文件端点的核心测试（Task 6）。

特别构造（与 .superpowers/sdd/2026-08-14-.../task-6-brief.md 同形式，但 fixture
按 Ruling E 改用 conftest 实际提供的 `app`/`client`/`admin_headers`/`dev_headers`
+ `db_conn`，不存在 `admin_client`/`non_admin_client`/`db_check`/`seeded_pair_2`）：

1. 权限门参数化遍历 6 个新端点 — 全部挂 @require_permission_sse。
2. 跨用户可见性 — 管理员能看别人子任务的产出文件。
3. 归属校验 — 守住 batch_id↔sid，不匹配返 404。
4. workspace_path=NULL 路径分支（files 空 vs preview/download/import/diff 400）。
5. 路径安全 safe_resolve（preview/download/import/diff 四闸门）。
6. 白名单扩展正向 — outputs/ 文件无 DB 记录靠 extra_whitelist 放行。
7. 幂等 import — 第二次返 existing（需待导路径有 ai_chat_session_files 行）。
8. 不回滚单条失败 — 一条 FILE_MISSING 不影响另一条 imported 落库。
9. uploaded_by=owner，不是管理员。
10. 操作日志 operator=当前管理员（g.current_user 自动解析）。
11. Ruling K — list_child_files 不写 DB（不调 record_session_files）；list_child_changes
   镜像 owner 端语义，ok=True 时调 record_session_files。

测试分两组：
- mock-DB（用 conftest `app`/`client`/`admin_headers`/`dev_headers`）：路由形状、
  分支覆盖、权限门、参数校验。patch 掉 routes.ai_batch_admin 下的工具函数，绝不
  碰真实 DB。
- real-DB（本文件自有 `real_client` fixture + `seeded_pair_2`）：真实落库断言
  （uploaded_by、operation_logs.operator_id、ai_chat_session_files.data_file_id）。
  `real_client` rebind 所有已导入模块的 get_db 回真实实现，与
  test_open_api_batch_integration.py 同模式。
"""
import os
import sys
import uuid
import shutil
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import db as _db_module  # noqa: E402
from db import get_db  # noqa: E402
from auth import create_token  # noqa: E402

BASE = '/ai/chat/admin/batches'

# 6 个新端点的视图函数名（url rule 末段）。
NEW_ENDPOINTS = {
    'list_child_files', 'preview_child_file', 'download_child_file',
    'import_child_files', 'list_child_changes', 'child_file_diff',
}


def _new_routes(app):
    """url_map 里 6 个新端点的规则。按视图函数名挑（比字符串匹配稳）。"""
    out = []
    for rule in app.url_map.iter_rules():
        name = (rule.endpoint or '').split('.')[-1]
        if name in NEW_ENDPOINTS:
            out.append(rule)
    return out


def _url(rule):
    """把路径参数替换为占位值，便于直接发请求。"""
    return (str(rule)
            .replace('<batch_id>', 'b-x')
            .replace('<sid>', 's-x'))


def _fake_sess(workspace=None, sid='s-x', owner='u-owner', status='completed'):
    """admin_get_child_session 的返回形状（与 utils.batch_repo 实现一致）。

    workspace 默认 None：list_child_files 端点把它当「无工作区」分支用。其余端点
    会显式传 'ws-fake' 或 tmp_path（safe_resolve 对不存在的根也只做规范化，不报错）。
    """
    return {'id': sid, 'status': status,
            'workspace_path': workspace, 'ownerUserId': owner}


# ============================================================================
# (1) 权限门参数化遍历 — 6 个新端点必挂 @require_permission_sse
# ============================================================================

def test_six_new_routes_registered(app):
    """防空集：参数化断言若零条规则会空转通过。"""
    routes = _new_routes(app)
    names = {(r.endpoint or '').split('.')[-1] for r in routes}
    assert names == NEW_ENDPOINTS, f'缺少端点: {NEW_ENDPOINTS - names}'


def test_every_new_file_route_rejects_authenticated_non_admin(app, client, dev_headers):
    """6 个新端点逐个打：已登录但无 admin 能力（developer 角色）一律 403。

    conftest 的 autouse fixture 已把 developer 预置为 admin_keys=set()，所以
    developer 走 require_permission_sse 的能力判定分支必落到 403。如果有人把
    装饰器换成 login_required 或删掉，这里立刻红。
    """
    leaked = []
    for rule in _new_routes(app):
        method = next(iter(rule.methods - {'HEAD', 'OPTIONS'}))
        url = _url(rule)
        kwargs = {'headers': dev_headers}
        if method == 'POST':
            kwargs['json'] = {'paths': []}
        resp = client.open(url, method=method, **kwargs)
        if resp.status_code != 403:
            leaked.append((method, url, resp.status_code))
    assert leaked == [], f'未鉴权的端点: {leaked}'


def test_download_route_accepts_access_token_query_param(app, client, admin_headers):
    """`require_permission_sse` 区别于 `require_permission` 的关键：浏览器 <a download>
    链接靠 ?access_token= 透传 JWT。断言这条 SSE 变体确实在这个端点生效（普通
    require_permission 在只有 query token 时会返 401）。
    """
    from unittest.mock import patch
    rule = next(r for r in _new_routes(app)
                if (r.endpoint or '').endswith('download_child_file'))
    token = admin_headers['Authorization'].split(' ', 1)[1]
    # 拿掉 Authorization 头，只用 query token
    with patch('routes.ai_batch_admin.admin_get_child_session', return_value=None):
        resp = client.get(f'{_url(rule)}?path=x.md&access_token={token}')
    # None session → 404，但能走到 handler 就证明 token 通过了能力门
    assert resp.status_code == 404, 'access_token query 鉴权没生效'


# ============================================================================
# (2-4)(mock-DB) list_child_files 分支：404 / workspace NULL / live-scan+augment
# ============================================================================

def test_list_files_404_when_session_missing(client, admin_headers):
    """admin_get_child_session 返回 None（sid 不属于该 batch 或不存在）→ 404 NOT_FOUND。"""
    from unittest.mock import patch
    with patch('routes.ai_batch_admin.admin_get_child_session', return_value=None):
        r = client.get(f'{BASE}/b-x/sessions/s-x/files', headers=admin_headers)
    assert r.status_code == 404
    assert r.get_json().get('code') == 'NOT_FOUND'


def test_list_files_empty_when_workspace_null(client, admin_headers):
    """归档后 workspace_path=NULL → 空列表（不是 400）。"""
    from unittest.mock import patch
    with patch('routes.ai_batch_admin.admin_get_child_session',
               return_value=_fake_sess(workspace=None)):
        r = client.get(f'{BASE}/b-x/sessions/s-x/files', headers=admin_headers)
    assert r.status_code == 200
    assert r.get_json() == {'files': [], 'truncated': False}


def test_list_files_passes_live_scan_to_augment(client, admin_headers):
    """live scan + augment 串联；truncated 透传；augment 用 sid 调（不调 owner）。"""
    from unittest.mock import patch
    files = [{'name': 'report.md', 'path': 'outputs/report.md',
              'dir': 'outputs', 'size': 42}]
    with patch('routes.ai_batch_admin.admin_get_child_session',
               return_value=_fake_sess(workspace='ws-fake', sid='s-1')), \
         patch('routes.ai_batch_admin.list_session_files',
               return_value=(files, True)) as ls, \
         patch('routes.ai_batch_admin.augment_with_data_file_id') as aug:
        r = client.get(f'{BASE}/b-x/sessions/s-1/files', headers=admin_headers)
    assert r.status_code == 200
    body = r.get_json()
    assert body['files'] == files
    assert body['truncated'] is True
    ls.assert_called_once_with('ws-fake')
    aug.assert_called_once_with('s-1', files)


def test_list_files_never_writes_session_files_table(client, admin_headers):
    """Ruling K：list_child_files 只读，绝不写 ai_chat_session_files。

    调一次 list_child_files，断言 record_session_files（git-changes 历史表维护
    入口）从头到尾没被调过。list_child_changes 才是写 DB 的那一侧。
    """
    from unittest.mock import patch
    with patch('routes.ai_batch_admin.admin_get_child_session',
               return_value=_fake_sess(workspace='ws-fake', sid='s-1')), \
         patch('routes.ai_batch_admin.list_session_files',
               return_value=([{'name': 'a', 'path': 'a', 'dir': 'workspace', 'size': 1}], False)), \
         patch('utils.workspace_changes.record_session_files') as rec:
        client.get(f'{BASE}/b-x/sessions/s-1/files', headers=admin_headers)
    rec.assert_not_called()


# ============================================================================
# (5)(mock-DB) preview 分支：NO_WORKSPACE / PATH_REQUIRED / BAD_PATH / FILE_NOT_FOUND
# ============================================================================

def test_preview_400_when_workspace_null(client, admin_headers):
    from unittest.mock import patch
    with patch('routes.ai_batch_admin.admin_get_child_session',
               return_value=_fake_sess(workspace=None)):
        r = client.get(f'{BASE}/b-x/sessions/s-x/files/preview?path=outputs/r.md',
                       headers=admin_headers)
    assert r.status_code == 400
    assert r.get_json().get('code') == 'NO_WORKSPACE'


def test_preview_400_when_path_missing(client, admin_headers):
    from unittest.mock import patch
    with patch('routes.ai_batch_admin.admin_get_child_session',
               return_value=_fake_sess(workspace='ws-fake')):
        r = client.get(f'{BASE}/b-x/sessions/s-x/files/preview', headers=admin_headers)
    assert r.status_code == 400
    assert r.get_json().get('code') == 'PATH_REQUIRED'


@pytest.mark.parametrize('bad_path', [
    '../../../etc/passwd',
    '/etc/passwd',
    '..\\..\\..\\windows\\system32',
])
def test_preview_400_on_bad_path(client, admin_headers, bad_path):
    """safe_resolve 越界（相对/绝对/Windows 绝对）→ 400 BAD_PATH。"""
    from unittest.mock import patch
    with patch('routes.ai_batch_admin.admin_get_child_session',
               return_value=_fake_sess(workspace='ws-fake')):
        r = client.get(f'{BASE}/b-x/sessions/s-x/files/preview',
                       query_string={'path': bad_path}, headers=admin_headers)
    assert r.status_code == 400
    assert r.get_json().get('code') == 'BAD_PATH'


def test_preview_404_when_file_not_on_disk(client, admin_headers, tmp_path):
    """safe_resolve OK 但 os.path.isfile False → 404 FILE_NOT_FOUND。"""
    from unittest.mock import patch
    with patch('routes.ai_batch_admin.admin_get_child_session',
               return_value=_fake_sess(workspace=str(tmp_path))):
        r = client.get(f'{BASE}/b-x/sessions/s-x/files/preview?path=outputs/missing.md',
                       headers=admin_headers)
    assert r.status_code == 404
    assert r.get_json().get('code') == 'FILE_NOT_FOUND'


def test_preview_returns_capped_content(client, admin_headers, tmp_path):
    """真实预览：read_file_preview 形状。"""
    from unittest.mock import patch
    f = tmp_path / 'r.md'
    f.write_text('# hello\n正文\n', encoding='utf-8')
    with patch('routes.ai_batch_admin.admin_get_child_session',
               return_value=_fake_sess(workspace=str(tmp_path))):
        r = client.get(f'{BASE}/b-x/sessions/s-x/files/preview?path=r.md',
                       headers=admin_headers)
    assert r.status_code == 200
    body = r.get_json()
    assert body.get('binary') is False
    assert 'hello' in body.get('content', '')


# ============================================================================
# (5)(mock-DB) download 分支
# ============================================================================

def test_download_400_when_workspace_null(client, admin_headers):
    from unittest.mock import patch
    with patch('routes.ai_batch_admin.admin_get_child_session',
               return_value=_fake_sess(workspace=None)):
        r = client.get(f'{BASE}/b-x/sessions/s-x/files/download?path=x.md',
                       headers=admin_headers)
    assert r.status_code == 400
    assert r.get_json().get('code') == 'NO_WORKSPACE'


@pytest.mark.parametrize('bad_path', [
    '../../../etc/passwd',
    '/etc/passwd',
    '..\\..\\..\\windows\\system32',
])
def test_download_400_on_bad_path(client, admin_headers, bad_path):
    """safe_resolve 越界 → 400 BAD_PATH，不论 workspace 是否真实存在。

    safe_resolve 对相对越界用 `relative_to` 检测，对绝对路径用 isabs 检测；
    根路径不需要真实存在（resolve 对不存在的路径只做规范化）。
    """
    from unittest.mock import patch
    with patch('routes.ai_batch_admin.admin_get_child_session',
               return_value=_fake_sess(workspace='ws-fake')):
        r = client.get(f'{BASE}/b-x/sessions/s-x/files/download',
                       query_string={'path': bad_path}, headers=admin_headers)
    assert r.status_code == 400
    assert r.get_json().get('code') == 'BAD_PATH'


def test_download_404_when_file_not_on_disk(client, admin_headers, tmp_path):
    from unittest.mock import patch
    with patch('routes.ai_batch_admin.admin_get_child_session',
               return_value=_fake_sess(workspace=str(tmp_path))):
        r = client.get(f'{BASE}/b-x/sessions/s-x/files/download?path=ghost.md',
                       headers=admin_headers)
    assert r.status_code == 404
    assert r.get_json().get('code') == 'FILE_NOT_FOUND'


def test_download_returns_file_bytes(client, admin_headers, tmp_path):
    """send_file 把真实文件内容原样回吐，download_name 取 basename。"""
    from unittest.mock import patch
    f = tmp_path / 'report.md'
    f.write_bytes(b'# report\nbody\n')
    with patch('routes.ai_batch_admin.admin_get_child_session',
               return_value=_fake_sess(workspace=str(tmp_path))):
        r = client.get(f'{BASE}/b-x/sessions/s-x/files/download?path=report.md',
                       headers=admin_headers)
    assert r.status_code == 200
    assert r.data == b'# report\nbody\n'
    # as_attachment 让 Content-Disposition 带 filename=report.md
    cd = r.headers.get('Content-Disposition', '')
    assert 'report.md' in cd


# ============================================================================
# (mock-DB) import 分支：404 / NO_WORKSPACE / PATHS_REQUIRED / TOO_MANY / 调用形
# ============================================================================

def test_import_404_when_session_missing(client, admin_headers):
    from unittest.mock import patch
    with patch('routes.ai_batch_admin.admin_get_child_session', return_value=None):
        r = client.post(f'{BASE}/b-x/sessions/s-x/files/import',
                        json={'paths': ['outputs/r.md']}, headers=admin_headers)
    assert r.status_code == 404
    assert r.get_json().get('code') == 'NOT_FOUND'


def test_import_400_when_workspace_null(client, admin_headers):
    from unittest.mock import patch
    with patch('routes.ai_batch_admin.admin_get_child_session',
               return_value=_fake_sess(workspace=None)):
        r = client.post(f'{BASE}/b-x/sessions/s-x/files/import',
                        json={'paths': ['outputs/r.md']}, headers=admin_headers)
    assert r.status_code == 400
    assert r.get_json().get('code') == 'NO_WORKSPACE'


def test_import_400_when_paths_missing_or_empty(client, admin_headers):
    from unittest.mock import patch
    sess = _fake_sess(workspace='ws-fake')
    with patch('routes.ai_batch_admin.admin_get_child_session', return_value=sess):
        r1 = client.post(f'{BASE}/b-x/sessions/s-x/files/import',
                         json={}, headers=admin_headers)
        r2 = client.post(f'{BASE}/b-x/sessions/s-x/files/import',
                         json={'paths': []}, headers=admin_headers)
        r3 = client.post(f'{BASE}/b-x/sessions/s-x/files/import',
                         headers=admin_headers)  # 无 body
    for r in (r1, r2, r3):
        assert r.status_code == 400
        assert r.get_json().get('code') == 'PATHS_REQUIRED'


def test_import_400_when_too_many_paths(client, admin_headers):
    """超过 MAX_IMPORT_PATHS（100）→ 400 TOO_MANY。"""
    from unittest.mock import patch
    from utils.session_file_import import MAX_IMPORT_PATHS
    sess = _fake_sess(workspace='ws-fake')
    paths = [f'p{i}.txt' for i in range(MAX_IMPORT_PATHS + 1)]
    with patch('routes.ai_batch_admin.admin_get_child_session', return_value=sess):
        r = client.post(f'{BASE}/b-x/sessions/s-x/files/import',
                        json={'paths': paths}, headers=admin_headers)
    assert r.status_code == 400
    assert r.get_json().get('code') == 'TOO_MANY'


def test_import_passes_live_paths_as_extra_whitelist_and_owner_as_uploaded_by(client, admin_headers):
    """关键 wiring 测：admin 端的 extra_whitelist = live-scan 集合；uploaded_by=owner
    （不是 admin id）；log_operation action/target_type/target_name 命中。"""
    from unittest.mock import patch
    sess = _fake_sess(workspace='ws-fake', sid='s-1', owner='u-owner-123')
    live = [{'name': 'report.md', 'path': 'outputs/report.md', 'dir': 'outputs', 'size': 1}]
    results = [{'path': 'outputs/report.md', 'status': 'imported',
                'file': {'id': 'df-1', 'name': 'report.md', 'size': 1,
                         'mimeType': 'text/markdown', 'url': '/api/data-files/df-1/download'}}]
    with patch('routes.ai_batch_admin.admin_get_child_session', return_value=sess), \
         patch('routes.ai_batch_admin.list_session_files',
               return_value=(live, False)) as ls, \
         patch('routes.ai_batch_admin.import_recorded_files',
               return_value=results) as imp, \
         patch('routes.ai_batch_admin.log_operation') as logop:
        r = client.post(f'{BASE}/b-2/sessions/s-1/files/import',
                        json={'paths': ['outputs/report.md']}, headers=admin_headers)
    assert r.status_code == 200
    # extra_whitelist 必须 = live-scan 的 path 集合（让 outputs/ 文件绕过 .gitignore）
    kw = imp.call_args.kwargs
    assert kw['extra_whitelist'] == {'outputs/report.md'}
    assert kw['uploaded_by'] == 'u-owner-123'  # owner，不是 admin
    assert imp.call_args.args[0:2] == ('s-1', 'ws-fake')  # session_id, workspace
    # 顺手维护 ai_chat_session_files：NOT called（import 端写的是另一个 util，
    # 见 import_recorded_files → set_data_file_id；这里不应调 record_session_files）
    ls.assert_called_once_with('ws-fake')
    # 审计：写管理员为 operator
    logop.assert_called_once()
    args = logop.call_args.args
    assert args[0] == 'create'           # action
    assert args[1] == 'data_file'        # target_type
    assert args[2] == 'df-1'             # target_id（imported_ids 拼接）
    assert 'b-2' in args[3] and 's-1' in args[3]   # target_name
    assert 'u-owner-123' in args[4]      # description 含归属


def test_import_log_counts_imported_and_existing_not_errors(client, admin_headers):
    """imported_ids 只统计 status in (imported, existing) 且有 file 的；error 项不计。
    即便全部失败（imported_ids 空），log_operation 也必须执行到（target_id=None），
    否则就是「管理员代改他人数据无痕」。"""
    from unittest.mock import patch
    sess = _fake_sess(workspace='ws-fake', sid='s-1', owner='u-owner')
    results = [
        {'path': 'a.md', 'status': 'imported', 'file': {'id': 'df-a'}},
        {'path': 'b.md', 'status': 'existing', 'file': {'id': 'df-b'}},
        {'path': 'c.md', 'error': '文件不存在', 'code': 'FILE_MISSING'},  # 不计
    ]
    with patch('routes.ai_batch_admin.admin_get_child_session', return_value=sess), \
         patch('routes.ai_batch_admin.list_session_files', return_value=([], False)), \
         patch('routes.ai_batch_admin.import_recorded_files', return_value=results), \
         patch('routes.ai_batch_admin.log_operation') as logop:
        r = client.post(f'{BASE}/b-2/sessions/s-1/files/import',
                        json={'paths': ['a.md', 'b.md', 'c.md']}, headers=admin_headers)
    assert r.status_code == 200
    assert logop.call_args.args[2] == 'df-a;df-b'   # 只拼成功的
    assert '2 个产出文件' in logop.call_args.args[4]


def test_import_log_still_runs_when_all_fail(client, admin_headers):
    """全部失败：imported_ids 空，但 log_operation 仍执行（target_id=None）。
    否则管理员代改他人数据无痕出问题无法追溯。"""
    from unittest.mock import patch
    sess = _fake_sess(workspace='ws-fake', sid='s-1', owner='u-owner')
    with patch('routes.ai_batch_admin.admin_get_child_session', return_value=sess), \
         patch('routes.ai_batch_admin.list_session_files', return_value=([], False)), \
         patch('routes.ai_batch_admin.import_recorded_files',
               return_value=[{'path': 'x.md', 'error': '缺', 'code': 'FILE_MISSING'}]), \
         patch('routes.ai_batch_admin.log_operation') as logop:
        client.post(f'{BASE}/b-2/sessions/s-1/files/import',
                    json={'paths': ['x.md']}, headers=admin_headers)
    logop.assert_called_once()
    assert logop.call_args.args[2] is None   # target_id
    assert '0 个产出文件' in logop.call_args.args[4]


# ============================================================================
# (Ruling K)(mock-DB) changes 分支：404 / NULL / record_session_files ok 分叉
# ============================================================================

def test_changes_404_when_session_missing(client, admin_headers):
    from unittest.mock import patch
    with patch('routes.ai_batch_admin.admin_get_child_session', return_value=None):
        r = client.get(f'{BASE}/b-x/sessions/s-x/changes', headers=admin_headers)
    assert r.status_code == 404
    assert r.get_json().get('code') == 'NOT_FOUND'


def test_changes_empty_when_workspace_null(client, admin_headers):
    from unittest.mock import patch
    with patch('routes.ai_batch_admin.admin_get_child_session',
               return_value=_fake_sess(workspace=None)):
        r = client.get(f'{BASE}/b-x/sessions/s-x/changes', headers=admin_headers)
    assert r.status_code == 200
    assert r.get_json() == {'changes': [], 'truncated': False, 'ok': True}


def test_changes_records_session_files_when_ok(client, admin_headers):
    """Ruling K 的正侧：list_child_changes 在 ok=True 时镜像 owner 端语义，调用
    record_session_files 落 ai_chat_session_files 记录。git-changes 历史表维护
    入口在这里，不在 list_child_files。"""
    from unittest.mock import patch
    changes = [{'path': 'src/a.py', 'status': 'added'}]
    with patch('routes.ai_batch_admin.admin_get_child_session',
               return_value=_fake_sess(workspace='ws-fake', sid='s-1')), \
         patch('routes.ai_batch_admin.git_changes',
               return_value=(changes, False, True)) as gc, \
         patch('utils.workspace_changes.record_session_files') as rec:
        r = client.get(f'{BASE}/b-x/sessions/s-1/changes', headers=admin_headers)
    assert r.status_code == 200
    body = r.get_json()
    assert body['changes'] == changes and body['ok'] is True
    gc.assert_called_once_with('ws-fake')
    rec.assert_called_once_with('s-1', changes)


def test_changes_does_not_record_when_not_ok(client, admin_headers):
    """git status 失败（ok=False）→ 不写 DB，避免用一份坏快照覆盖历史记录。

    与 owner 端 routes/ai_chat.py::list_changes 同分支语义。
    """
    from unittest.mock import patch
    with patch('routes.ai_batch_admin.admin_get_child_session',
               return_value=_fake_sess(workspace='ws-fake', sid='s-1')), \
         patch('routes.ai_batch_admin.git_changes',
               return_value=([], False, False)), \
         patch('utils.workspace_changes.record_session_files') as rec:
        r = client.get(f'{BASE}/b-x/sessions/s-1/changes', headers=admin_headers)
    assert r.status_code == 200
    assert r.get_json()['ok'] is False
    rec.assert_not_called()


# ============================================================================
# (5)(mock-DB) diff 分支
# ============================================================================

def test_diff_404_when_session_missing(client, admin_headers):
    from unittest.mock import patch
    with patch('routes.ai_batch_admin.admin_get_child_session', return_value=None):
        r = client.get(f'{BASE}/b-x/sessions/s-x/files/diff?path=a.md',
                       headers=admin_headers)
    assert r.status_code == 404
    assert r.get_json().get('code') == 'NOT_FOUND'


def test_diff_400_when_workspace_null(client, admin_headers):
    from unittest.mock import patch
    with patch('routes.ai_batch_admin.admin_get_child_session',
               return_value=_fake_sess(workspace=None)):
        r = client.get(f'{BASE}/b-x/sessions/s-x/files/diff?path=a.md',
                       headers=admin_headers)
    assert r.status_code == 400
    assert r.get_json().get('code') == 'NO_WORKSPACE'


def test_diff_400_when_path_missing(client, admin_headers):
    from unittest.mock import patch
    with patch('routes.ai_batch_admin.admin_get_child_session',
               return_value=_fake_sess(workspace='ws-fake')):
        r = client.get(f'{BASE}/b-x/sessions/s-x/files/diff', headers=admin_headers)
    assert r.status_code == 400
    assert r.get_json().get('code') == 'PATH_REQUIRED'


def test_diff_400_on_bad_path(client, admin_headers):
    from unittest.mock import patch
    with patch('routes.ai_batch_admin.admin_get_child_session',
               return_value=_fake_sess(workspace='ws-fake')):
        r = client.get(f'{BASE}/b-x/sessions/s-x/files/diff',
                       query_string={'path': '../../../etc/passwd'}, headers=admin_headers)
    assert r.status_code == 400
    assert r.get_json().get('code') == 'BAD_PATH'


def test_diff_passes_through_file_diff_result(client, admin_headers):
    from unittest.mock import patch
    expected = {'status': 'modified', 'diff': '@@ diff @@', 'truncated': False}
    with patch('routes.ai_batch_admin.admin_get_child_session',
               return_value=_fake_sess(workspace='ws-fake', sid='s-1')), \
         patch('routes.ai_batch_admin.file_diff', return_value=expected) as fd:
        r = client.get(f'{BASE}/b-x/sessions/s-1/files/diff?path=src/a.py',
                       headers=admin_headers)
    assert r.status_code == 200
    assert r.get_json() == expected
    fd.assert_called_once_with('ws-fake', 'src/a.py')


# ============================================================================
# Real-DB integration tests
# ============================================================================

@pytest.fixture
def real_client():
    """真实 DB 的 test client：rebind 所有已导入模块的 get_db 回真实实现（不 mock）。

    与 conftest 的 mock-DB `client` 区分。conftest 的 autouse
    `_reset_and_prime_permission_cache` 仍然生效（admin 角色 = superuser），所以
    `admin_headers`（role=admin）走能力门不必查 DB；`_rebind_module_get_db_to_real`
    也已经把少数模块 rebind 了，这里补上 routes.*/utils.* 全集，让 save_workspace_file
    / log_operation / admin_get_child_session / import_recorded_files 真打 DB。
    与 test_open_api_batch_integration.py 同模式。
    """
    from app import app as flask_app
    for mod_name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        if getattr(mod, 'get_db', None) is not None and (
            mod_name.startswith('routes.') or mod_name.startswith('utils.')
            or mod_name == 'auth'
        ):
            try:
                mod.get_db = _db_module.get_db
            except (AttributeError, TypeError):
                pass
    flask_app.config['TESTING'] = True
    return flask_app.test_client()


@pytest.fixture
def seeded_pair_2():
    """真实 DB + 真实临时工作区：一个 owner 用户、一个批、两个子任务。

    - sid1：workspace_path=<tmpdir>，盘上有 outputs/report.md + outputs/extra.md；
      ai_chat_session_files 预置一行 (sid1, 'outputs/report.md', status='added',
      data_file_id=NULL)。预置该行是为了启用幂等 + data_file_id 回填路径
      （set_data_file_id 是 plain UPDATE，对没有行的路径是 no-op；这里模拟
      「该文件曾被 git 扫描记录过」的真实前置条件）。
    - sid2：workspace_path=NULL，机器归档后的状态。

    清理：删 data_files 里的测试行 + 从盘上 rmtree 对应 storage dir、删
    operation_logs 里由本测试插入的审计行、删 sessions（级联清 ai_chat_session_files）、
    删 batch、删 owner 用户、rmtree tmpdir。
    """
    owner = 'u-seed-' + uuid.uuid4().hex[:8]
    bid = 'b-seed-' + uuid.uuid4().hex[:8]
    sid1 = 's-seed1-' + uuid.uuid4().hex[:8]
    sid2 = 's-seed2-' + uuid.uuid4().hex[:8]
    tmpdir = tempfile.mkdtemp(prefix='seed-pair-')
    os.makedirs(os.path.join(tmpdir, 'outputs'), exist_ok=True)
    report_md = os.path.join(tmpdir, 'outputs', 'report.md')
    extra_md = os.path.join(tmpdir, 'outputs', 'extra.md')
    with open(report_md, 'w', encoding='utf-8') as f:
        f.write('# 报告\n这是 admin 代导入要落库的内容。\n')
    with open(extra_md, 'w', encoding='utf-8') as f:
        f.write('# extra\n白名单扩展正向测试用：无 DB 行、靠 extra_whitelist 放行。\n')

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (id, username, password_hash, display_name, role) "
                "VALUES (%s,%s,'x',%s,'developer')",
                (owner, 'seed-' + owner[:16], 'Seed Owner',))
            cur.execute(
                "INSERT INTO ai_chat_batches (id,user_id,name,prompt,status,total) "
                "VALUES (%s,%s,'seed-batch','p','completed',2)",
                (bid, owner))
            cur.execute(
                "INSERT INTO ai_chat_sessions "
                "  (id,user_id,status,batch_id,batch_seq,batch_input_file,workspace_path) "
                "VALUES (%s,%s,'completed',%s,0,'uploads/a.txt',%s)",
                (sid1, owner, bid, tmpdir))
            cur.execute(
                "INSERT INTO ai_chat_sessions "
                "  (id,user_id,status,batch_id,batch_seq,batch_input_file,workspace_path) "
                "VALUES (%s,%s,'completed',%s,1,'uploads/b.txt',NULL)",
                (sid2, owner, bid))
            # 预置一行 ai_chat_session_files（让幂等 + 回填路径可走）。
            cur.execute(
                "INSERT INTO ai_chat_session_files (session_id, path, status) "
                "VALUES (%s, %s, 'added')",
                (sid1, 'outputs/report.md'))
        conn.commit()
    yield {'owner': owner, 'batch_id': bid, 'sid1': sid1, 'sid2': sid2,
           'tmpdir': tmpdir}

    # ---- teardown：测试可能额外插了 data_files 行 + 操作日志 + extra/deleted
    # session_files 行，全部清掉。顺序：先 data_files（可能有 file 落盘）+ 操作日志
    # → 再 sessions（FK 级联清 ai_chat_session_files）→ batch → user → tmpdir。
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                # data_files 这次测试导入的：列出来要同时删盘上文件
                cur.execute(
                    "SELECT id, storage_path FROM data_files WHERE uploaded_by = %s",
                    (owner,))
                rows = cur.fetchall()
                for _id, storage_path in rows:
                    if storage_path:
                        d = os.path.dirname(storage_path)
                        if os.path.isdir(d):
                            shutil.rmtree(d, ignore_errors=True)
                cur.execute("DELETE FROM data_files WHERE uploaded_by = %s", (owner,))
                # operation_logs：本次 admin 代导入写的审计行（target_name 含 batch_id）
                cur.execute(
                    "DELETE FROM operation_logs WHERE operator_id = 'user-admin' "
                    "  AND target_type = 'data_file' AND target_name LIKE %s",
                    (f'批任务 {bid}%',))
                # 测试可能 add 过的 extra session_files 行（如 deleted.md）
                cur.execute("DELETE FROM ai_chat_session_files WHERE session_id IN (%s,%s)",
                            (sid1, sid2))
                cur.execute("DELETE FROM ai_chat_sessions WHERE id IN (%s,%s)",
                            (sid1, sid2))
                cur.execute("DELETE FROM ai_chat_batches WHERE id = %s", (bid,))
                cur.execute("DELETE FROM users WHERE id = %s", (owner,))
            conn.commit()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _df_uploaded_by(file_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT uploaded_by FROM data_files WHERE id = %s", (file_id,))
            row = cur.fetchone()
    return row[0] if row else None


def test_list_child_files_cross_user_visible(real_client, admin_headers, seeded_pair_2):
    """管理员看其他人子任务的产出文件（owner=developer，admin 跨用户可见）。"""
    f = seeded_pair_2
    r = real_client.get(f'{BASE}/{f["batch_id"]}/sessions/{f["sid1"]}/files',
                        headers=admin_headers)
    assert r.status_code == 200
    files = r.get_json()['files']
    paths = {x['path'] for x in files}
    assert 'outputs/report.md' in paths
    assert 'outputs/extra.md' in paths
    # augment 加的 dataFileId 字段一定在（即便预置行 data_file_id 还是 NULL）
    assert 'dataFileId' in files[0]


def test_list_child_files_404_for_wrong_batch(real_client, admin_headers, seeded_pair_2):
    """sid 不属于该 batch_id → admin_get_child_session 返 None → 404。

    不做这层校验，路径就成了「用任意 batchId 读任意会话」的通道。
    """
    f = seeded_pair_2
    # 用一个根本不存在的 sid（同时不属本 batch）
    r = real_client.get(f'{BASE}/{f["batch_id"]}/sessions/sess-NOT-IN-DB/files',
                        headers=admin_headers)
    assert r.status_code == 404
    assert r.get_json().get('code') == 'NOT_FOUND'


def test_list_child_files_empty_when_workspace_null(real_client, admin_headers, seeded_pair_2):
    """sid2 workspace_path=NULL → 空列表（preview/download/import 才返 400）。"""
    f = seeded_pair_2
    r = real_client.get(f'{BASE}/{f["batch_id"]}/sessions/{f["sid2"]}/files',
                        headers=admin_headers)
    assert r.status_code == 200
    assert r.get_json() == {'files': [], 'truncated': False}


def test_preview_400_when_workspace_null_real(real_client, admin_headers, seeded_pair_2):
    f = seeded_pair_2
    r = real_client.get(
        f'{BASE}/{f["batch_id"]}/sessions/{f["sid2"]}/files/preview?path=outputs/x.md',
        headers=admin_headers)
    assert r.status_code == 400
    assert r.get_json().get('code') == 'NO_WORKSPACE'


def test_download_404_when_workspace_null(real_client, admin_headers, seeded_pair_2):
    f = seeded_pair_2
    r = real_client.get(
        f'{BASE}/{f["batch_id"]}/sessions/{f["sid2"]}/files/download?path=x.md',
        headers=admin_headers)
    assert r.status_code == 400
    assert r.get_json().get('code') == 'NO_WORKSPACE'


@pytest.mark.parametrize('bad_path', [
    '../../../etc/passwd',
    '/etc/passwd',
    '..\\..\\..\\windows\\system32',
])
def test_download_rejects_path_traversal(real_client, admin_headers, seeded_pair_2, bad_path):
    """真实 workspace 下，越界路径一律 400 BAD_PATH（safe_resolve 抛 WorkspacePathError）。"""
    f = seeded_pair_2
    r = real_client.get(
        f'{BASE}/{f["batch_id"]}/sessions/{f["sid1"]}/files/download',
        query_string={'path': bad_path}, headers=admin_headers)
    assert r.status_code == 400
    assert r.get_json().get('code') == 'BAD_PATH'


def test_changes_returns_empty_when_no_repo(real_client, admin_headers, seeded_pair_2):
    """tmpdir 没 .git → git_changes 返 ([], False, ok=True)（没 repo 是合法「无变更」）。"""
    f = seeded_pair_2
    r = real_client.get(f'{BASE}/{f["batch_id"]}/sessions/{f["sid1"]}/changes',
                        headers=admin_headers)
    assert r.status_code == 200
    body = r.get_json()
    assert body['ok'] is True
    assert body['changes'] == []
    assert body['truncated'] is False


# ---- (6)(9)(10) 白名单扩展 + uploaded_by=owner + operator=admin ---------------

def test_admin_import_outputs_file_passes_live_whitelist(real_client, admin_headers, seeded_pair_2):
    """outputs/extra.md 没有任何 ai_chat_session_files 行（被 .gitignore 屏蔽不入
    DB）——owner 端 import 会被 NOT_RECORDED 拒掉；admin 端靠 extra_whitelist 放行。

    这条钉住 admin 端与 owner 端的**差异**：admin 给 live-scan 集合开白名单，让
    outputs/ 文件「列得到就导得到」。若有人日后「统一一下」让两边都仅 DB 白名单，
    这条会红，admin 看得到但导不出的反直觉 bug 就回不来了。
    """
    f = seeded_pair_2
    r = real_client.post(
        f'{BASE}/{f["batch_id"]}/sessions/{f["sid1"]}/files/import',
        json={'paths': ['outputs/extra.md']}, headers=admin_headers)
    assert r.status_code == 200
    results = r.get_json()['results']
    assert results[0]['status'] == 'imported'
    # uploaded_by = 原 owner，不是 admin
    assert _df_uploaded_by(results[0]['file']['id']) == f['owner']


def test_admin_import_uploaded_by_is_owner_not_admin(real_client, admin_headers, seeded_pair_2):
    """归属契约：导入落 data_files 时 uploaded_by=批任务原 owner，绝非管理员本人。

    一个只断言 200 的测试对此无感——传成管理员 id 会让这批文件在「我的文件」里
    出现，归属错位、备份/还原范围跟着错。所以这条单独验 DB 列值。
    """
    f = seeded_pair_2
    r = real_client.post(
        f'{BASE}/{f["batch_id"]}/sessions/{f["sid1"]}/files/import',
        json={'paths': ['outputs/report.md']}, headers=admin_headers)
    assert r.status_code == 200
    file_id = r.get_json()['results'][0]['file']['id']
    assert _df_uploaded_by(file_id) == f['owner']
    # 双保险：admin 自己的 id 是 conftest 的 'user-admin'
    assert _df_uploaded_by(file_id) != 'user-admin'


def test_admin_import_operator_log_is_admin_user(real_client, admin_headers, seeded_pair_2):
    """审计契约：operation_logs.operator_id = 当前管理员（g.current_user.userId），
    不是 owner。log_operation 内部自动从 g.current_user 取 operator_id，所以路由
    不传 operator=参数；这条验证 g.current_user 在 require_permission_sse 中真被设上。
    """
    f = seeded_pair_2
    real_client.post(
        f'{BASE}/{f["batch_id"]}/sessions/{f["sid1"]}/files/import',
        json={'paths': ['outputs/report.md']}, headers=admin_headers)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT operator_id, target_type, target_name, description "
                "  FROM operation_logs "
                " WHERE operator_id = 'user-admin' AND target_type = 'data_file' "
                "   AND target_name LIKE %s "
                " ORDER BY created_at DESC LIMIT 1", (f'批任务 {f["batch_id"]}%',))
            row = cur.fetchone()
    assert row is not None, '缺少 admin 代导入的审计行'
    assert row[0] == 'user-admin'                      # operator
    assert row[1] == 'data_file'                       # target_type
    assert f['sid1'] in row[2]                          # target_name 含 sid
    assert f['owner'] in row[3]                         # description 含 owner 归属


# ---- (7)(8) 幂等 + 不回滚 ----------------------------------------------------

def test_admin_import_sets_data_file_id_on_session_files(real_client, admin_headers, seeded_pair_2):
    """导入成功后回填 ai_chat_session_files.data_file_id（spec §2.5：让下次走 DB
    白名单的幂等快路径）。预置行 data_file_id=NULL，导入后必须 NOT NULL。"""
    f = seeded_pair_2
    real_client.post(
        f'{BASE}/{f["batch_id"]}/sessions/{f["sid1"]}/files/import',
        json={'paths': ['outputs/report.md']}, headers=admin_headers)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT data_file_id FROM ai_chat_session_files "
                " WHERE session_id = %s AND path = %s",
                (f['sid1'], 'outputs/report.md'))
            row = cur.fetchone()
    assert row is not None
    assert row[0] is not None


def test_admin_import_idempotent_second_returns_existing(real_client, admin_headers, seeded_pair_2):
    """同一文件导两次：第二次返 existing；DB 只入库一次（data_file_id 不变）。"""
    f = seeded_pair_2
    url = f'{BASE}/{f["batch_id"]}/sessions/{f["sid1"]}/files/import'
    r1 = real_client.post(url, json={'paths': ['outputs/report.md']}, headers=admin_headers)
    assert r1.status_code == 200
    fid1 = r1.get_json()['results'][0]['file']['id']

    r2 = real_client.post(url, json={'paths': ['outputs/report.md']}, headers=admin_headers)
    assert r2.status_code == 200
    res2 = r2.get_json()['results'][0]
    assert res2['status'] == 'existing'
    assert res2['file']['id'] == fid1   # 复用同一个 data_files 行，不另建

    # DB: 该文件只入库一次（uploaded_by=owner 的 data_files 行只有 1 条）
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM data_files WHERE uploaded_by = %s",
                        (f['owner'],))
            assert cur.fetchone()[0] == 1


def test_admin_import_partial_failure_does_not_rollback(real_client, admin_headers, seeded_pair_2):
    """3 个 path：撤销 report.md（预置行+盘上文件）→ imported；deleted.md（预置行+盘上
    无文件）→ FILE_MISSING。成功的 row 必须真在 DB 里（不回滚）。

    本测试在 sid1 上临时加一行 (deleted.md, status='added') 模拟「曾被 git 扫描记
    录、后来文件被删」的状态。fixture teardown 会清掉。
    """
    f = seeded_pair_2
    # 临时补一行：deleted.md 在 DB 记录里，但盘上没有
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ai_chat_session_files (session_id, path, status) "
                "VALUES (%s, %s, 'added')",
                (f['sid1'], 'outputs/deleted.md'))
        conn.commit()

    r = real_client.post(
        f'{BASE}/{f["batch_id"]}/sessions/{f["sid1"]}/files/import',
        json={'paths': ['outputs/report.md', 'outputs/deleted.md']},
        headers=admin_headers)
    assert r.status_code == 200
    results = r.get_json()['results']
    by_path = {x['path']: x for x in results}
    # report.md：预置行+盘上文件 → imported 或 existing（之前没导过 → imported）
    assert by_path['outputs/report.md']['status'] in ('imported', 'existing')
    # deleted.md：DB 有记录但盘上无 → FILE_MISSING（不是 NOT_RECORDED）
    assert by_path['outputs/deleted.md'].get('code') == 'FILE_MISSING'
    # 已导入的 row 真在 DB 里（不回滚成功的）
    fid = by_path['outputs/report.md']['file']['id']
    assert _df_uploaded_by(fid) == f['owner']