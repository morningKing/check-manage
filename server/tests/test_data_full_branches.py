"""族D-1 项目分支 —— L2 live-server API 层（TD-D01–D08）。

契约锚点（计划③ Global Constraints #1–#6）：快照从创建者当前分支复制（测试默认
在 main 建）；switch 仅 branch 类型；分支状态 per-user；merge 只测 theirs（ours
是静默 no-op，见 04 已知限制）；锁只拦「当前分支=被锁分支」的写路径；restore
先清空再恢复；DELETE 级联快照。
"""
import uuid

import pytest

import data_full_live as live

pytestmark = pytest.mark.data_full

NAME = {'id': 'f1', 'label': '名称', 'fieldName': 'name',
        'controlType': 'text', 'required': True, 'order': 1}
QTY = {'id': 'f2', 'label': '数量', 'fieldName': 'qty',
       'controlType': 'number', 'required': False, 'order': 2}


@pytest.fixture(scope='module')
def admin():
    if not live.server_up():
        pytest.skip('dev 栈未启动（Vite :5173 代理 /api → Flask :3002）')
    return live.login()


def _project(admin, purpose):
    page = live.make_page(admin, 'D', purpose, fields=[NAME, QTY])
    return page


def _rec(admin, collection, name, qty=None):
    body = {'id': uuid.uuid4().hex, 'name': name}
    if qty is not None:
        body['qty'] = qty
    r = live.api('POST', f'/{collection}', admin, body)
    assert r.status_code == 201, f'{r.status_code} {r.text[:200]}'
    return r.json()


def _make_branch(admin, page, name_suffix='开发分支'):
    r = live.api('POST', '/project-versions', admin, {
        'projectMenuId': page['project_menu_id'],
        'name': f"DTEST-D-{name_suffix}-{uuid.uuid4().hex[:6]}",
        'versionType': 'branch',
        'createdBy': 'admin',
    })
    assert r.status_code == 201, f'建分支失败 {r.status_code} {r.text[:300]}'
    return r.json()


def _switch(admin, vid, pmid):
    r = live.api('POST', f'/project-versions/{vid}/switch', admin,
                 {'projectMenuId': pmid})
    assert r.status_code < 300, f'{r.status_code} {r.text[:300]}'
    return r.json()


def _back_to_main(admin, page):
    """清理兜底：切回 main 再回收页面（尽力而为，不掩盖用例自身断言失败）。

    drop_page 的 GET ?all=true 与逐条 DELETE 都只覆盖请求者「当前分支」的行；
    若用户停在分支上，main 行会漏删、user_current_project_branch 也会残留
    指向将删分支。凡切过分支的用例，finally 里先 switch-main。
    """
    try:
        live.api('POST', f"/project-versions/{page['project_menu_id']}/switch-main",
                 admin, {'projectMenuId': page['project_menu_id']})
    except Exception:
        pass


def test_td_d01_create_branch_snapshots_main(admin):
    page = _project(admin, 'br-create')
    try:
        for i in range(3):
            _rec(admin, page['collection'], f'主干{i}')
        br = _make_branch(admin, page)
        assert br['id'].startswith('prj-ver-')
        assert br['recordsCount'] == 3 and br['versionType'] == 'branch'
        lst = live.api('GET', f"/project-versions/{page['project_menu_id']}", admin)
        assert any(it['id'] == br['id'] for it in lst.json()['items'])
        allb = live.api('GET', '/project-versions/all-branches', admin).json()
        assert allb[0]['id'] == 'main'  # 首项恒为主分支
    finally:
        live.drop_page(admin, page)


def test_td_d02_snapshot_not_switchable_branch_switch_clones(admin):
    page = _project(admin, 'br-switch')
    try:
        _rec(admin, page['collection'], '甲')
        snap = live.api('POST', '/project-versions', admin, {
            'projectMenuId': page['project_menu_id'], 'name': 'DTEST-D-快照',
            'versionType': 'snapshot', 'createdBy': 'admin'})
        assert snap.status_code == 201
        bad = live.api('POST', f"/project-versions/{snap.json()['id']}/switch",
                       admin, {'projectMenuId': page['project_menu_id']})
        assert bad.status_code == 400  # 快照不可切换
        br = _make_branch(admin, page)
        out = _switch(admin, br['id'], page['project_menu_id'])
        assert out['branchId'] == br['id']
        cur = live.api('GET',
                       f"/project-versions/{page['project_menu_id']}/current-branch",
                       admin).json()
        assert cur['branchId'] == br['id']
        # 克隆后分支上可见主干数据
        rows = live.api('GET', f"/{page['collection']}", admin).json()
        assert rows['total'] == 1
    finally:
        _back_to_main(admin, page)
        live.drop_page(admin, page)


