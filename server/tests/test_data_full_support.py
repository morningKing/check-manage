"""族H 评论/时间线/备份 —— L2 live-server API 层（TD-H01–H05）。

契约锚点（计划④ Global Constraints #7–#9）：
- 评论（routes/comments.py 实读）：POST /comments/<c>/<rid> 空白内容 →
  400 「评论内容不能为空」（:38）；PUT/DELETE /comments/<cid> 为
  author-or-admin——admin 可改/删他人评论（:83/:102），非作者非 admin →
  403 「无权编辑此评论」/「无权删除此评论」；不校验记录存在。
- 时间线（routes/timeline.py:18-76 实读）：GET /timeline/<c>/<rid> 把
  record_comments（不过滤分支）与 operation_logs（按用户当前分支过滤，
  本族用例全在 main）合并，按 timestamp 排序返回；写侧 = dynamic
  create/update/delete 里的 log_operation。
- 备份（routes/backups.py 实读，全端点 admin.backup）：GET /backups
  返回 JSON 数组（:45-57，不是 {backups:[]}）；GET /backups/<id>/download
  二进制 ZIP（PK 魔数）；settings 路由 GET/PUT 均为 /backups/settings
  （:170-193，brief 的探测式写法已按实读定稿）。
  红线：绝不调 /backups/<id>/restore 与 /backups/factory-reset。
  已知产品缺陷（2026-10-07 实测定档，H04）：POST /backups 全量导出在
  ai_execution_usage 表上必然 500——cost 列 NUMERIC（dev 库 319 行非空），
  utils/backup._serialize_value（:476-484）未处理 Decimal，json.dumps 无
  default= → TypeError → 500「备份失败: Object of type Decimal is not JSON
  serializable」。H04 按实测行为定档并断言缺陷指纹，修复后需改回 201 契约。

清理约定：
- record_comments 无 FK（init_db.py:509-522），删记录不级联删评论 →
  各用例把自建的评论显式 DELETE；
- H02 的探针用户/角色 finally 删（沿族G G03 模式）；
- H04 因建备份被上述缺陷阻断，不产生任何备份行/文件（备份行在 ZIP 完成
  后才落库，实测验证），无 finally DELETE 项；
- H05 settings 往返后回写原值，不改动 dev 定时备份配置。
"""
import os
import time
import uuid

import pytest

import data_full_live as live

pytestmark = pytest.mark.data_full

NAME = {'id': 'f1', 'label': '名称', 'fieldName': 'name',
        'controlType': 'text', 'required': True, 'order': 1}


@pytest.fixture(scope='module')
def admin():
    if not live.server_up():
        pytest.skip('dev 栈未启动')
    return live.login()


@pytest.fixture(scope='module')
def pageh(admin):
    page = live.make_page(admin, 'H', 'support', fields=[NAME])
    yield page
    live.drop_page(admin, page)


def _rec(admin, collection, name):
    r = live.api('POST', f'/{collection}', admin, {'id': uuid.uuid4().hex, 'name': name})
    assert r.status_code == 201
    return r.json()


def _comment(admin, collection, rid, content):
    r = live.api('POST', f'/comments/{collection}/{rid}', admin, {'content': content})
    assert r.status_code == 201, f'{r.status_code} {r.text[:300]}'
    return r.json()['id']


def _second_user(admin):
    """建 write 权限探针角色+用户并登录，返回 (role_id, headers, user_id)。"""
    role = live.api('POST', '/roles', admin,
                    {'name': f"DTEST-H-role-{uuid.uuid4().hex[:6]}",
                     'defaultPageAccess': 'write'})
    assert role.status_code == 201, f'role create: {role.status_code} {role.text}'
    role_id = role.json()['id']
    uname = f"dtest_h_user_{uuid.uuid4().hex[:6]}"
    u = live.api('POST', '/users', admin,
                 {'username': uname, 'password': 'Dtest#12345',
                  'displayName': 'H 探针', 'role': role_id})
    assert u.status_code == 201, f'user create: {u.status_code} {u.text}'
    uid = u.json()['id']
    login = live.api('POST', '/auth/login', None,
                     {'username': uname, 'password': 'Dtest#12345'})
    assert login.status_code == 200, f'probe login: {login.status_code}'
    headers = {'Authorization': f"Bearer {login.json()['token']}"}
    return role_id, headers, uid


