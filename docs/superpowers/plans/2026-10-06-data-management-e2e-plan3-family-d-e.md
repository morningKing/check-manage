# 数据管理 E2E 测试体系 · 计划③：族D 项目分支与依赖 + 族E 数据进出 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 覆盖族D（项目分支全生命周期 + 跨项目依赖）与族E（导入/ETL/导出/菜单导出），L2 29 例 + L3 5 例全绿，02/01/04 文档同步。

**Architecture:** 延续计划①②模式：L2 live-server pytest 打 Vite :5173 代理（新增文件 `test_data_full_branches.py`（含依赖）与 `test_data_full_io.py`（导入+ETL+导出））；L3 Playwright 两 spec（`data-branches.spec.ts`、`data-io.spec.ts`）。每测试自建项目拓扑（make_page 即 workspace→project→data 三级链，天然项目隔离）。

**Tech Stack:** pytest + requests（L2，openpyxl 可用于服务端侧验证但导入解析在前端）、Playwright + TS（L3，xlsx 生成复用前端 node_modules 的 `xlsx` 包）。

**Spec:** [docs/superpowers/specs/2026-10-06-data-management-e2e-design.md](../specs/2026-10-06-data-management-e2e-design.md) §5 族D/族E；前置计划①②（helpers/约定/基线 L2 42 + L3 17 全绿）。

## Global Constraints

- 测试资产 `DTEST-<族>-<用途>-<时间戳>`；每测试自建项目拓扑并清理；禁止触碰非 DTEST 数据。
- L2：`cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_data_full_<file>.py -v`；服务不在 skip（marker `data_full`）。
- L3：`npx playwright test e2e/data-full`；选择器以组件实读为准（brief 给的猜测一律先核实）。
- 后端改代码必须手动重启 Flask；本计划预期零产品代码改动（发现契约不符→登记 DONE_WITH_CONCERNS）。
- **族D/E 已核实 API 事实**（2026-10-06 研究代理核实，file:line 备案；写测试勿凭记忆改写）：
  1. **建分支**：`POST /project-versions` `{projectMenuId, name, description?, versionType:'snapshot'|'branch'(默认 snapshot), createdBy(必需)}` → 201 `{id:'prj-ver-*', recordsCount, relationsCount, collectionsCount,...}`；项目下无数据菜单 → 400；**快照从创建者当前分支复制**（测试须确保在 main 上建）；权限 `admin.project_versions`。
  2. **switch**：`POST /project-versions/<vid>/switch` `{projectMenuId}` 仅 branch+active 可切（snapshot → 400 `只能切换到分支类型`）；switch 把用户对该项目及**每个 collection** 的当前分支置为该分支，并从快照克隆行到 `dynamic_data(branch_id=vid)`；`switch-main` 切回；分支状态 **per-user**（`user_current_project_branch` 表）；`GET /project-versions/<pmid>/current-branch` → `{branchId(默认'main'), branchName,...}`；`PUT` `{branchId}`。
  3. **diff**：`POST /project-versions/diff` `{projectMenuId, baseVersion, targetVersion}`（可 'main'|'current'|<vid>）→ `{collections:[{added,removed,modified[{id,fields[{fieldName,oldValue,newValue}]}],unchangedCount}], totalAdded,...}`；**只比较 pageConfig 声明的字段**；relation 字段按排序后 id 数组比较。
  4. **merge**：`POST /project-versions/merge` `{versionId, targetBranch('current'|'main'), strategy('theirs'), projectMenuId, skipDependencyCheck?}` → `{success, mergeId:'merge-*', collections[{recordsCreated/Updated/Deleted}], canMergeAgain:true}`；**theirs=删 target 独有、插 source 独有、更新修改**；**`strategy:'ours'` 是静默 no-op（产品缺陷级事实，只测 theirs）**；依赖阻塞 → 400 `存在阻塞依赖`；merge-history → `{mergeRecords, total}`。`merge-detailed` 需 `{collections:[...], fieldDecisions}`，空 → 400。
  5. **lock**：`POST /project-versions/<vid>/lock` `{reason?}`（reason 接受但**不持久化**）；仅 branch+active+未锁（否则 400）；主分支锁 `POST /project-versions/main/<pmid>/lock`（menu_type='project' 才行）。锁的写路径效果：dynamic.py POST/PUT/DELETE/batch-* 对**当前分支=被锁分支的用户**返 403 `{"error":"当前分支已被 <locked_by> 锁定，无法进行修改操作"}`；GET 不拦；merge/restore 不受锁约束。
  6. **restore**：`POST /project-versions/<vid>/restore` `{projectMenuId}` **清空当前分支数据**后从快照恢复 → `{success, recordsCount}`。**DELETE 分支**：protected/被跨项目依赖/有子分支 → 400；级联删快照；`delete-impact` → `{canDelete, usersOnBranch, childCount, warningMessage,...}`。
  7. **跨项目依赖**：`POST /projects/<pmid>/dependencies` `{targetProject(必需), relationType(必需), sourceBranch?/targetBranch?默认'main', pinnedVersion?}` → 201 `{id:'dep-*',...relations}`；源和目标都必须 `menu_type='project'`；重复 (source,sourceBranch,target) → 400 `已存在相同的依赖声明`；循环 → 400。`validate` → `{isValid, errors[], warnings[], relationValidations[]}`（持久化 is_validated）；`GET /projects/<src>/scan-relations/<tgt>` → `{relations:[{source_collection,source_field,target_collection,control_type}], total}`（扫 pageConfig relation 字段落点）；`GET .../dependents`、`GET .../branches/<bid>/delete-check` → `{canDelete,...}`、`GET .../merge-check?sourceBranch=`、`GET .../merge-order?sourceBranch=`、`POST .../update-dependencies-after-merge` `{sourceBranch}` → `{success, updatedCount}`。
  8. **导入链路**：**解析在前端 SheetJS**（DynamicPage 隐藏 file input → parseImportFile → 分块 `POST /api/<collection>/batch-create`（保 id）→ `POST /importRuns` 登记）。`POST /importRuns` 是**纯历史登记**：`{pageId, collection, branchId, fileName, successCount, createdCount, updatedCount, failedCount, failures[{recordId,originalRecord,payload,reason}]}` → 201 `{id:'imprun-*'}`（status 自动 success|partial）；需页面 create 权限；`GET /importRuns?pageId&collection&limit&offset` → `{runs, total}`；`GET /importRuns/<id>` → `{run, failures}`；`POST .../retry-result` `{resolvedRecordIds, successDelta, createdDelta, updatedDelta}` 删已解决 failures 并重算 status；**无 DELETE 端点（残留）**。
  9. **batch-create 语义**：已存在 id = **upsert UPDATE**（重复导入覆盖）；批内重复 id → 409 `批量导入包含重复 ID`（除非 options.continueOnError）；缺 id 服务端补 uuid。
  10. **ETL**：`POST /etlTasks` `{name, description?, steps[], enabled}`（steps 存储不校验）；step `{id, name, type, config, onError:'stop'|'skip'|'continue'}`；type∈`http_request/json_input{data:JSON字符串}/file_upload{fileId}/script(python 返回 list)/field_mapping{mappings[{source,target}],keepUnmapped}/filter{expression}/save_to_collection{collection, mode:'insert'|'upsert'|'update', matchField(upsert/update 必需, update 未匹配计 error)}`；**save 恒写 main 分支**（无视用户当前分支）；分批提交（失败可半落）。`POST /etlTasks/<id>/run`：`dryRun:true` → **同步**执行后**回滚**（零副作用），resp `{status, totalRecords, successCount, errorCount, stepResults, errors}`；否则 **async 202 `{logId, status:'pending'}`**，APScheduler 2s tick 认领（dev 常驻；pytest 下不启动）。日志 `GET /etlTasks/<id>/logs(≤20)` / `/logs/<logId>` → `{status: pending|running|success|partial|error|cancelled, totalRecords, successCount, errorCount, stepResults[...], progressCurrent,...}`；cancel 对已结束 → 409 `任务已结束，无法取消`，否则协作标志。`enabled` 是**展示字段**（调度器不读）；任务只手动 run 触发。
  11. **导出脚本**：`POST /exportScripts` `{name(全局唯一→400), description?, language默认'python', script, outputFormat默认'json', scope:'page'|'menu', boundCollection XOR boundMenuId 必需}`；脚本=python 沙箱子进程，locals `{data, fields, page_name, references, result, filename, content_type}`，**必须给 result 赋值**；`POST /exportScripts/<id>/test` `{collection?...}` → `{success, preview(≤5000), filename, contentType, size, recordCount}`，失败 400 `{success:false, error}`；`/debug` 断点 trace；`POST /exportScripts/execute` `{scriptId!, collection!, recordId?, branchId?}` → **二进制附件**（绑定不符 400）；`POST /exportScripts/batchExport` `{tasks:[{scriptId, collection, branchId?}]}` → ZIP。
  12. **菜单导出**：`POST /menuExport` `{menuIds!, scriptId?, branchId默认'main'}` → ZIP（部分失败进 `X-Export-Errors` 头，全失败 400）；**只有导出无导入端点**；`GET /menuExport/availableMenus` → 树；`POST /menuExport/preview` `{menuIds, branchId}` → `{menus[{pages, totalRecords}], totalRecords, availableScripts}`；`POST /menuExport/batchClear` `{collections!, branchId}` → 清空分支记录 `{totalDeleted}`（仅对 DTEST collection 使用）。
  13. **L3 UI 入口**：ProjectVersionManager 抽屉经 DynamicPage 操作菜单 `version`/`dependency` 命令打开（props `{projectMenuId, defaultTab:'versions'|'dependencies'}`），仅当菜单有 projectId/父为 project 时渲染；导入/导出/导入历史/模板全在页面「操作」下拉（import/template/importHistory 由 canCreate 门控，导出=Excel 下载、脚本导出=`script:<id>` 项）。
  14. **无 API 清理路径的残留表**（sweep 清单，计划⑤统一处理）：import_runs、merge_records、merge_backups、project_version_snapshots（分支 DELETE 级联可清）、user_current_project_branch（分支 DELETE 后仍存）、workflow_definitions/workflow_instances、data_files。