def test_td_d03_branch_edit_isolated_from_main(admin):
    page = _project(admin, 'br-iso')
    try:
        _rec(admin, page['collection'], '主干记录')
        br = _make_branch(admin, page)
        _switch(admin, br['id'], page['project_menu_id'])
        _rec(admin, page['collection'], '分支独有')
        br_rows = live.api('GET', f"/{page['collection']}", admin).json()
        assert br_rows['total'] == 2
        live.api('POST', f"/project-versions/{page['project_menu_id']}/switch-main",
                 admin, {'projectMenuId': page['project_menu_id']})
        main_rows = live.api('GET', f"/{page['collection']}", admin).json()
        assert main_rows['total'] == 1  # 分支新增不影响 main
        assert main_rows['data'][0]['name'] == '主干记录'
    finally:
        _back_to_main(admin, page)
        live.drop_page(admin, page)


def test_td_d04_diff_detects_added_and_modified(admin):
    page = _project(admin, 'br-diff')
    try:
        base = _rec(admin, page['collection'], '将被改', qty=1)
        br = _make_branch(admin, page)
        _switch(admin, br['id'], page['project_menu_id'])
        live.api('PUT', f"/{page['collection']}/{base['id']}", admin,
                 {'qty': 99, '_version': base['_version']})
        _rec(admin, page['collection'], '分支新增')
        live.api('POST', f"/project-versions/{page['project_menu_id']}/switch-main",
                 admin, {'projectMenuId': page['project_menu_id']})
        d = live.api('POST', '/project-versions/diff', admin, {
            'projectMenuId': page['project_menu_id'],
            'baseVersion': 'main', 'targetVersion': br['id']})
        assert d.status_code < 300, f'{d.status_code} {d.text[:300]}'
        body = d.json()
        assert body['totalAdded'] == 1
        assert body['totalModified'] == 1
        mod = body['collections'][0]['modified'][0]
        flds = {f['fieldName']: f for f in mod['fields']}
        assert 'qty' in flds and flds['qty']['newValue'] == 99
    finally:
        _back_to_main(admin, page)
        live.drop_page(admin, page)


def test_td_d05_merge_theirs_updates_main_and_history(admin):
    page = _project(admin, 'br-merge')
    try:
        _rec(admin, page['collection'], '主干基线')
        br = _make_branch(admin, page)
        _switch(admin, br['id'], page['project_menu_id'])
        _rec(admin, page['collection'], '分支新增甲')
        _rec(admin, page['collection'], '分支新增乙')
        m = live.api('POST', '/project-versions/merge', admin, {
            'versionId': br['id'], 'targetBranch': 'main',
            'strategy': 'theirs', 'projectMenuId': page['project_menu_id']})
        assert m.status_code < 300, f'{m.status_code} {m.text[:300]}'
        body = m.json()
        assert body['success'] is True and body['mergeId'].startswith('merge-')
        created = sum(c['recordsCreated'] for c in body['collections'])
        assert created >= 2
        live.api('POST', f"/project-versions/{page['project_menu_id']}/switch-main",
                 admin, {'projectMenuId': page['project_menu_id']})
        rows = live.api('GET', f"/{page['collection']}", admin).json()
        names = {r['name'] for r in rows['data']}
        assert {'主干基线', '分支新增甲', '分支新增乙'} <= names  # 合并落 main
        h = live.api('GET', f"/project-versions/{br['id']}/merge-history", admin)
        assert h.json()['total'] >= 1
    finally:
        _back_to_main(admin, page)
        live.drop_page(admin, page)


def test_td_d05b_merge_detailed_field_decisions(admin):
    page = _project(admin, 'br-mdet')
    try:
        base = _rec(admin, page['collection'], '详合基线', qty=1)
        br = _make_branch(admin, page)
        _switch(admin, br['id'], page['project_menu_id'])
        live.api('PUT', f"/{page['collection']}/{base['id']}", admin,
                 {'qty': 42, '_version': base['_version']})
        live.api('POST', f"/project-versions/{page['project_menu_id']}/switch-main",
                 admin, {'projectMenuId': page['project_menu_id']})
        d = live.api('POST', '/project-versions/diff', admin, {
            'projectMenuId': page['project_menu_id'],
            'baseVersion': 'main', 'targetVersion': br['id']})
        mod = d.json()['collections'][0]['modified'][0]
        payload = {'versionId': br['id'], 'targetBranch': 'main',
                   'projectMenuId': page['project_menu_id'],
                   'collections': [{'collection': page['collection'],
                                    'added': [], 'removed': [],
                                    'modified': [{'recordId': mod['id'],
                                                  'fieldDecisions': [
                                                      {'fieldName': f['fieldName'],
                                                       'useSource': True}
                                                      for f in mod['fields']]}]}]}
        m = live.api('POST', '/project-versions/merge-detailed', admin, payload)
        assert m.status_code < 300, f'{m.status_code} {m.text[:300]}'
        live.api('POST', f"/project-versions/{page['project_menu_id']}/switch-main",
                 admin, {'projectMenuId': page['project_menu_id']})
        got = live.api('GET', f"/{page['collection']}/{base['id']}", admin).json()
        assert got['qty'] == 42  # useSource 决策把分支值合入 main
        empty = live.api('POST', '/project-versions/merge-detailed', admin, {
            'versionId': br['id'], 'targetBranch': 'main',
            'projectMenuId': page['project_menu_id'], 'collections': []})
        assert empty.status_code == 400  # 没有选择任何变更
    finally:
        _back_to_main(admin, page)
        live.drop_page(admin, page)


