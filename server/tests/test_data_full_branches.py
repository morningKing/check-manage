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


# ==================== 族D-2 跨项目依赖（TD-D09–D14） ====================
# 契约核对（routes/cross_project_dependencies.py + utils/cross_project_dependency.py），
# 与 brief 草稿的三处偏差均为对齐实测契约：
# 1. relationType 枚举仅 track-main | read-write | read-only——草稿的 uses/consumes
#    会被 400 拒收，D09 PUT 的 consumes 相应改为 track-main；
# 2. validate 返回的 relationValidations 行键是驼峰 sourceCollection/sourceField；
# 3. merge-order 的 sourceBranch 必须是 project_versions 真实行 id（main 是虚拟
#    分支，传 main 会 500「版本不存在」），故 D12 先建真分支再查。
# 另外：D10–D13 finally 里尽力删除依赖行（_drop_dep，失败不掩盖断言），减少残留。

REL = {'id': 'f3', 'label': '关联目标', 'fieldName': 'rel', 'controlType': 'relation',
       'required': False, 'order': 3,
       'relationConfig': {'targetCollection': 'SET_AT_RUNTIME', 'displayField': 'name',
                          'targetField': 'rel'}}


def _dep_topology(admin, suffix):
    """项目乙（先建，含数据页）+ 项目甲（数据页带 relation→乙 collection）。"""
    p2 = _project(admin, f'dep-b-{suffix}')
    fields = [NAME, dict(REL, relationConfig={
        'targetCollection': p2['collection'], 'displayField': 'name',
        'targetField': 'rel'})]
    p1 = live.make_page(admin, 'D', f'dep-a-{suffix}', fields=fields)
    return p1, p2


def _drop_dep(admin, p1, dep_id):
    """尽力而为的残留清理：依赖声明行先删再回收页面（响应不校验）。"""
    if dep_id:
        live.api('DELETE', f"/projects/{p1['project_menu_id']}/dependencies/{dep_id}",
                 admin)


def test_td_d09_dependency_crud_and_duplicate(admin):
    p1, p2 = _dep_topology(admin, 'crud')
    dep_id = None
    try:
        r = live.api('POST', f"/projects/{p1['project_menu_id']}/dependencies", admin,
                     {'targetProject': p2['project_menu_id'], 'relationType': 'read-only'})
        assert r.status_code == 201, f'{r.status_code} {r.text[:300]}'
        dep = r.json()
        dep_id = dep['id']
        assert dep_id.startswith('dep-')
        dup = live.api('POST', f"/projects/{p1['project_menu_id']}/dependencies", admin,
                       {'targetProject': p2['project_menu_id'], 'relationType': 'read-only'})
        assert dup.status_code == 400  # 已存在相同的依赖声明
        lst = live.api('GET', f"/projects/{p1['project_menu_id']}/dependencies", admin)
        assert any(d['id'] == dep_id for d in lst.json()['dependencies'])
        deps = live.api('GET', f"/projects/{p2['project_menu_id']}/dependents", admin)
        assert any(d['id'] == dep_id for d in deps.json()['dependents'])
        put = live.api('PUT',
                       f"/projects/{p1['project_menu_id']}/dependencies/{dep_id}",
                       admin, {'relationType': 'track-main'})
        assert put.status_code < 300
        dele = live.api('DELETE',
                        f"/projects/{p1['project_menu_id']}/dependencies/{dep_id}",
                        admin)
        assert dele.status_code < 300
        lst2 = live.api('GET', f"/projects/{p1['project_menu_id']}/dependencies", admin)
        assert not any(d['id'] == dep_id for d in lst2.json()['dependencies'])
    finally:
        _drop_dep(admin, p1, dep_id)
        live.drop_page(admin, p1)
        live.drop_page(admin, p2)


def test_td_d10_validate_reports_relations(admin):
    p1, p2 = _dep_topology(admin, 'val')
    dep_id = None
    try:
        dep = live.api('POST', f"/projects/{p1['project_menu_id']}/dependencies", admin,
                       {'targetProject': p2['project_menu_id'],
                        'relationType': 'read-only'}).json()
        dep_id = dep.get('id')
        v = live.api('POST', f"/dependencies/{dep_id}/validate", admin, {})
        assert v.status_code < 300, f'{v.status_code} {v.text[:300]}'
        body = v.json()
        assert body['isValid'] is True
        assert any(rv['sourceCollection'] == p1['collection']
                   for rv in body['relationValidations'])
        scan = live.api('GET',
                        f"/projects/{p1['project_menu_id']}/scan-relations/{p2['project_menu_id']}",
                        admin)
        rels = scan.json()['relations']
        assert any(r['source_field'] == 'rel' and r['control_type'] == 'relation'
                   for r in rels)
    finally:
        _drop_dep(admin, p1, dep_id)
        live.drop_page(admin, p1)
        live.drop_page(admin, p2)