- 既有事实沿用：batch-create 缺 id 补 uuid；数据页名称全局唯一；viewConfig 须 PUT。

---

### Task 1: 族D-1 分支 L2（TD-D01–D08 + D05b）

**Files:**
- Modify: `server/tests/data_full_live.py`（make_page 返回值补 `project_menu_id`/`workspace_menu_id` 两个确定性键）
- Create: `server/tests/test_data_full_branches.py`（TD-D01–D08 + TD-D05b，共 9 例）

**Interfaces:**
- Produces: `make_page` 句柄新增 `project_menu_id`（= `menu-proj-<collection>`，Task 2 依赖）；分支 L2 基线 8 例。

- [ ] **Step 1: 扩展 make_page 返回值**

在 `data_full_live.py` `make_page` 的返回 dict 增加两个键（值由既有确定性 ID 规则直接给出，不发请求）：

```python
    return {'collection': collection, 'page_id': page_id,
            'menu_id': r3.json().get('id', f'menu-{collection}'),
            'project_menu_id': f'menu-proj-{collection}',
            'workspace_menu_id': f'menu-ws-{collection}',
            'path': f'/dtest/{collection}', 'name': collection}
```

（若既有实现的项目/工作区菜单 id 后缀与此不同，以实现为准并在报告注明。）

- [ ] **Step 2: 写 test_data_full_branches.py（TD-D01–D08）**