def test_td_d06_branch_lock_blocks_write_then_unlock(admin):
    page = _project(admin, 'br-lock')
    try:
        br = _make_branch(admin, page)
        _switch(admin, br['id'], page['project_menu_id'])
        lk = live.api('POST', f"/project-versions/{br['id']}/lock", admin,
                      {'reason': 'DTEST 锁定'})
        assert lk.status_code < 300
        denied = live.api('POST', f"/{page['collection']}", admin,
                          {'id': uuid.uuid4().hex, 'name': '被锁'})
        assert denied.status_code == 403
        assert '锁定' in denied.json()['error']
        ulk = live.api('POST', f"/project-versions/{br['id']}/unlock", admin, {})
        assert ulk.status_code < 300
        ok = live.api('POST', f"/{page['collection']}", admin,
                      {'id': uuid.uuid4().hex, 'name': '解锁后'})
        assert ok.status_code == 201
        # 重复 unlock → 400
        ulk2 = live.api('POST', f"/project-versions/{br['id']}/unlock", admin, {})
        assert ulk2.status_code == 400  # 该分支未被锁定
        # 主分支锁与分支锁共享 check_branch_lock 路径（含 main 分支判定），
        # 补一段：lock main → main 上写 403 → unlock 后恢复可写
        live.api('POST', f"/project-versions/{page['project_menu_id']}/switch-main",
                 admin, {'projectMenuId': page['project_menu_id']})
        mlk = live.api('POST', f"/project-versions/main/{page['project_menu_id']}/lock",
                       admin, {'reason': 'DTEST 主分支锁定'})
        assert mlk.status_code < 300
        denied_main = live.api('POST', f"/{page['collection']}", admin,
                               {'id': uuid.uuid4().hex, 'name': '主干被锁'})
        assert denied_main.status_code == 403
        assert '锁定' in denied_main.json()['error']
        mulk = live.api('POST',
                        f"/project-versions/main/{page['project_menu_id']}/unlock",
                        admin, {})
        assert mulk.status_code < 300
        ok_main = live.api('POST', f"/{page['collection']}", admin,
                           {'id': uuid.uuid4().hex, 'name': '主干解锁后'})
        assert ok_main.status_code == 201
    finally:
        _back_to_main(admin, page)
        live.drop_page(admin, page)


def test_td_d07_restore_wipes_branch_to_snapshot(admin):
    page = _project(admin, 'br-restore')
    try:
        _rec(admin, page['collection'], '快照基线')
        br = _make_branch(admin, page)
        _switch(admin, br['id'], page['project_menu_id'])
        _rec(admin, page['collection'], '分支脏数据')
        rs = live.api('POST', f"/project-versions/{br['id']}/restore", admin,
                      {'projectMenuId': page['project_menu_id']})
        assert rs.status_code < 300, f'{rs.status_code} {rs.text[:300]}'
        rows = live.api('GET', f"/{page['collection']}", admin).json()
        assert rows['total'] == 1 and rows['data'][0]['name'] == '快照基线'
    finally:
        _back_to_main(admin, page)
        live.drop_page(admin, page)


def test_td_d08_delete_impact_and_delete_cascades(admin):
    page = _project(admin, 'br-del')
    try:
        _rec(admin, page['collection'], '基线')
        br = _make_branch(admin, page)
        _switch(admin, br['id'], page['project_menu_id'])
        live.api('POST', f"/project-versions/{page['project_menu_id']}/switch-main",
                 admin, {'projectMenuId': page['project_menu_id']})
        imp = live.api('GET', f"/project-versions/{br['id']}/delete-impact", admin)
        assert imp.status_code == 200
        assert imp.json()['canDelete'] is True
        d = live.api('DELETE', f"/project-versions/{br['id']}", admin)
        assert d.status_code < 300
        lst = live.api('GET', f"/project-versions/{page['project_menu_id']}", admin)
        assert not any(it['id'] == br['id'] for it in lst.json()['items'])
    finally:
        _back_to_main(admin, page)
        live.drop_page(admin, page)