def test_td_d11_delete_check_blocks_when_dependent_exists(admin):
    p1, p2 = _dep_topology(admin, 'delchk')
    dep_id = None
    try:
        dep = live.api('POST', f"/projects/{p1['project_menu_id']}/dependencies", admin,
                       {'targetProject': p2['project_menu_id'],
                        'relationType': 'read-only'}).json()
        dep_id = dep.get('id')
        chk = live.api('GET',
                       f"/projects/{p2['project_menu_id']}/branches/main/delete-check",
                       admin)
        assert chk.status_code == 200
        body = chk.json()
        # p2 被 p1 依赖 → 不可删（canDelete false 或 dependentProjects 非空）
        assert body.get('canDelete') is False or body.get('dependentProjects')
    finally:
        _drop_dep(admin, p1, dep_id)
        live.drop_page(admin, p1)
        live.drop_page(admin, p2)


def test_td_d12_merge_check_and_order(admin):
    p1, p2 = _dep_topology(admin, 'mchk')
    dep_id = None
    try:
        dep = live.api('POST', f"/projects/{p1['project_menu_id']}/dependencies", admin,
                       {'targetProject': p2['project_menu_id'],
                        'relationType': 'read-only'}).json()
        dep_id = dep.get('id')
        mc = live.api('GET',
                      f"/projects/{p1['project_menu_id']}/merge-check?sourceBranch=main",
                      admin)
        assert mc.status_code == 200 and 'canMerge' in mc.json()
        br = _make_branch(admin, p1)  # merge-order 只认 project_versions 真实行
        mo = live.api('GET',
                      f"/projects/{p1['project_menu_id']}/merge-order?sourceBranch={br['id']}",
                      admin)
        assert mo.status_code == 200, f'{mo.status_code} {mo.text[:300]}'
        assert isinstance(mo.json().get('mergeOrder'), list)
        assert any(m['branchId'] == br['id'] for m in mo.json()['mergeOrder'])
    finally:
        _drop_dep(admin, p1, dep_id)
        live.drop_page(admin, p1)
        live.drop_page(admin, p2)


def test_td_d12b_merge_order_main_virtual_400(admin):
    """虚拟主分支 main 请求 merge-order → 显式 400（04 #5 修复，不再 500）。

    'main' 是虚拟分支（user_current_project_branch 概念值，project_versions
    无对应行）——util get_coordinated_merge_order 按 id 查版本会 ValueError
    → 通用 except 回 500。路由层（routes/cross_project_dependencies.py
    get_merge_order）先拦截：main → 400 「虚拟主分支 main 无合并顺序，请指定
    具体分支」。真实分支路径见 TD-D12（_make_branch 后 merge-order 200）。
    """
    page = _project(admin, 'mo-main')
    try:
        mo = live.api('GET',
                      f"/projects/{page['project_menu_id']}/merge-order?sourceBranch=main",
                      admin)
        assert mo.status_code == 400, f'{mo.status_code} {mo.text[:300]}'
        assert mo.json().get('error') == '虚拟主分支 main 无合并顺序，请指定具体分支'
    finally:
        live.drop_page(admin, page)


def test_td_d13_update_dependencies_after_merge(admin):
    p1, p2 = _dep_topology(admin, 'udam')
    dep_id = None
    try:
        dep = live.api('POST', f"/projects/{p1['project_menu_id']}/dependencies", admin,
                       {'targetProject': p2['project_menu_id'], 'relationType': 'read-write',
                        'sourceBranch': 'main'}).json()
        dep_id = dep.get('id')
        u = live.api('POST',
                     f"/projects/{p1['project_menu_id']}/update-dependencies-after-merge",
                     admin, {'sourceBranch': 'main'})
        assert u.status_code < 300 and u.json()['success'] is True
        assert isinstance(u.json().get('updatedCount'), int)
    finally:
        _drop_dep(admin, p1, dep_id)
        live.drop_page(admin, p1)
        live.drop_page(admin, p2)


def test_td_d14_dependency_requires_project_menus(admin):
    """实际契约：源/目标必须 project 菜单——传数据页菜单 id 应 400。

    relationType 用合法枚举值（read-only），保证 400 来自源菜单类型校验
    （源项目不存在或不是项目类型）而非参数枚举校验。
    """
    p1, p2 = _dep_topology(admin, 'projchk')
    try:
        bad = live.api('POST', f"/projects/{p1['menu_id']}/dependencies", admin,
                       {'targetProject': p2['menu_id'], 'relationType': 'read-only'})
        assert bad.status_code == 400, f'数据页菜单应被拒，得 {bad.status_code}'
    finally:
        live.drop_page(admin, p1)
        live.drop_page(admin, p2)