```python
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
    finally:
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
        live.drop_page(admin, page)
```

注意（执行者核对）：主分支锁用例未单列——`POST /project-versions/main/<pmid>/lock` 与分支锁共享 check_branch_lock 路径，若实现顺利可在 D06 内追加主分支锁段落（lock main → main 上写 403 → unlock），或单独 TD-D06b；按实现复杂度决定并在报告注明。

- [ ] **Step 3: 跑绿**

```bash
cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_data_full_branches.py -v
```

Expected: 9 passed（两遍稳定）。

- [ ] **Step 4: Commit**

```bash
git add server/tests/data_full_live.py server/tests/test_data_full_branches.py
git commit -m "test(data-full): 族D L2 —— 分支建/切/隔离/diff/merge/详合/锁/恢复/删除 9 例"
```

---

### Task 2: 族D-2 跨项目依赖 L2（TD-D09–D14）

**Files:**
- Modify: `server/tests/test_data_full_branches.py`（追加 TD-D09–D14，6 例）

**Interfaces:**
- Consumes: Task 1 的 `project_menu_id` 句柄键。
- Produces: 依赖 L2 基线 6 例。

- [ ] **Step 1: 追加用例（TD-D09–D14）**

```python
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


def test_td_d09_dependency_crud_and_duplicate(admin):
    p1, p2 = _dep_topology(admin, 'crud')
    try:
        r = live.api('POST', f"/projects/{p1['project_menu_id']}/dependencies", admin,
                     {'targetProject': p2['project_menu_id'], 'relationType': 'uses'})
        assert r.status_code == 201, f'{r.status_code} {r.text[:300]}'
        dep = r.json()
        assert dep['id'].startswith('dep-')
        dup = live.api('POST', f"/projects/{p1['project_menu_id']}/dependencies", admin,
                       {'targetProject': p2['project_menu_id'], 'relationType': 'uses'})
        assert dup.status_code == 400  # 已存在相同的依赖声明
        lst = live.api('GET', f"/projects/{p1['project_menu_id']}/dependencies", admin)
        assert any(d['id'] == dep['id'] for d in lst.json()['dependencies'])
        deps = live.api('GET', f"/projects/{p2['project_menu_id']}/dependents", admin)
        assert any(d['id'] == dep['id'] for d in deps.json()['dependents'])
        put = live.api('PUT',
                       f"/projects/{p1['project_menu_id']}/dependencies/{dep['id']}",
                       admin, {'relationType': 'consumes'})
        assert put.status_code < 300
        dele = live.api('DELETE',
                        f"/projects/{p1['project_menu_id']}/dependencies/{dep['id']}",
                        admin)
        assert dele.status_code < 300
        lst2 = live.api('GET', f"/projects/{p1['project_menu_id']}/dependencies", admin)
        assert not any(d['id'] == dep['id'] for d in lst2.json()['dependencies'])
    finally:
        live.drop_page(admin, p1)
        live.drop_page(admin, p2)


def test_td_d10_validate_reports_relations(admin):
    p1, p2 = _dep_topology(admin, 'val')
    try:
        dep = live.api('POST', f"/projects/{p1['project_menu_id']}/dependencies", admin,
                       {'targetProject': p2['project_menu_id'],
                        'relationType': 'uses'}).json()
        v = live.api('POST', f"/dependencies/{dep['id']}/validate", admin, {})
        assert v.status_code < 300, f'{v.status_code} {v.text[:300]}'
        body = v.json()
        assert body['isValid'] is True
        assert any(rv['source_collection'] == p1['collection']
                   for rv in body['relationValidations'])
        scan = live.api('GET',
                        f"/projects/{p1['project_menu_id']}/scan-relations/{p2['project_menu_id']}",
                        admin)
        rels = scan.json()['relations']
        assert any(r['source_field'] == 'rel' and r['control_type'] == 'relation'
                   for r in rels)
    finally:
        live.drop_page(admin, p1)
        live.drop_page(admin, p2)


def test_td_d11_delete_check_blocks_when_dependent_exists(admin):
    p1, p2 = _dep_topology(admin, 'delchk')
    try:
        live.api('POST', f"/projects/{p1['project_menu_id']}/dependencies", admin,
                 {'targetProject': p2['project_menu_id'], 'relationType': 'uses'})
        chk = live.api('GET',
                       f"/projects/{p2['project_menu_id']}/branches/main/delete-check",
                       admin)
        assert chk.status_code == 200
        body = chk.json()
        # p2 被 p1 依赖 → 不可删（canDelete false 或 dependentProjects 非空）
        assert body.get('canDelete') is False or body.get('dependentProjects')
    finally:
        live.drop_page(admin, p1)
        live.drop_page(admin, p2)


def test_td_d12_merge_check_and_order(admin):
    p1, p2 = _dep_topology(admin, 'mchk')
    try:
        live.api('POST', f"/projects/{p1['project_menu_id']}/dependencies", admin,
                 {'targetProject': p2['project_menu_id'], 'relationType': 'uses'})
        mc = live.api('GET',
                      f"/projects/{p1['project_menu_id']}/merge-check?sourceBranch=main",
                      admin)
        assert mc.status_code == 200 and 'canMerge' in mc.json()
        mo = live.api('GET',
                      f"/projects/{p1['project_menu_id']}/merge-order?sourceBranch=main",
                      admin)
        assert mo.status_code == 200
        assert isinstance(mo.json().get('mergeOrder'), list)
    finally:
        live.drop_page(admin, p1)
        live.drop_page(admin, p2)


def test_td_d13_update_dependencies_after_merge(admin):
    p1, p2 = _dep_topology(admin, 'udam')
    try:
        dep = live.api('POST', f"/projects/{p1['project_menu_id']}/dependencies", admin,
                       {'targetProject': p2['project_menu_id'], 'relationType': 'uses',
                        'sourceBranch': 'main'}).json()
        u = live.api('POST',
                     f"/projects/{p1['project_menu_id']}/update-dependencies-after-merge",
                     admin, {'sourceBranch': 'main'})
        assert u.status_code < 300 and u.json()['success'] is True
        assert isinstance(u.json().get('updatedCount'), int)
    finally:
        live.drop_page(admin, p1)
        live.drop_page(admin, p2)


def test_td_d14_dependency_requires_project_menus(admin):
    """实际契约：源/目标必须 project 菜单——传数据页菜单 id 应 400。"""
    p1, p2 = _dep_topology(admin, 'projchk')
    try:
        bad = live.api('POST', f"/projects/{p1['menu_id']}/dependencies", admin,
                       {'targetProject': p2['menu_id'], 'relationType': 'uses'})
        assert bad.status_code == 400, f'数据页菜单应被拒，得 {bad.status_code}'
    finally:
        live.drop_page(admin, p1)
        live.drop_page(admin, p2)
```