def test_td_h01_comment_crud_and_empty_rejected(admin, pageh):
    rec = _rec(admin, pageh['collection'], '评论宿主')
    cid = _comment(admin, pageh['collection'], rec['id'], 'DTEST 评论甲')
    lst = live.api('GET', f"/comments/{pageh['collection']}/{rec['id']}", admin)
    assert lst.status_code == 200
    assert any(c['id'] == cid for c in lst.json())
    put = live.api('PUT', f'/comments/{cid}', admin, {'content': 'DTEST 评论改'})
    assert put.status_code < 300
    empty = live.api('POST', f"/comments/{pageh['collection']}/{rec['id']}", admin,
                     {'content': '   '})
    assert empty.status_code == 400  # 评论内容不能为空（comments.py:38）
    dele = live.api('DELETE', f'/comments/{cid}', admin)
    assert dele.status_code < 300
    after = live.api('GET', f"/comments/{pageh['collection']}/{rec['id']}", admin)
    assert all(c['id'] != cid for c in after.json())  # 删除即从列表消失
    # 契约#9：评论不校验记录存在（comments.py add_comment 直接 INSERT）——
    # 对不存在的 record_id 仍 201，落行后按 id 删掉
    orphan = live.api('POST',
                      f"/comments/{pageh['collection']}/no-such-record", admin,
                      {'content': 'DTEST 评论孤儿宿主'})
    assert orphan.status_code == 201, f'{orphan.status_code} {orphan.text[:300]}'
    orphan_del = live.api('DELETE', f"/comments/{orphan.json()['id']}", admin)
    assert orphan_del.status_code < 300, (
        f'{orphan_del.status_code} {orphan_del.text[:300]}')


def test_td_h02_comment_permission_author_vs_admin(admin, pageh):
    rec = _rec(admin, pageh['collection'], '权限宿主')
    role_id, user_headers, uid = None, None, None
    admin_cid, cid = None, None
    try:
        role_id, user_headers, uid = _second_user(admin)
        # 非作者非 admin 改 admin 的评论 → 403 无权编辑此评论
        admin_cid = _comment(admin, pageh['collection'], rec['id'], 'DTEST admin 评论')
        denied = live.api('PUT', f'/comments/{admin_cid}', user_headers,
                          {'content': '越权改'})
        assert denied.status_code == 403, f'{denied.status_code} {denied.text[:300]}'
        assert '无权编辑此评论' in denied.json()['error']
        # 作者可改自己的
        own = live.api('POST', f"/comments/{pageh['collection']}/{rec['id']}",
                       user_headers, {'content': '他人评论'})
        assert own.status_code == 201, f'{own.status_code} {own.text[:300]}'
        cid = own.json()['id']
        edit = live.api('PUT', f'/comments/{cid}', user_headers, {'content': '自改'})
        assert edit.status_code < 300
        # author-or-admin 定论（comments.py:83）：admin 改他人评论 → 200 且内容生效
        admin_edit = live.api('PUT', f'/comments/{cid}', admin, {'content': 'admin 强改'})
        assert admin_edit.status_code == 200, f'{admin_edit.status_code} {admin_edit.text[:300]}'
        assert admin_edit.json()['content'] == 'admin 强改'
        # admin 删他人评论 → <300（comments.py:102）
        admin_del = live.api('DELETE', f'/comments/{cid}', admin)
        assert admin_del.status_code < 300
        cid = None  # 已删，finally 不再补刀
    finally:
        if cid:
            live.api('DELETE', f'/comments/{cid}', admin)
        if admin_cid:
            live.api('DELETE', f'/comments/{admin_cid}', admin)
        if uid:
            live.api('DELETE', f'/users/{uid}', admin)
        if role_id:
            live.api('DELETE', f'/roles/{role_id}', admin)


def test_td_h03_timeline_merges_comment_and_change(admin, pageh):
    rec = _rec(admin, pageh['collection'], '时间线宿主')
    upd = live.api('PUT', f"/{pageh['collection']}/{rec['id']}", admin,
                   {'name': '时间线宿主改', '_version': rec['_version']})
    assert upd.status_code < 300, f'{upd.status_code} {upd.text[:300]}'
    cid = _comment(admin, pageh['collection'], rec['id'], 'DTEST-H-时间线评论')
    try:
        t = live.api('GET', f"/timeline/{pageh['collection']}/{rec['id']}", admin)
        assert t.status_code == 200, f'{t.status_code} {t.text[:300]}'
        entries = t.json()
        types = {e['type'] for e in entries}
        assert 'comment' in types and ({'change', 'statusChange'} & types)
        assert any(e.get('content') == 'DTEST-H-时间线评论'
                   for e in entries if e['type'] == 'comment')
        ts_list = [e['timestamp'] for e in entries]
        assert ts_list == sorted(ts_list)  # 按 timestamp 排序
    finally:
        live.api('DELETE', f'/comments/{cid}', admin)