注意（执行者核对）：`_dep_topology` 中 p1 带 relation→p2；若 PUT /pageConfigs 对 relationConfig 的全量替换路径有坑，可改为「p1 先建无关联字段 → p2 → PUT 补字段」两步法（计划② T3 已验证该路径可行）。D13 的 updatedCount 语义以实测为准（依赖源分支可能不匹配时 updatedCount=0 亦可接受，断言已放宽为 int）。

- [ ] **Step 2: 跑绿**

```bash
cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_data_full_branches.py -v
```

Expected: 15 passed（两遍稳定）。

- [ ] **Step 3: Commit**

```bash
git add server/tests/test_data_full_branches.py
git commit -m "test(data-full): 族D L2 依赖 —— CRUD/validate/scan-relations/delete-check/merge-check/更新 6 例"
```

---

### Task 3: 族E-1 导入链路 L2（TD-E01–E05）

**Files:**
- Create: `server/tests/test_data_full_io.py`（TD-E01–E05；后续 Task 4 续加导出）

**Interfaces:**
- Consumes: `data_full_live`（batch-create 走通用 api）。
- Produces: 导入 L2 基线 5 例；`_rec` 局部助手模式。

- [ ] **Step 1: 写 test_data_full_io.py 导入部分（TD-E01–E05）**

```python
"""族E 数据进出 —— L2 live-server API 层。

导入（TD-E01–E05）/ ETL（TD-E06–E10）/ 导出与菜单导出（TD-E11–E16）。
契约锚点（计划③ Global Constraints #8–#12）：batch-create 已存在 id=upsert UPDATE；
POST /importRuns 是纯历史登记（解析在前端 SheetJS）；ETL dryRun 同步回滚、真跑
async 202 + 轮询日志、save 恒写 main、cancel 已结束 409；导出脚本 python 必须给
result 赋值、execute 二进制、batchExport ZIP、menuExport 只有导出无导入。
"""
import json
import time
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


@pytest.fixture(scope='module')
def pageh(admin):
    page = live.make_page(admin, 'E', 'io', fields=[NAME, QTY])
    yield page
    live.drop_page(admin, page)


def _names(admin, collection):
    rows = live.api('GET', f'/{collection}?all=true', admin).json()
    return {r['name']: r for r in rows['data']}


def test_td_e01_batch_create_upsert_existing_id(admin, pageh):
    """重复导入语义：同 id 再 batch-create = UPDATE 覆盖（不 409 不重复）。"""
    rid = uuid.uuid4().hex
    r1 = live.api('POST', f"/{pageh['collection']}/batch-create", admin,
                  {'records': [{'id': rid, 'data': {'name': '导入甲', 'qty': 1}}]})
    assert r1.status_code < 300
    r2 = live.api('POST', f"/{pageh['collection']}/batch-create", admin,
                  {'records': [{'id': rid, 'data': {'name': '导入甲改', 'qty': 2}}]})
    assert r2.status_code < 300
    names = _names(admin, pageh['collection'])
    assert '导入甲改' in names and names['导入甲改']['qty'] == 2
    assert '导入甲' not in names  # 覆盖而非并存


def test_td_e02_batch_create_dup_ids_in_batch_409(admin, pageh):
    rid = uuid.uuid4().hex
    r = live.api('POST', f"/{pageh['collection']}/batch-create", admin,
                 {'records': [{'id': rid, 'data': {'name': '重复a'}},
                              {'id': rid, 'data': {'name': '重复b'}}]})
    assert r.status_code == 409  # 批内重复 ID
    cont = live.api('POST', f"/{pageh['collection']}/batch-create", admin,
                    {'records': [{'id': rid, 'data': {'name': '重复a'}},
                                 {'id': rid, 'data': {'name': '重复b'}}],
                     'options': {'continueOnError': True}})
    assert cont.status_code < 300  # continueOnError 放行（upsert 语义，最终 1 行）
    names = _names(admin, pageh['collection'])
    assert sum(1 for n in names if n.startswith('重复')) == 1


def test_td_e03_import_run_history_create_and_read(admin, pageh):
    """POST /importRuns 纯登记（解析在前端）；failures 驱动 partial 状态。"""
    r = live.api('POST', '/importRuns', admin, {
        'pageId': pageh['page_id'], 'collection': pageh['collection'],
        'branchId': 'main', 'fileName': 'DTEST-E-导入.xlsx',
        'successCount': 2, 'createdCount': 2, 'updatedCount': 0,
        'failedCount': 1,
        'failures': [{'recordId': 'row-3', 'originalRecord': {'名称': '坏行'},
                      'payload': {}, 'reason': '名称缺失'}]})
    assert r.status_code == 201, f'{r.status_code} {r.text[:300]}'
    run_id = r.json()['id']
    assert run_id.startswith('imprun-')
    lst = live.api('GET',
                   f"/importRuns?pageId={pageh['page_id']}&collection={pageh['collection']}",
                   admin)
    assert lst.status_code == 200 and lst.json()['total'] >= 1
    got = live.api('GET', f'/importRuns/{run_id}', admin)
    assert got.status_code == 200
    assert got.json()['run']['status'] == 'partial'
    assert len(got.json()['failures']) == 1


def test_td_e04_import_run_retry_result_resolves_failures(admin, pageh):
    r = live.api('POST', '/importRuns', admin, {
        'pageId': pageh['page_id'], 'collection': pageh['collection'],
        'branchId': 'main', 'fileName': 'DTEST-E-重试.xlsx',
        'successCount': 1, 'createdCount': 1, 'updatedCount': 0,
        'failedCount': 1,
        'failures': [{'recordId': 'row-9', 'originalRecord': {'名称': '待重试'},
                      'payload': {'name': '待重试'}, 'reason': 'qty 非法'}]})
    run_id = r.json()['id']
    rr = live.api('POST', f'/importRuns/{run_id}/retry-result', admin,
                  {'resolvedRecordIds': ['row-9'], 'successDelta': 1,
                   'createdDelta': 1, 'updatedDelta': 0})
    assert rr.status_code < 300, f'{rr.status_code} {rr.text[:300]}'
    got = live.api('GET', f'/importRuns/{run_id}', admin)
    assert len(got.json()['failures']) == 0  # 已解决 failures 被删
    assert got.json()['run']['status'] == 'success'  # 重算


def test_td_e05_import_run_validation_and_pagination(admin, pageh):
    bad = live.api('POST', '/importRuns', admin,
                   {'pageId': pageh['page_id'], 'collection': pageh['collection'],
                    'fileName': 'DTEST-E-缺参.xlsx'})
    assert bad.status_code == 400  # 缺统计字段/必填参数
    lst = live.api('GET',
                   f"/importRuns?pageId={pageh['page_id']}&collection={pageh['collection']}&limit=1&offset=0",
                   admin)
    assert lst.status_code == 200
    assert len(lst.json()['runs']) <= 1 and 'total' in lst.json()
```

注意（执行者核对）：E03/E04 的必填字段集以 `server/routes/import_runs.py:37-83` 实测为准（缺参 400 的具体字段若不同，按实际报错调整并在报告注明）；retry-result 的 status 重算规则（partial→success）以实测为准，若不符记录实际行为。

- [ ] **Step 2: 跑绿**

```bash
cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_data_full_io.py -v
```

Expected: 5 passed（两遍稳定）。

- [ ] **Step 3: Commit**

```bash
git add server/tests/test_data_full_io.py
git commit -m "test(data-full): 族E L2 导入 —— batch upsert/批内重复/历史登记/重试/分页 5 例"
```

---

### Task 4: 族E-2 ETL + 导出 L2（TD-E06–E16，11 例）

**Files:**
- Modify: `server/tests/test_data_full_io.py`（追加 TD-E06–E16）

**Interfaces:**
- Consumes: Task 3 文件骨架；Global Constraints #10–#12。
- Produces: ETL 5 例 + 导出 6 例；`_wait_etl_log` 轮询助手。

- [ ] **Step 1: 追加 ETL 用例（TD-E06–E10）**

```python
def _etl_task(admin, pageh, mode='insert', match_field=None):
    steps = [
        {'id': 's1', 'name': '入数', 'type': 'json_input',
         'config': {'data': json.dumps([
             {'name': 'ETL甲', 'qty': 1}, {'name': 'ETL乙', 'qty': 2}])},
         'onError': 'stop'},
        {'id': 's2', 'name': '入库', 'type': 'save_to_collection',
         'config': {'collection': pageh['collection'], 'mode': mode,
                    **({'matchField': match_field} if match_field else {})},
         'onError': 'stop'},
    ]
    r = live.api('POST', '/etlTasks', admin,
                 {'name': f"DTEST-E-etl-{uuid.uuid4().hex[:8]}",
                  'description': '数据管理 e2e', 'steps': steps, 'enabled': True})
    assert r.status_code < 300, f'{r.status_code} {r.text[:300]}'
    return r.json()


def _wait_etl_log(admin, task_id, log_id, timeout_s=40):
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        g = live.api('GET', f'/etlTasks/{task_id}/logs/{log_id}', admin)
        if g.status_code == 200:
            last = g.json()
            if last.get('status') in ('success', 'partial', 'error', 'cancelled'):
                return last
        time.sleep(2)
    raise AssertionError(f'ETL 日志未在 {timeout_s}s 内终态: {last}')


def test_td_e06_etl_crud_and_steps_stored(admin, pageh):
    t = _etl_task(admin, pageh)
    try:
        got = live.api('GET', f"/etlTasks/{t['id']}", admin)
        assert got.status_code == 200
        assert got.json()['steps'][1]['type'] == 'save_to_collection'
        lst = live.api('GET', '/etlTasks', admin)
        assert any(x['id'] == t['id'] for x in lst.json())
    finally:
        live.api('DELETE', f"/etlTasks/{t['id']}", admin)


def test_td_e07_etl_dry_run_sync_no_side_effects(admin, pageh):
    t = _etl_task(admin, pageh)
    try:
        r = live.api('POST', f"/etlTasks/{t['id']}/run", admin, {'dryRun': True})
        assert r.status_code < 300, f'{r.status_code} {r.text[:300]}'
        body = r.json()
        assert body['totalRecords'] == 2 and body['successCount'] == 2
        after = _names(admin, pageh['collection'])
        # dryRun 回滚：零副作用（ETL 数据未落库）
        assert 'ETL甲' not in after and 'ETL乙' not in after
    finally:
        live.api('DELETE', f"/etlTasks/{t['id']}", admin)


def test_td_e08_etl_real_run_async_inserts_to_main(admin, pageh):
    t = _etl_task(admin, pageh)
    try:
        r = live.api('POST', f"/etlTasks/{t['id']}/run", admin, {})
        assert r.status_code == 202, f'真跑应 202 async，得 {r.status_code}'
        log = _wait_etl_log(admin, t['id'], r.json()['logId'])
        assert log['status'] == 'success', f"{log['status']} {log.get('errorDetail')}"
        assert log['totalRecords'] == 2 and log['successCount'] == 2
        names = _names(admin, pageh['collection'])
        assert 'ETL甲' in names and 'ETL乙' in names
    finally:
        live.api('DELETE', f"/etlTasks/{t['id']}", admin)


def test_td_e09_etl_upsert_mode_idempotent_rerun(admin, pageh):
    t = _etl_task(admin, pageh, mode='upsert', match_field='name')
    try:
        r1 = live.api('POST', f"/etlTasks/{t['id']}/run", admin, {})
        log1 = _wait_etl_log(admin, t['id'], r1.json()['logId'])
        assert log1['status'] == 'success'
        r2 = live.api('POST', f"/etlTasks/{t['id']}/run", admin, {})
        log2 = _wait_etl_log(admin, t['id'], r2.json()['logId'])
        assert log2['status'] == 'success'
        names = _names(admin, pageh['collection'])
        assert sum(1 for n in names if n.startswith('ETL')) == 2  # 重跑不翻倍
    finally:
        live.api('DELETE', f"/etlTasks/{t['id']}", admin)


def test_td_e10_etl_cancel_finished_409_and_logs_list(admin, pageh):
    t = _etl_task(admin, pageh)
    try:
        r = live.api('POST', f"/etlTasks/{t['id']}/run", admin, {})
        log = _wait_etl_log(admin, t['id'], r.json()['logId'])
        c = live.api('POST', f"/etlTasks/{t['id']}/logs/{log['id']}/cancel", admin, {})
        assert c.status_code == 409  # 任务已结束，无法取消
        logs = live.api('GET', f"/etlTasks/{t['id']}/logs", admin)
        assert logs.status_code == 200 and len(logs.json()) >= 1
    finally:
        live.api('DELETE', f"/etlTasks/{t['id']}", admin)
```