def test_td_h04_backup_list_download_and_create_defect(admin):
    """备份 建档缺陷定档 + 列表 + 下载（delete 腿被缺陷阻断，见下）。

    产品缺陷定档（2026-10-07 实测，按边界规则不改产品代码，DONE_WITH_CONCERNS
    上报）：POST /backups 全量导出必 500——BACKUP_TABLES 含 ai_execution_usage，
    其 cost 列为 NUMERIC（dev 库 casemanage 319 行非空，样例 Decimal('0.0042')），
    utils/backup._serialize_value（:476-484）未处理 Decimal 且 json.dumps
    （:562 / :805）无 default= → TypeError「Object of type Decimal is not JSON
    serializable」，路由捕获后回 500「备份失败: …」（backups.py:79-80）。
    失败不留备份行（备份行在 ZIP 完成后才 INSERT，实测验证无残留行），仅在
    server/backups 留 .tmp-backup-<id>-*.json 碎片——本用例断言后即按 mtime
    清走自建碎片（产品侧 _cleanup_stale_backup_tmp 另有下次建档自愈兜底）。

    下面的 500 + 指纹断言是刻意的缺陷金丝雀：产品修复 _serialize_value 后
    POST 会返回 201，此处会翻转失败提醒把断言改回 201 契约，并补全
    建档→finally DELETE 的删除腿。既有备份行一律不删（dev 资产）。
    """
    t0 = time.time()  # epoch 秒：与 os.path.getmtime 同一时钟域，守卫才有效
    r = live.api('POST', '/backups', admin, {'note': 'DTEST-H 备份冒烟'})
    elapsed = time.time() - t0
    body = r.json()
    assert r.status_code == 500 and '备份失败' in body.get('error', ''), (
        f'缺陷已修复或行为漂移：{r.status_code} {str(body)[:300]} '
        f'({elapsed:.1f}s)——若已 201，请按 docstring 改回建档契约并补删除腿')
    assert 'Decimal is not JSON serializable' in body['error']
    # 定档断言后立刻清走本次 POST 遗留的 .tmp-backup-*.json 碎片
    # （只删 mtime >= t0 的自建碎片，不碰他人/历史文件；产品侧
    # _cleanup_stale_backup_tmp 也会在下一次建档开头自愈兜底）
    from utils.backup import BACKUP_DIR
    if os.path.isdir(BACKUP_DIR):
        for n in os.listdir(BACKUP_DIR):
            p = os.path.join(BACKUP_DIR, n)
            if (n.startswith('.tmp-backup-') and n.endswith('.json')
                    and os.path.isfile(p) and os.path.getmtime(p) >= t0):
                try:
                    os.remove(p)
                except OSError:
                    pass
    # 列表：JSON 数组形状（实读 :45-57），按 created_at 倒序
    lst = live.api('GET', '/backups', admin)
    assert lst.status_code == 200
    assert isinstance(lst.json(), list) and lst.json()
    # 下载：对既有最新备份只读校验二进制 ZIP 魔数（不触碰任何既有行）
    newest = lst.json()[0]
    dl = live.api('GET', f"/backups/{newest['id']}/download", admin)
    assert dl.status_code == 200
    assert dl.content[:2] == b'PK'


def test_td_h05_backup_settings_roundtrip(admin):
    g = live.api('GET', '/backups/settings', admin)
    assert g.status_code == 200, f'{g.status_code} {g.text[:200]}'
    body = g.json()
    assert {'enabled', 'interval', 'retentionCount'} <= set(body)
    original = {'enabled': body['enabled'], 'interval': body['interval'],
                'retentionCount': body['retentionCount']}
    # 换一个合法 retentionCount 验证写入生效（PUT 校验：正整数，backups.py:189-190）
    target = dict(original)
    target['retentionCount'] = 7 if original['retentionCount'] != 7 else 8
    p = live.api('PUT', '/backups/settings', admin, target)
    assert p.status_code < 300, f'{p.status_code} {p.text[:300]}'
    assert p.json()['retentionCount'] == target['retentionCount']
    # 回写原值，不遗留对 dev 定时备份配置的改动
    restore = live.api('PUT', '/backups/settings', admin, original)
    assert restore.status_code < 300
    assert restore.json()['retentionCount'] == original['retentionCount']
    assert restore.json()['interval'] == original['interval']