- [ ] **Step 2: 追加导出用例（TD-E11–E16）**

```python
CSV_SCRIPT = (
    "lines = [','.join([str(r.get('name','')), str(r.get('qty',''))]) for r in data]\n"
    "result = '\\n'.join(lines)\n"
    "filename = 'dtest-export.csv'\n"
    "content_type = 'text/csv'\n"
)


def _export_script(admin, collection):
    r = live.api('POST', '/exportScripts', admin, {
        'name': f"DTEST-E-exp-{uuid.uuid4().hex[:8]}",
        'description': '数据管理 e2e', 'language': 'python',
        'script': CSV_SCRIPT, 'outputFormat': 'csv',
        'scope': 'page', 'boundCollection': collection})
    assert r.status_code < 300, f'{r.status_code} {r.text[:300]}'
    return r.json()


def test_td_e11_export_script_crud_unique_name(admin, pageh):
    s = _export_script(admin, pageh['collection'])
    try:
        dup = live.api('POST', '/exportScripts', admin, {
            'name': s['name'], 'script': CSV_SCRIPT, 'outputFormat': 'csv',
            'scope': 'page', 'boundCollection': pageh['collection']})
        assert dup.status_code == 400  # 名称全局唯一
        lst = live.api('GET', f"/exportScripts/for-collection/{pageh['collection']}",
                       admin)
        assert lst.status_code == 200
    finally:
        live.api('DELETE', f"/exportScripts/{s['id']}", admin)


def test_td_e12_export_test_preview_and_syntax_error(admin, pageh):
    s = _export_script(admin, pageh['collection'])
    try:
        live.api('POST', f"/{pageh['collection']}", admin,
                 {'id': uuid.uuid4().hex, 'name': '导出行', 'qty': 7})
        t = live.api('POST', f"/exportScripts/{s['id']}/test", admin,
                     {'collection': pageh['collection']})
        assert t.status_code < 300, f'{t.status_code} {t.text[:300]}'
        body = t.json()
        assert body['success'] is True and '导出行' in body['preview']
        bad = live.api('POST', '/exportScripts', admin, {
            'name': f"DTEST-E-bad-{uuid.uuid4().hex[:8]}",
            'script': 'result = (语法错误', 'outputFormat': 'csv',
            'scope': 'page', 'boundCollection': pageh['collection']})
        assert bad.status_code < 300  # 脚本存储不校验语法
        t2 = live.api('POST', f"/exportScripts/{bad.json()['id']}/test", admin,
                      {'collection': pageh['collection']})
        assert t2.status_code == 400 and t2.json().get('success') is False
        live.api('DELETE', f"/exportScripts/{bad.json()['id']}", admin)
    finally:
        live.api('DELETE', f"/exportScripts/{s['id']}", admin)


def test_td_e13_export_execute_binary_and_binding_mismatch(admin, pageh):
    s = _export_script(admin, pageh['collection'])
    other = live.make_page(admin, 'E', 'exp-other', fields=[NAME])
    try:
        live.api('POST', f"/{pageh['collection']}", admin,
                 {'id': uuid.uuid4().hex, 'name': '执行行', 'qty': 3})
        ex = live.api('POST', '/exportScripts/execute', admin,
                      {'scriptId': s['id'], 'collection': pageh['collection']})
        assert ex.status_code == 200
        assert '执行行' in ex.content.decode('utf-8')
        mismatch = live.api('POST', '/exportScripts/execute', admin,
                            {'scriptId': s['id'], 'collection': other['collection']})
        assert mismatch.status_code == 400  # 绑定不符
    finally:
        live.drop_page(admin, other)
        live.api('DELETE', f"/exportScripts/{s['id']}", admin)


def test_td_e14_export_batch_zip(admin, pageh):
    s = _export_script(admin, pageh['collection'])
    try:
        live.api('POST', f"/{pageh['collection']}", admin,
                 {'id': uuid.uuid4().hex, 'name': '批导行', 'qty': 1})
        z = live.api('POST', '/exportScripts/batchExport', admin,
                     {'tasks': [{'scriptId': s['id'],
                                 'collection': pageh['collection']}]})
        assert z.status_code == 200
        assert z.content[:2] == b'PK'  # ZIP 魔数
    finally:
        live.api('DELETE', f"/exportScripts/{s['id']}", admin)


def test_td_e15_menu_export_preview_zip_and_batch_clear(admin):
    """batchClear 清空整个 collection——用独立页，不碰模块共享 pageh。"""
    solo = live.make_page(admin, 'E', 'menuexp', fields=[NAME, QTY])
    try:
        live.api('POST', f"/{solo['collection']}", admin,
                 {'id': uuid.uuid4().hex, 'name': '菜单导出行', 'qty': 5})
        av = live.api('GET', '/menuExport/availableMenus', admin)
        assert av.status_code == 200
        pv = live.api('POST', '/menuExport/preview', admin,
                      {'menuIds': [solo['menu_id']], 'branchId': 'main'})
        assert pv.status_code == 200
        body = pv.json()
        assert any(m['menuId'] == solo['menu_id'] for m in body['menus'])
        z = live.api('POST', '/menuExport', admin,
                     {'menuIds': [solo['menu_id']], 'branchId': 'main'})
        assert z.status_code == 200 and z.content[:2] == b'PK'
        clear = live.api('POST', '/menuExport/batchClear', admin,
                         {'collections': [solo['collection']], 'branchId': 'main'})
        assert clear.status_code < 300
        assert _names(admin, solo['collection']) == {}  # batchClear 清空分支记录
    finally:
        live.drop_page(admin, solo)


def test_td_e16_export_missing_result_assignment_rejected(admin, pageh):
    """实际契约：脚本不给 result 赋值 → test 端点 400 success:false。"""
    r = live.api('POST', '/exportScripts', admin, {
        'name': f"DTEST-E-nores-{uuid.uuid4().hex[:8]}",
        'script': "x = 1\n", 'outputFormat': 'csv',
        'scope': 'page', 'boundCollection': pageh['collection']})
    try:
        t = live.api('POST', f"/exportScripts/{r.json()['id']}/test", admin,
                     {'collection': pageh['collection']})
        assert t.status_code == 400 and t.json().get('success') is False
    finally:
        live.api('DELETE', f"/exportScripts/{r.json()['id']}", admin)
```

注意（执行者核对）：CSV_SCRIPT 的换行/引号在 Python 源码里是转义后的脚本文本——先本地验证脚本在 test 端点能跑通再定稿；`references`/`fields` locals 若影响执行以实测为准。E07 的 dryRun 断言若 totalRecords 口径不同（如含 sample 截断）按实测调整。E15 已用独立页（batchClear 清空整个 collection，不得指向共享 pageh）。

- [ ] **Step 3: 跑绿**

```bash
cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_data_full_io.py -v
```

Expected: 16 passed（E01–E16，两遍稳定）。

- [ ] **Step 4: Commit**

```bash
git add server/tests/test_data_full_io.py
git commit -m "test(data-full): 族E L2 ETL+导出 —— dryRun回滚/异步真跑/upsert幂等/cancel409/脚本test与execute/batchExport/menuExport 11 例"
```

---

### Task 5: 族D L3 分支与依赖 UI（TD-D17–D19）

**Files:**
- Create: `e2e/data-full/data-branches.spec.ts`（3 例）

**Interfaces:**
- Consumes: helpers（createDataPage 句柄需补 `projectMenuId`/`workspaceMenuId` 两个确定性键——预批扩展 DataPageHandle，同 Task 1 的 python 侧）。
- Produces: 族D L3 基线 3 例。

- [ ] **Step 1: 写 data-branches.spec.ts（TD-D17–D19）**

用例要点（先读 `src/components/common/ProjectVersionManager.vue`、`src/views/dynamic/DynamicPage.vue` 操作菜单命令 `version`/`dependency` 与头部分支下拉（non-guest）核实选择器）：

- **TD-D17 版本管理抽屉与建分支**：gotoWithAuth 打开 DTEST 页（其菜单挂在 project 下，抽屉可渲染）→ 操作菜单点「版本管理」（实际命令名以组件为准）→ 抽屉出现 → 在新建表单填名称（分支类型）提交 → 列表出现该分支 → API `GET /project-versions/<projectMenuId>` 断言存在。afterAll 走 API DELETE 分支 + deleteDataPage。
- **TD-D18 分支切换数据隔离**：API 建 2 条 main 记录 + 建分支 + switch；UI 侧（操作菜单或头部分支下拉，以实读为准）切到该分支 → 表格显示快照数据；API 在分支加 1 条 → UI 刷新后可见 → 切回主分支 → 表格回到 main 数据（分支新增不可见）。断言以表格行文本为准。
- **TD-D19 依赖管理抽屉**：同一页面操作菜单打开「依赖管理」（defaultTab dependencies）→ 抽屉出现且无报错（空态可见）→ 关闭。轻量冒烟。

截图：branch-drawer、branch-switched-table、dependency-drawer。

- [ ] **Step 2: 跑绿**

```bash
npx playwright test e2e/data-full/data-branches.spec.ts
```

Expected: 3 passed，两遍稳定；全量 `npx playwright test e2e/data-full` → 20 passed（17+3）。

- [ ] **Step 3: Commit**

```bash
git add e2e/data-full/helpers.ts e2e/data-full/data-branches.spec.ts
git commit -m "test(data-full): 族D L3 —— 版本管理抽屉/分支切换隔离/依赖抽屉 3 例"
```

---

### Task 6: 族E L3 导入与导出 UI（TD-E17–E18）

**Files:**
- Create: `e2e/data-full/data-io.spec.ts`（2 例）

**Interfaces:**
- Consumes: helpers；`xlsx` 包（前端 node_modules 已有，spec 里 `import * as XLSX from 'xlsx'` 生成真实 xlsx buffer——若 playwright 转译环境解析不了该包，退化用 SheetJS 支持的 CSV 文本经同一 file input 上传，报告中注明）。
- Produces: 族E L3 基线 2 例。

- [ ] **Step 1: 写 data-io.spec.ts（TD-E17–E18）**

用例要点（先读 `src/views/dynamic/DynamicPage.vue` 操作菜单 import/export 命令与隐藏 file input（约 :185、:3079-3085）、`src/utils/importPageRecords.ts`、`src/utils/excel.ts` 核实链路与列名约定——列头用字段 label「名称/数量」）：

- **TD-E17 导入 UI 全链路**：UI 建页（名称/数量两字段）→ 操作菜单点「导入」→ 隐藏 input `setInputFiles`（内存生成的 xlsx：sheet 名任意，两列「名称」「数量」3 行数据）→ 等待导入完成反馈（成功提示或行渲染，以实读为准）→ API 断言 3 行落库（id 由前端生成）→ `GET /importRuns` 断言新增一条 success 记录（fileName 匹配）。
- **TD-E18 导出 Excel 下载**：同一页面（≥1 行数据）→ 操作菜单点「导出」→ `page.waitForEvent('download')` 捕获下载 → 断言下载触发且文件非空（suggestedFilename 以实读为准）。

截图：io-imported-table、io-export-download。

- [ ] **Step 2: 跑绿**

```bash
npx playwright test e2e/data-full/data-io.spec.ts
```

Expected: 2 passed，两遍稳定；全量 → 22 passed（17+3+2）。

- [ ] **Step 3: Commit**

```bash
git add e2e/data-full/data-io.spec.ts
git commit -m "test(data-full): 族E L3 —— 导入 UI 全链路（xlsx→落库→历史）与导出下载 2 例"
```

---

### Task 7: 全量跑绿 + 文档收口

**Files:**
- Modify: `docs/data-testing/02-数据管理功能测试用例.md`（族D 表 19 行、族E 表 18 行）
- Modify: `docs/data-testing/01-测试方案.md`（环境事实 #13–#16）
- Modify: `docs/data-testing/04-缺陷记录与修复.md`（已知限制补录）

**Interfaces:**
- Produces: 族D/E 基线落表；全量口径 L2 42+31=73、L3 17+5=22。

- [ ] **Step 1: 全量跑**

```bash
cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_data_full_crud.py tests/test_data_full_field_types.py tests/test_data_full_workflow.py tests/test_data_full_relations.py tests/test_data_full_branches.py tests/test_data_full_io.py -q
npx playwright test e2e/data-full
```

Expected: L2 73 passed；L3 22 passed（两遍）。DTEST 残留清扫扩到全部已知残留表（Global Constraints #14 十四表：menus/page_configs/dynamic_data/roles/users/data_files/import_runs/merge_records/merge_backups/project_versions/project_version_snapshots/user_current_project_branch/workflow_definitions/workflow_instances），报告各表 DTEST 计数（无 API 清理路径的表如实报数）。

- [ ] **Step 2: 文档收口**

- 02：族D 表（TD-D01–D08+D05b、D09–D14、D17–D19 共 18 行）与族E 表（TD-E01–E18 共 18 行），执行结果按实测；关键契约落单元格（D05 merge theirs、D06 403 文本、E01 upsert、E07 dryRun 回滚、E08 async 202、E13 绑定不符 400、E16 result 必须赋值）。
- 01 环境事实追加：
  - #13 分支：快照从创建者当前分支复制；分支状态 per-user；锁仅拦「当前分支=被锁分支」的写路径（GET/merge/restore 不受锁约束）；lock reason 不持久化。
  - #14 merge `strategy:'ours'` 是静默 no-op（只实现 theirs）——04 已知限制登记。
  - #15 导入解析在前端 SheetJS，`POST /importRuns` 纯历史登记且无 DELETE；batch-create 已存在 id=upsert 覆盖。
  - #16 ETL 真跑 async（202 + 2s 调度）、dryRun 同步回滚、save 恒写 main、`enabled` 为展示字段；ETL/依赖调度器在 dev 常驻（启动时全量校验依赖）。
- 04 已知限制追加：⑨ merge `strategy:'ours'` 静默 no-op（只实现 theirs）；⑩ import_runs/merge_records/merge_backups/user_current_project_branch 无 API 清理路径（残留清单，计划⑤）；⑪ 菜单导出**无导入端点**（spec 设想的「导出→导入回环」不可行，仅单向导出）。

- [ ] **Step 3: Commit + 自查**

```bash
git add docs/data-testing/
git commit -m "docs(data-testing): 族D/E 用例落表——L2 73 例 + L3 22 例全量绿 + ours no-op/菜单导出无导入 等已知限制登记"
```

自查：`git status` 干净；`git log --oneline` 含本计划 7 任务 commit；截图目录有 branch-drawer / io-imported-table / io-export-download 等。

---

## 计划④⑤ 接续说明（不在本计划内实施）

- **计划④ 族F/G/H**：触发规则/校验脚本/Webhook（本地 stub）/行操作；列视图+查询台；评论/时间线/备份。
- **计划⑤ 收口**：02 全表定稿、首轮全量报告（03）、残留一次性清理（data_files/import_runs/merge_records/merge_backups/user_current_project_branch/workflow 两表）、user-guide 核对。
