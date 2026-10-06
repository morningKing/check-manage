# 数据管理 E2E 测试体系 · 计划②：族B 字段控制类型 + 族C 关联体系 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按计划①定型的模式扩展数据管理 E2E 体系到族B（字段控制类型，含 workflow 两套状态机）与族C（关联体系），L2 24 例 + L3 10 例全绿，02/01 文档同步。

**Architecture:** 镜像计划①：L2 live-server pytest（server/tests/test_data_full_*.py，打 Vite :5173 代理）+ L3 Playwright（e2e/data-full/，API 断言为主 + 关键 UI 真实交互）。复用 `data_full_live.py` 与 `e2e/data-full/helpers.ts`；导入机制在 Task 1 定型为 conftest 统一 sys.path。

**Tech Stack:** pytest + requests（L2）、Playwright + TypeScript（L3）、Flask :3002 + Vite :5173 + PostgreSQL。

**Spec:** [docs/superpowers/specs/2026-10-06-data-management-e2e-design.md](../specs/2026-10-06-data-management-e2e-design.md) §5 族B/族C；前置计划① `2026-10-06-data-management-e2e-plan1-scaffold-family-a.md`（helpers/约定/基线：L2 18 + L3 7 全绿）。

## Global Constraints

- 测试资产前缀 `DTEST-<族>-<用途>-<时间戳>`；每用例自建/自清（`make_page`/`drop_page` 或 TS helpers 同款，含 workspace→project→data 三级菜单链）；禁止触碰非 DTEST 数据。
- L2 跑法：`cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_data_full_<file>.py -v`（Git Bash 前缀式）；服务不在 skip（marker `data_full`）；`data_full_live.BASE` 默认 `http://localhost:5173`（Vite 代理 /api → Flask :3002）。
- L3 跑法：`npx playwright test e2e/data-full`（workers=1）；表单控件定位用 placeholder/label 锚点；Element Plus 弹层挂 body 下不要在 dialog 作用域内找；el-dialog 用 `.el-dialog:visible`。
- 后端改代码必须手动重启 Flask（无自动 reload）；改前端 Vite 热更无需重启。
- 缺陷流程硬规则：新测试先在修复前 commit 上失败再修；发现即修记 04 文档。
- **族B/C 已核实 API 事实**（2026-10-06 研究代理核实，file:line 备案；写测试勿凭记忆改写）：
  1. **autoSequence**：`sequenceConfig {prefix, max}`，pad 服务端推导 `len(str(max))`；仅 create 分配且**覆盖客户端值**（响应读生成值）；并发安全=计数行 FOR UPDATE + PK advisory lock；**PUT 不重分配**（存什么是什么，计数器不动）；**batch-create 不分配**，导入值原样保留且计数器 reseed 到 GREATEST；max 软上限超宽不报错。
  2. **autoTimestamp**：**纯前端**（store 创建/更新时写 ISO）；服务端零处理——API 直连不填充，L2 断言「服务端不补」，L3 断言 UI 填充/刷新。
  3. **compositeText**：`{sourceFields, separator}`（默认 `' - '`）；**前端计算**（create/update 由 store 重算）；服务端不重算——API 直改源字段 composite 残留旧值（L2 断言此事实）；store update 重算基于 patch。
  4. **select/multiSelect/radio/checkbox**：**服务端无选项校验**，任意值直接进 JSONB（测试按实际契约断言并在注释说明）。
  5. **date/datetime**：原样存储客户端字符串；前端 value-format：date `YYYY-MM-DD`、datetime `YYYY-MM-DD HH:mm:ss`（空格无 T）。
  6. **file/image**：`POST /api/data-files/upload` multipart（`file` 必需，`collection`/`fieldName` 可选）→ 201 `{id,name,size,mimeType,url:'/api/data-files/<id>/download'}`；记录字段值为数组 `[{uid,name,url,size,type}]`（uid=file id）；扩展名白名单 400；默认上限 50MB → 413；**无服务端删除端点**；下载端点支持 `?access_token=`。
  7. **markdown**：服务端零处理，原样 JSONB，前端渲染。
  8. **字段级 workflowConfig**：`{enabled, transitions:[{from,to,label,roles?,conditions?,actions?}]}`；转换执行=记录 PUT（完整记录+状态字段=to+可选 `_workflowComment`），**不是** workflows.py 端点；服务端校验条件=字段有 enabled workflowConfig 且 old≠new 且 **old 非 None（首次写入完全绕过校验）**；非法转换/角色不符 → **400**；`roles: []` = 所有角色可转。
  9. **跨页编排（workflows.py）**：`POST /workflow/definitions` payload `{id?, name, description?, enabled, stages[], edges[]}` upsert，stage `{id,name,collection,statusField,assignedRoles[],advanceTransition{from,to},rejectTransition?,spawn?}`，无 edges 回退线性链，响应含非阻断 `warnings[]`；`POST /workflow/instances` `{workflowId, collection, recordId}` → 201 **snake_case** `{id:'wfi-*', workflow_id, status:'running', current_stage_id, chain, ...}`，同记录重复运行中实例 409；实例推进=记录 PUT 使 statusField 从 stage.advanceTransition.from→to（stage 按 collection+statusField 匹配）；`assignedRoles` 不符 → **403 且整个事务回滚**（状态字段也不变）；`GET /workflow/inbox` 运行中实例且 user.role∈assignedRoles 才出现，**空数组=所有人可见**，item `{kind:'workflow', instanceId, workflowName, stageName, collection, recordId, enteredAt}`。
  10. **relation (M:N)**：`GET /relations/<c>` → `{record_id:{field:[ids]}}`、`GET /relations/<c>/<rid>` → `{field:[ids]}`（仅需登录）；`PUT /relations/<c>/<rid>/<field>` payload `{targetCollection, targetField, ids}` = **全量替换**+双向同步（删 removed 反向行、added 反向行），响应 `{ids}`，**记录不存在也不 404**；`DELETE /relations/<c>/<rid>` 双向清空；create/update 可走 body `_relations:[{fieldName,targetCollection,targetField,ids}]` 同事务原子写；记录删除双向清理。
  11. **reference (1:N)**：`ReferenceConfig {targetCollection, displayField, inheritFields[]}`；子记录存父 id 字符串；**继承是显示层**（`_ref_<field>_<f>` 键），服务端不拷贝不重算；**父删除 RESTRICT**：有子引用 → 409 `无法删除：被「…」引用`，batch-delete 同样 blocked。
  12. **quoteSelect**：值为 JSONB **id 字符串数组**，不写 data_relations；被引记录删除后**悬挂 id 不清理**（前端回退显示原始 id）。
  13. **relation-graph**：`GET /relation-graph/<c>/<rid>` → `{nodes:[{id,label,collection,collectionLabel,data}], edges:[{source,target,label,relType}], centerId}`，relType∈`relation|reference|quoteSelect`，单跳两级。
  14. **UI pickers**：relation/reference/quoteSelect 均为 `el-select-v2` filterable remote（`src/components/dynamic-form/controls/`）；字段级 workflow 转换按钮只在**查看弹窗 footer**（WorkflowActions），guest 不可见。

---

### Task 1: conftest 导入定型 + 族B L2 基础控制类型

**Files:**
- Modify: `server/tests/conftest.py`（sys.path 补 tests 目录）
- Modify: `server/tests/test_data_full_crud.py`（移除文件内 sys.path hack）
- Create: `server/tests/test_data_full_field_types.py`（TD-B01–B09）

**Interfaces:**
- Consumes: `data_full_live.py`（make_page/drop_page/api/CRUD_FIELDS/CRUD_FIELDS 字段结构——本任务新增族B专用字段集，走 make_page 的 fields 参数）。
- Produces: 后续所有族文件统一 `import data_full_live as live`（conftest 保证可导入，无需 per-file hack）；族B L2 基线 9 例。

- [ ] **Step 1: conftest 统一导入路径**

`server/tests/conftest.py` 现有 `sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))`（指向 server/）。在其后追加一行，把 tests/ 目录也加入（conftest 在任何测试模块 import 前执行，全局生效）：

```python
# data_full_* 系列以平铺名 import 同目录助手（data_full_live）；
# 在 conftest 统一补路径，各测试文件不再自带 sys.path hack。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
```

同时把 `server/tests/test_data_full_crud.py` 头部的三行 hack（`sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))` 及其注释）删除，保留 `import data_full_live as live`。跑一遍 crud 套件确认 18 passed 不回归。

- [ ] **Step 2: 写 test_data_full_field_types.py（TD-B01–B09）**

```python
"""族B 字段控制类型 —— L2 live-server API 层（TD-B01–B09）。

已核实契约（计划② Global Constraints #1–#7）：autoSequence 仅 create 服务端
分配且覆盖客户端值 / PUT 不重分配 / batch-create 不分配且 reseed；
autoTimestamp 与 compositeText 纯前端（服务端零处理，L2 断言该事实）；
select 无服务端选项校验；date/datetime 原样存储。
"""
import concurrent.futures

import pytest

import data_full_live as live

pytestmark = pytest.mark.data_full

B_FIELDS = [
    {'id': 'f1', 'label': '名称', 'fieldName': 'name', 'controlType': 'text',
     'required': True, 'order': 1},
    {'id': 'f2', 'label': '单号', 'fieldName': 'sn', 'controlType': 'autoSequence',
     'required': False, 'order': 2,
     'sequenceConfig': {'prefix': 'DTS-', 'max': 999}},
    {'id': 'f3', 'label': '状态', 'fieldName': 'sel', 'controlType': 'select',
     'required': False, 'order': 3,
     'options': [{'label': '待处理', 'value': 'todo'},
                 {'label': '进行中', 'value': 'doing'}]},
    {'id': 'f4', 'label': '日期', 'fieldName': 'd', 'controlType': 'date',
     'required': False, 'order': 4},
    {'id': 'f5', 'label': '时间', 'fieldName': 'dt', 'controlType': 'datetime',
     'required': False, 'order': 5},
    {'id': 'f6', 'label': '自动时间戳', 'fieldName': 'ts', 'controlType': 'autoTimestamp',
     'required': False, 'order': 6},
    {'id': 'f7', 'label': '组合文本', 'fieldName': 'comp', 'controlType': 'compositeText',
     'required': False, 'order': 7,
     'compositeTextConfig': {'sourceFields': ['name'], 'separator': ' - '}},
]


@pytest.fixture(scope='module')
def admin():
    if not live.server_up():
        pytest.skip('dev 栈未启动（Vite :5173 代理 /api → Flask :3002）')
    return live.login()


@pytest.fixture(scope='module')
def pageh(admin):
    page = live.make_page(admin, 'B', 'types', fields=B_FIELDS)
    yield page
    live.drop_page(admin, page)


def _post(admin, collection, **data):
    return live.api('POST', f'/{collection}', admin, data)


def test_td_b01_autosequence_format_and_increment(admin, pageh):
    r1 = _post(admin, pageh['collection'], {'id': uuid.uuid4().hex, 'name': '甲'})
    r2 = _post(admin, pageh['collection'], {'id': uuid.uuid4().hex, 'name': '乙'})
    assert r1.status_code == 201 and r2.status_code == 201
    # 服务端覆盖客户端值（客户端未传 sn），pad=len('999')=3
    assert r1.json()['sn'] == 'DTS-001', f"得 {r1.json().get('sn')}"
    assert r2.json()['sn'] == 'DTS-002'


def test_td_b02_autosequence_concurrent_no_duplicates(admin, pageh):
    """8 并发创建：计数行 FOR UPDATE + advisory lock 保证不重号。"""
    def create(i):
        return _post(admin, pageh['collection'],
                     {'id': __import__('uuid').uuid4().hex, 'name': f'并发{i}'}).json()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        sns = [f.get('sn') for f in ex.map(create, range(8))]
    assert all(sns), f'缺 sn: {sns}'
    assert len(set(sns)) == 8, f'重号: {sns}'


def test_td_b03_autosequence_put_not_reallocated(admin, pageh):
    rec = _post(admin, pageh['collection'],
                {'id': __import__('uuid').uuid4().hex, 'name': '改写'}).json()
    put = live.api('PUT', f"/{pageh['collection']}/{rec['id']}", admin,
                   {'sn': 'XX-999', '_version': rec['_version']})
    assert put.status_code < 300
    after = live.api('GET', f"/{pageh['collection']}/{rec['id']}", admin).json()
    assert after['sn'] == 'XX-999'  # PUT 存什么是什么
    nxt = _post(admin, pageh['collection'],
                {'id': __import__('uuid').uuid4().hex, 'name': '后续'}).json()
    assert nxt['sn'].startswith('DTS-')  # 计数器未受 PUT 影响，继续自增


def test_td_b04_autosequence_batch_no_alloc_and_reseed(admin, pageh):
    """batch-create 不分配：导入值原样保留，且计数器 reseed 到 GREATEST。"""
    r = live.api('POST', f"/{pageh['collection']}/batch-create", admin,
                 {'records': [{'id': __import__('uuid').uuid4().hex,
                               'data': {'name': '导入甲', 'sn': 'DTS-050'}} for _ in range(1)]})
    assert r.status_code < 300
    got = live.api('GET', f"/{pageh['collection']}", admin,
                   ).json() if False else live.api('GET', f"/{pageh['collection']}?keyword=导入甲", admin).json()
    assert got['data'][0]['sn'] == 'DTS-050'
    nxt = _post(admin, pageh['collection'],
                {'id': __import__('uuid').uuid4().hex, 'name': 'reseed后'}).json()
    assert nxt['sn'] == 'DTS-051', f'reseed 到 GREATEST 后应 051，得 {nxt["sn"]}'


def test_td_b05_select_no_server_validation(admin, pageh):
    """实际契约：服务端不校验选项，任意值直接入库（前端负责约束）。"""
    r = _post(admin, pageh['collection'],
              {'id': __import__('uuid').uuid4().hex, 'name': '非法值', 'sel': '不存在的选项'})
    assert r.status_code == 201
    assert live.api('GET', f"/{pageh['collection']}/{r.json()['id']}", admin).json()['sel'] == '不存在的选项'


def test_td_b06_date_datetime_stored_verbatim(admin, pageh):
    rec = _post(admin, pageh['collection'],
                {'id': __import__('uuid').uuid4().hex, 'name': '日期',
                 'd': '2026-01-02', 'dt': '2026-01-02 03:04:05'}).json()
    got = live.api('GET', f"/{pageh['collection']}/{rec['id']}", admin).json()
    assert got['d'] == '2026-01-02' and got['dt'] == '2026-01-02 03:04:05'  # 无归一化


def test_td_b07_autotimestamp_server_absent(admin, pageh):
    """实际契约：autoTimestamp 纯前端填充，API 直连创建服务端不补值。"""
    rec = _post(admin, pageh['collection'],
                {'id': __import__('uuid').uuid4().hex, 'name': '无ts'}).json()
    assert 'ts' not in rec, f'服务端不应填充 ts: {rec.get("ts")}'


def test_td_b08_compositetext_server_absent(admin, pageh):
    """实际契约：compositeText 前端计算，API 直连创建服务端不计算。"""
    rec = _post(admin, pageh['collection'],
                {'id': __import__('uuid').uuid4().hex, 'name': '组合'}).json()
    assert 'comp' not in rec, f'服务端不应计算 comp: {rec.get("comp")}'


def test_td_b09_file_upload_endpoint(admin, pageh):
    """上传→下载回读→扩展名白名单 400。记录值数组契约由 L3 UI 链路覆盖。"""
    r = live.upload_file(admin, 'hello 数据文件'.encode('utf-8'), 'dtest-b.txt',
                         collection=pageh['collection'], field_name='name')
    assert r.status_code == 201, f'{r.status_code} {r.text[:200]}'
    body = r.json()
    assert body['url'] == f"/api/data-files/{body['id']}/download"
    dl = live.api('GET', f"/data-files/{body['id']}/download", admin)
    assert dl.status_code == 200 and dl.content.decode('utf-8') == 'hello 数据文件'
    bad = live.upload_file(admin, b'x', 'dtest-b.sh', collection=pageh['collection'])
    assert bad.status_code == 400  # 扩展名白名单兜底
```

注意（执行者核对）：`test_data_full_field_types.py` 顶部统一 `import uuid`（B01–B04、B06–B08 均用 `uuid.uuid4().hex`）；`data_full_live.py` 需新增 `upload_file(headers, content: bytes, filename: str, collection=None, field_name=None)` 助手（requests multipart：`files={'file': (filename, content, 'application/octet-stream')}, data={'collection': ..., 'fieldName': ...}`），后续族复用。

- [ ] **Step 3: 跑绿**

```bash
cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_data_full_field_types.py tests/test_data_full_crud.py -v
```

Expected: 9 + 18 = 27 passed。

- [ ] **Step 4: Commit**

```bash
git add server/tests/conftest.py server/tests/test_data_full_crud.py server/tests/data_full_live.py server/tests/test_data_full_field_types.py
git commit -m "test(data-full): 族B L2 —— autoSequence/存储契约/文件上传 9 例 + conftest 导入定型"
```

---

### Task 2: 族B L2 workflow 状态机（字段级 + 跨页编排）

**Files:**
- Create: `server/tests/test_data_full_workflow.py`（TD-B10–B14）

**Interfaces:**
- Consumes: `data_full_live`（make_page 支持自定义字段）；Global Constraints #8–#9。
- Produces: workflow L2 基线 5 例；04 文档若发现角色语义新事实则登记。

- [ ] **Step 1: 写 test_data_full_workflow.py（TD-B10–B14）**

```python
"""族B workflow 状态机 —— L2 live-server API 层（TD-B10–B14）。

两套并存（计划② Global Constraints #8/#9）：
- 字段级 workflowConfig：转换=记录 PUT（完整记录+状态=to），非法/角色不符 400，
  首次写入（old=None）完全绕过校验；
- 跨页编排 definitions/instances/inbox：实例响应 snake_case；stage 推进=记录 PUT
  使 statusField 从 advanceTransition.from→to；assignedRoles 不符 403 且回滚；
  assignedRoles 空数组=所有人可见（用空数组与『必然不存在角色』两个锚点规避
  内置角色 id/名语义陷阱）。
"""
import uuid

import pytest

import data_full_live as live

pytestmark = pytest.mark.data_full


def _status_field(field_name='status', transitions=None, enabled=True):
    return {'id': 'f-status', 'label': '状态', 'fieldName': field_name,
            'controlType': 'select', 'required': False, 'order': 1,
            'options': [{'label': '待处理', 'value': 'todo'},
                        {'label': '进行中', 'value': 'doing'},
                        {'label': '已完成', 'value': 'done'}],
            'workflowConfig': {'enabled': enabled, 'transitions': transitions or []}}


@pytest.fixture(scope='module')
def admin():
    if not live.server_up():
        pytest.skip('dev 栈未启动')
    return live.login()


def _create_record(admin, collection, status='todo'):
    r = live.api('POST', f'/{collection}', admin,
                 {'id': uuid.uuid4().hex, 'status': status})
    assert r.status_code == 201
    return r.json()


def test_td_b10_field_workflow_valid_transition(admin):
    trans = [{'from': 'todo', 'to': 'doing', 'label': '开始'}]
    page = live.make_page(admin, 'B', 'wf-ok',
                          fields=[_status_field(transitions=trans)])
    try:
        rec = _create_record(admin, page['collection'])  # 首次写入绕过校验
        put = live.api('PUT', f"/{page['collection']}/{rec['id']}", admin,
                       {'status': 'doing', '_version': rec['_version']})
        assert put.status_code < 300, f'{put.status_code} {put.text[:200]}'
        assert live.api('GET', f"/{page['collection']}/{rec['id']}",
                        admin).json()['status'] == 'doing'
    finally:
        live.drop_page(admin, page)


def test_td_b11_field_workflow_invalid_transition_400(admin):
    trans = [{'from': 'todo', 'to': 'doing', 'label': '开始'}]  # 无 todo→done 边
    page = live.make_page(admin, 'B', 'wf-bad',
                          fields=[_status_field(transitions=trans)])
    try:
        rec = _create_record(admin, page['collection'])
        put = live.api('PUT', f"/{page['collection']}/{rec['id']}", admin,
                       {'status': 'done', '_version': rec['_version']})
        assert put.status_code == 400, f'非法转换应 400，得 {put.status_code}'
        assert live.api('GET', f"/{page['collection']}/{rec['id']}",
                        admin).json()['status'] == 'todo'
    finally:
        live.drop_page(admin, page)


def test_td_b12_field_workflow_role_gate_400_and_empty_roles_allowing(admin):
    # roles:['no-such-role-x'] 对任何用户都拒绝；roles:[] 对所有角色放行
    page = live.make_page(admin, 'B', 'wf-role', fields=[
        _status_field(field_name='status', transitions=[
            {'from': 'todo', 'to': 'doing', 'label': '开始', 'roles': []},
        ]),
    ])
    try:
        rec = _create_record(admin, page['collection'])
        put = live.api('PUT', f"/{page['collection']}/{rec['id']}", admin,
                       {'status': 'doing', '_version': rec['_version']})
        assert put.status_code < 300  # roles:[] = 所有角色可转
        page2 = live.make_page(admin, 'B', 'wf-role2', fields=[
            _status_field(field_name='status', transitions=[
                {'from': 'todo', 'to': 'doing', 'label': '开始',
                 'roles': ['no-such-role-x']},
            ]),
        ])
        try:
            rec2 = _create_record(admin, page2['collection'])
            put2 = live.api('PUT', f"/{page2['collection']}/{rec2['id']}", admin,
                            {'status': 'doing', '_version': rec2['_version']})
            assert put2.status_code == 400
            assert live.api('GET', f"/{page2['collection']}/{rec2['id']}",
                            admin).json()['status'] == 'todo'
        finally:
            live.drop_page(admin, page2)
    finally:
        live.drop_page(admin, page)


def test_td_b13_orchestration_definition_instance_and_dup_409(admin):
    page = live.make_page(admin, 'B', 'orch', fields=[_status_field()])
    try:
        d = live.api('POST', '/workflow/definitions', admin, {
            'name': f"DTEST-B-orch-def-{uuid.uuid4().hex[:8]}",
            'enabled': True,
            'stages': [{'id': 's1', 'name': '处理', 'collection': page['collection'],
                        'statusField': 'status', 'assignedRoles': [],
                        'advanceTransition': {'from': 'todo', 'to': 'doing'}}],
            'edges': [],
        })
        assert d.status_code < 300, f'{d.status_code} {d.text[:300]}'
        wid = d.json()['id']
        rec = _create_record(admin, page['collection'])
        inst = live.api('POST', '/workflow/instances', admin,
                        {'workflowId': wid, 'collection': page['collection'],
                         'recordId': rec['id']})
        assert inst.status_code == 201, f'{inst.status_code} {inst.text[:300]}'
        body = inst.json()
        assert body['status'] == 'running' and body['id'].startswith('wfi-')
        assert body['workflow_id'] == wid  # snake_case 契约
        dup = live.api('POST', '/workflow/instances', admin,
                       {'workflowId': wid, 'collection': page['collection'],
                        'recordId': rec['id']})
        assert dup.status_code == 409  # 同记录重复运行中实例
        inbox = live.api('GET', '/workflow/inbox', admin)
        items = [i for i in inbox.json() if i.get('kind') == 'workflow'
                 and i.get('instanceId') == body['id']]
        assert items, f'inbox 应含该实例: {str(inbox.json())[:300]}'
        assert items[0]['collection'] == page['collection']
    finally:
        live.drop_page(admin, page)


def test_td_b14_orchestration_advance_via_record_put_and_role_403(admin):
    page = live.make_page(admin, 'B', 'orch2', fields=[_status_field()])
    try:
        d = live.api('POST', '/workflow/definitions', admin, {
            'name': f"DTEST-B-orch2-{uuid.uuid4().hex[:8]}",
            'enabled': True,
            'stages': [{'id': 's1', 'name': '处理', 'collection': page['collection'],
                        'statusField': 'status', 'assignedRoles': [],
                        'advanceTransition': {'from': 'todo', 'to': 'doing'}}],
            'edges': [],
        })
        wid = d.json()['id']
        rec = _create_record(admin, page['collection'])
        inst = live.api('POST', '/workflow/instances', admin,
                        {'workflowId': wid, 'collection': page['collection'],
                         'recordId': rec['id']}).json()
        put = live.api('PUT', f"/{page['collection']}/{rec['id']}", admin,
                       {'status': 'doing', '_version': rec['_version']})
        assert put.status_code < 300
        # 推进后实例应脱离 running（inbox 不再出现该实例）
        inbox = live.api('GET', '/workflow/inbox', admin).json()
        assert not [i for i in inbox if i.get('kind') == 'workflow'
                    and i.get('instanceId') == inst['id']]
        # 403 分支：assignedRoles 必然不匹配的角色 → 403 且状态回滚
        d2 = live.api('POST', '/workflow/definitions', admin, {
            'name': f"DTEST-B-orch3-{uuid.uuid4().hex[:8]}",
            'enabled': True,
            'stages': [{'id': 's1', 'name': '受限', 'collection': page['collection'],
                        'statusField': 'status', 'assignedRoles': ['no-such-role-x'],
                        'advanceTransition': {'from': 'doing', 'to': 'done'}}],
            'edges': [],
        })
        rec2 = _create_record(admin, page['collection'], status='doing')
        live.api('POST', '/workflow/instances', admin,
                 {'workflowId': d2.json()['id'], 'collection': page['collection'],
                  'recordId': rec2['id']})
        put2 = live.api('PUT', f"/{page['collection']}/{rec2['id']}", admin,
                        {'status': 'done', '_version': rec2['_version']})
        assert put2.status_code == 403, f'角色不符应 403，得 {put2.status_code}'
        assert live.api('GET', f"/{page['collection']}/{rec2['id']}",
                        admin).json()['status'] == 'doing'  # 整个事务回滚
    finally:
        live.drop_page(admin, page)
```

注意（执行者核对）：实例推进后断言用 inbox 摘除即可（无需实例详情端点）。若 `POST /workflow/definitions` 响应无 `id` 键，读实际响应结构取之并在报告注明。

- [ ] **Step 2: 跑绿**

```bash
cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_data_full_workflow.py -v
```

Expected: 5 passed。

- [ ] **Step 3: Commit**

```bash
git add server/tests/test_data_full_workflow.py
git commit -m "test(data-full): 族B L2 workflow——字段级转换/角色门禁 + 编排 definitions/instances/inbox 5 例"
```

---

### Task 3: 族C L2 关联体系

**Files:**
- Create: `server/tests/test_data_full_relations.py`（TD-C01–C10）

**Interfaces:**
- Consumes: `data_full_live`；Global Constraints #10–#13。
- Produces: 族C L2 基线 10 例；`make_page` 若需支持 relation/reference/quote 字段配置直接经 fields 参数传入（无需改助手）。

- [ ] **Step 1: 写 test_data_full_relations.py（TD-C01–C10）**

```python
"""族C 关联体系 —— L2 live-server API 层（TD-C01–C10）。

契约锚点（计划② Global Constraints #10–#13）：PUT /relations 全量替换+双向同步、
记录不存在不 404；reference 继承仅显示层、父删除 RESTRICT 409（单条与批量）；
quoteSelect 存 id 数组、被引删除留悬挂 id；relation-graph 单跳两级。
"""
import uuid

import pytest

import data_full_live as live

pytestmark = pytest.mark.data_full

NAME = {'id': 'f1', 'label': '名称', 'fieldName': 'name',
        'controlType': 'text', 'required': True, 'order': 1}


def _relation_field(fid, label, target, target_field='rel', order=2):
    return {'id': fid, 'label': label, 'fieldName': 'rel', 'controlType': 'relation',
            'required': False, 'order': order,
            'relationConfig': {'targetCollection': target, 'displayField': 'name',
                               'targetField': target_field}}


@pytest.fixture(scope='module')
def admin():
    if not live.server_up():
        pytest.skip('dev 栈未启动')
    return live.login()


@pytest.fixture(scope='module')
def ab_pages(admin):
    """A/B 两个互指 relation 字段的页面（双向 M:N 标准拓扑）。"""
    pa = live.make_page(admin, 'C', 'rel-a', fields=[
        NAME, _relation_field('f2', '关联', 'PLACEHOLDER_B')])
    pb = live.make_page(admin, 'C', 'rel-b', fields=[
        NAME, _relation_field('f2', '关联', pa['collection'])])
    # A 页的 relationConfig 目标在创建 B 页前先占位——需要回写修正为真实 collection
    fields = [NAME, _relation_field('f2', '关联', pb['collection'])]
    fix = live.api('PUT', f"/pageConfigs/{pa['page_id']}", admin, {'fields': fields})
    assert fix.status_code < 300, f'{fix.status_code} {fix.text[:200]}'
    pa['fields'] = fields
    yield pa, pb
    live.drop_page(admin, pa)
    live.drop_page(admin, pb)


def _rec(admin, collection, name):
    r = live.api('POST', f'/{collection}', admin, {'id': uuid.uuid4().hex, 'name': name})
    assert r.status_code == 201
    return r.json()


def test_td_c01_relation_put_and_get(admin, ab_pages):
    pa, pb = ab_pages
    a, b = _rec(admin, pa['collection'], 'A甲'), _rec(admin, pb['collection'], 'B甲')
    put = live.api('PUT', f"/relations/{pa['collection']}/{a['id']}/rel", admin,
                   {'targetCollection': pb['collection'], 'targetField': 'rel',
                    'ids': [b['id']]})
    assert put.status_code < 300 and put.json().get('ids') == [b['id']]
    got = live.api('GET', f"/relations/{pa['collection']}/{a['id']}", admin)
    assert got.json().get('rel') == [b['id']]


def test_td_c02_relation_reverse_visible(admin, ab_pages):
    pa, pb = ab_pages
    a, b = _rec(admin, pa['collection'], 'A乙'), _rec(admin, pb['collection'], 'B乙')
    live.api('PUT', f"/relations/{pa['collection']}/{a['id']}/rel", admin,
             {'targetCollection': pb['collection'], 'targetField': 'rel', 'ids': [b['id']]})
    rev = live.api('GET', f"/relations/{pb['collection']}/{b['id']}", admin)
    assert rev.json().get('rel') == [a['id']]  # B 侧反向可见


def test_td_c03_relation_full_replace(admin, ab_pages):
    pa, pb = ab_pages
    a = _rec(admin, pa['collection'], 'A丙')
    b1, b2, b3 = (_rec(admin, pb['collection'], f'B丙{i}') for i in (1, 2, 3))
    tgt = {'targetCollection': pb['collection'], 'targetField': 'rel'}
    live.api('PUT', f"/relations/{pa['collection']}/{a['id']}/rel", admin,
             {**tgt, 'ids': [b1['id'], b2['id']]})
    live.api('PUT', f"/relations/{pa['collection']}/{a['id']}/rel", admin,
             {**tgt, 'ids': [b2['id'], b3['id']]})
    assert live.api('GET', f"/relations/{pa['collection']}/{a['id']}",
                    admin).json()['rel'] == [b2['id'], b3['id']]
    # 被移除的 b1 反向行同步删除
    assert live.api('GET', f"/relations/{pb['collection']}/{b1['id']}",
                    admin).json().get('rel') in (None, [],)


def test_td_c04_relations_inline_on_create(admin, ab_pages):
    pa, pb = ab_pages
    b = _rec(admin, pb['collection'], 'B丁')
    r = live.api('POST', f"/{pa['collection']}", admin,
                 {'id': uuid.uuid4().hex, 'name': 'A丁',
                  '_relations': [{'fieldName': 'rel',
                                  'targetCollection': pb['collection'],
                                  'targetField': 'rel', 'ids': [b['id']]}]})
    assert r.status_code == 201
    assert live.api('GET', f"/relations/{pa['collection']}/{r.json()['id']}",
                    admin).json()['rel'] == [b['id']]


def test_td_c05_record_delete_cleans_both_directions(admin, ab_pages):
    pa, pb = ab_pages
    a, b = _rec(admin, pa['collection'], 'A戊'), _rec(admin, pb['collection'], 'B戊')
    live.api('PUT', f"/relations/{pa['collection']}/{a['id']}/rel", admin,
             {'targetCollection': pb['collection'], 'targetField': 'rel', 'ids': [b['id']]})
    assert live.api('DELETE', f"/{pa['collection']}/{a['id']}", admin).status_code < 300
    assert live.api('GET', f"/relations/{pb['collection']}/{b['id']}",
                    admin).json().get('rel') in (None, [],)


def test_td_c06_reference_parent_delete_restrict_409(admin):
    parent = live.make_page(admin, 'C', 'ref-p', fields=[NAME])
    child = live.make_page(admin, 'C', 'ref-c', fields=[
        NAME, {'id': 'f2', 'label': '父项', 'fieldName': 'ref', 'controlType': 'reference',
               'required': False, 'order': 2,
               'referenceConfig': {'targetCollection': parent['collection'],
                                   'displayField': 'name', 'inheritFields': ['name']}}])
    try:
        p = _rec(admin, parent['collection'], '父甲')
        c = _rec(admin, child['collection'], '子甲')
        live.api('PUT', f"/{child['collection']}/{c['id']}", admin,
                 {'ref': p['id'], '_version': c['_version']})
        single = live.api('DELETE', f"/{parent['collection']}/{p['id']}", admin)
        assert single.status_code == 409, f'RESTRICT 应 409，得 {single.status_code}'
        assert '引用' in single.json().get('error', '')
        batch = live.api('POST', f"/{parent['collection']}/batch-delete", admin,
                         {'ids': [p['id']]})
        assert batch.status_code == 409, f'批量删除同样 blocked，得 {batch.status_code}'
    finally:
        live.drop_page(admin, child)
        live.drop_page(admin, parent)


def test_td_c07_reference_inheritance_display_layer_only(admin):
    """实际契约：继承不落库——子记录 JSONB 无父字段拷贝，仅前端 _ref_ 显示键。"""
    parent = live.make_page(admin, 'C', 'ref-p2', fields=[NAME])
    child = live.make_page(admin, 'C', 'ref-c2', fields=[
        NAME, {'id': 'f2', 'label': '父项', 'fieldName': 'ref', 'controlType': 'reference',
               'required': False, 'order': 2,
               'referenceConfig': {'targetCollection': parent['collection'],
                                   'displayField': 'name', 'inheritFields': ['name']}}])
    try:
        p = _rec(admin, parent['collection'], '父乙')
        c = _rec(admin, child['collection'], '子乙')
        live.api('PUT', f"/{child['collection']}/{c['id']}", admin,
                 {'ref': p['id'], '_version': c['_version']})
        got = live.api('GET', f"/{child['collection']}/{c['id']}", admin).json()
        assert got['ref'] == p['id']
        assert 'name' not in got or got.get('name') == '子乙'  # 父 name 未被拷入
    finally:
        live.drop_page(admin, child)
        live.drop_page(admin, parent)


def test_td_c08_quoteselect_dangling_id_on_delete(admin):
    """实际契约：被引记录删除后引用方悬挂 id 不清理。"""
    q = live.make_page(admin, 'C', 'quote-q', fields=[NAME])
    a = live.make_page(admin, 'C', 'quote-a', fields=[
        NAME, {'id': 'f2', 'label': '引用', 'fieldName': 'quote',
               'controlType': 'quoteSelect', 'required': False, 'order': 2,
               'quoteConfig': {'targetCollection': q['collection'],
                               'displayField': 'name'}}])
    try:
        q1 = _rec(admin, q['collection'], '被引甲')
        r = _rec(admin, a['collection'], '引用方')
        live.api('PUT', f"/{a['collection']}/{r['id']}", admin,
                 {'quote': [q1['id']], '_version': r['_version']})
        assert live.api('DELETE', f"/{q['collection']}/{q1['id']}",
                        admin).status_code < 300
        got = live.api('GET', f"/{a['collection']}/{r['id']}", admin).json()
        assert got['quote'] == [q1['id']]  # 悬挂 id 保留（前端回退显示原始 id）
    finally:
        live.drop_page(admin, a)
        live.drop_page(admin, q)


def test_td_c09_relation_graph_two_level(admin):
    """A1 -relation- B1、A1 -reference- P1、A1 quoteSelect [Q1] → 单跳两级全在图上。"""
    parent = live.make_page(admin, 'C', 'g-p', fields=[NAME])
    qb = live.make_page(admin, 'C', 'g-q', fields=[NAME])
    gb = live.make_page(admin, 'C', 'g-b', fields=[
        NAME, _relation_field('f2', '关联', 'PLACEHOLDER')])
    ga = live.make_page(admin, 'C', 'g-a', fields=[
        NAME, _relation_field('f2', '关联', gb['collection']),
        {'id': 'f3', 'label': '父项', 'fieldName': 'ref', 'controlType': 'reference',
         'required': False, 'order': 3,
         'referenceConfig': {'targetCollection': parent['collection'],
                             'displayField': 'name', 'inheritFields': []}},
        {'id': 'f4', 'label': '引用', 'fieldName': 'quote', 'controlType': 'quoteSelect',
         'required': False, 'order': 4,
         'quoteConfig': {'targetCollection': qb['collection'], 'displayField': 'name'}}])
    try:
        p, q1 = _rec(admin, parent['collection'], '图父'), _rec(admin, qb['collection'], '图引')
        b1 = _rec(admin, gb['collection'], '图B')
        a1 = _rec(admin, ga['collection'], '图A')
        live.api('PUT', f"/relations/{ga['collection']}/{a1['id']}/rel", admin,
                 {'targetCollection': gb['collection'], 'targetField': 'rel',
                  'ids': [b1['id']]})
        live.api('PUT', f"/{ga['collection']}/{a1['id']}", admin,
                 {'ref': p['id'], 'quote': [q1['id']], '_version': a1['_version']})
        g = live.api('GET', f"/relation-graph/{ga['collection']}/{a1['id']}", admin)
        assert g.status_code == 200
        body = g.json()
        assert body['centerId'] == a1['id']
        node_ids = {n['id'] for n in body['nodes']}
        assert {b1['id'], p['id'], q1['id']} <= node_ids, f'邻居缺失: {node_ids}'
        rel_types = {e['relType'] for e in body['edges']}
        assert {'relation', 'reference', 'quoteSelect'} <= rel_types
    finally:
        for h in (ga, gb, qb, parent):
            live.drop_page(admin, h)


def test_td_c10_relations_put_missing_record_no_404(admin, ab_pages):
    """实际契约：PUT /relations 对不存在的记录不 404（仅取显示名失败容忍）。"""
    pa, pb = ab_pages
    b = _rec(admin, pb['collection'], 'B己')
    put = live.api('PUT', f"/relations/{pa['collection']}/no-such-record/rel", admin,
                   {'targetCollection': pb['collection'], 'targetField': 'rel',
                    'ids': [b['id']]})
    assert put.status_code < 300, f'实际契约不 404，得 {put.status_code}: {put.text[:200]}'
```

注意（执行者核对）：`ab_pages` fixture 里先建 A（占位目标）再建 B 再 PUT 修正 A 的 fields——若 `make_page` 支持先建 B 再建 A（B 的 relationConfig 目标是 A），可简化为无占位两步；按简化实现并在报告注明。PUT /pageConfigs 全量覆盖 fields——确认 PUT 语义（增量 or 全量）后选择修正方式，若 PUT fields 是合并语义则改用整包提交。

- [ ] **Step 2: 跑绿**

```bash
cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_data_full_relations.py -v
```

Expected: 10 passed。

- [ ] **Step 3: Commit**

```bash
git add server/tests/test_data_full_relations.py
git commit -m "test(data-full): 族C L2 —— relation 全量替换/反向/清理 + reference RESTRICT + quoteSelect 悬挂 + 关系图 10 例"
```

---

### Task 4: 族B L3 字段类型 UI 旅程

**Files:**
- Create: `e2e/data-full/data-field-types.spec.ts`（TD-B15–B20）

**Interfaces:**
- Consumes: `e2e/data-full/helpers.ts`（createDataPage/deleteDataPage/gotoWithAuth/api/screenshot/createRecord——签名同计划①）。
- Produces: 族B L3 基线 6 例。

- [ ] **Step 1: 写 data-field-types.spec.ts（TD-B15–B20）**

用例要点（选择器锚点必须先读组件核实，禁止凭本计划臆断——`src/components/dynamic-form/controls/` 下 AutoSequence.vue / DatePicker.vue / MarkdownEditor 相关 / FileUpload.vue、`src/components/common/MarkdownPreview.vue`、`src/components/dynamic-form/FormRenderer.vue`）：

- **TD-B15 autoTimestamp UI 填充/刷新**：页面字段 name+ts(autoTimestamp)。UI 新增填 name 提交 → API 读记录断言 `ts` 存在且匹配 ISO 格式 `/^\d{4}-\d{2}-\d{2}T/`（前端 store 填充）；记下 ts 值，sleep 确保时钟前进（≥1100ms）后 UI 编辑 name 再提交 → API 断言 ts 变化（刷新语义）。
- **TD-B16 compositeText UI 计算**：页面字段 name+qty(number)+comp(compositeText sourceFields ['name','qty'] separator ' - ')。UI 新增 name='组合甲' qty=3 → 表格行/详情断言显示 `组合甲 - 3`；UI 编辑 name='组合乙' → 断言重算为 `组合乙 - 3`。
- **TD-B17 markdown 编辑与渲染**：页面字段 name+md(markdown)。UI 新增 md 值 `# 标题甲\n\n**粗体**文本`（MdEditor 输入区填 textarea）→ 保存后表格单元格为纯文本摘要（不含 `#`/`**`）；打开详情对话框断言渲染出的 `h1` 含 `标题甲`、`strong` 含 `粗体`（MdPreview）。
- **TD-B18 文件上传 UI**：页面字段 name+attach(file)。`setInputFiles` 定位 FileUpload 的 `input[type=file]`（先读 FileUpload.vue 确认选择器与是否需要触发点击）上传小 txt（如 `DTEST 文件内容`）→ 等待上传完成 → 提交记录 → API 断言字段值为数组且 `[0].name` 正确；再经 UI 替换为第二个文件 → API 断言数组更新（新 name）。
- **TD-B19 autoSequence UI 只读与生成**：页面字段 name+sn(autoSequence prefix 'DTSU-' max 999)。表单中断言 sn 控件为只读（span 或 disabled，含「保存后生成」提示文案）→ 提交后表格行显示 `DTSU-001`；再建第二条 → `DTSU-002`。
- **TD-B20 字段级 workflow UI 流转**：页面字段 name+status(select todo/doing/done + workflowConfig transitions todo→doing「开始」/doing→done「完成」，roles: [])。UI 新增 status=todo 记录 → 打开行「查看」对话框 → footer 出现「开始」按钮（WorkflowActions 只在查看弹窗 footer）→ 点击 → 意见对话框确认 → 对话框关闭后 API 断言 status='doing'；再打开查看确认「完成」按钮出现。注意：工作流按钮断言加宽容超时（弹窗渲染）。

每个用例 beforeAll 建页（viewConfig 不需要）、afterAll `deleteDataPage`；文件上传用 Playwright `page.setInputFiles` 与 buffer（`{ name, mimeType, buffer }`）避免磁盘临时文件；所有 API 断言走 `api()` 助手。

- [ ] **Step 2: 跑绿**

```bash
npx playwright test e2e/data-full/data-field-types.spec.ts
```

Expected: 6 passed，连续两遍稳定；截图 `e2e/screenshots/data-full/` 至少 ft-md-render / ft-file-uploaded / ft-wf-transitioned 三张。已知风险：MdEditor 的 textarea 可能藏在 CodeMirror 类结构里（以组件实读为准）；el-select-v2/状态选择不涉及本 spec。

- [ ] **Step 3: Commit**

```bash
git add e2e/data-full/data-field-types.spec.ts
git commit -m "test(data-full): 族B L3 —— 自动时间戳/组合文本/markdown/文件上传/自增序号/workflow UI 6 例"
```

---

### Task 5: 族C L3 关联 UI 旅程

**Files:**
- Create: `e2e/data-full/data-relations.spec.ts`（TD-C11–C14）

**Interfaces:**
- Consumes: helpers 同 Task 4。
- Produces: 族C L3 基线 4 例。

- [ ] **Step 1: 写 data-relations.spec.ts（TD-C11–C14）**

用例要点（先读 `src/components/dynamic-form/controls/RelationSelect.vue` / `ReferenceSelect.vue` / `QuoteSelect.vue` 与 RelationGraphDialog 核实交互与选择器；三者均为 el-select-v2 filterable remote）：

- **TD-C11 relation 选择器 UI**：建 A/B 两页（A.relation→B，remote 搜索）。UI 新增 A 记录：关联字段点开 → 输入 B 记录名 → 下拉点选（`.el-select-dropdown__item` 挂 body）→ 提交 → API `GET /relations/A/<id>` 断言含该 B id；重开编辑对话框断言已选中项回显。
- **TD-C12 reference 选择器与继承显示**：建父/子两页（子.reference→父，inheritFields ['name']）。UI 新增子记录选父 → 提交 → API 断言 ref=父 id；打开子记录详情对话框断言父 name 以继承显示（`_ref_` 解析——详情中显示父名而非裸 id；先读 FormRenderer/详情渲染核实显示形态再断言）。
- **TD-C13 quoteSelect 多选 UI**：建 Q/A 两页。UI 新增 A 记录在引用字段多选两个 Q 记录 → 提交 → API 断言 quote 数组含两 id；重开编辑断言两项回显。
- **TD-C14 关系图谱对话框**：给一条 A 记录建 relation（API 造数即可）→ 打开该记录查看对话框 → 点「关系图谱」按钮 → 断言图谱对话框出现且渲染容器可见（RelationGraphDialog 的 canvas/svg，选择器以组件实读为准）→ 关闭。

beforeAll/afterAll 建页清理同 Task 4；跨页 UI 断言用 API 助手核对落库。

- [ ] **Step 2: 跑绿**

```bash
npx playwright test e2e/data-full/data-relations.spec.ts
```

Expected: 4 passed，连续两遍稳定；截图至少 rel-picker-selected / rel-graph-dialog 两张。

- [ ] **Step 3: Commit**

```bash
git add e2e/data-full/data-relations.spec.ts
git commit -m "test(data-full): 族C L3 —— 关联/引用/引用选择选择器与关系图谱 4 例"
```

---

### Task 6: 全量跑绿 + 文档收口

**Files:**
- Modify: `docs/data-testing/02-数据管理功能测试用例.md`（追加族B、族C 用例表）
- Modify: `docs/data-testing/01-测试方案.md`（环境事实补 #8–#11：autoTimestamp/compositeText 纯前端、workflow 两套与首次写入绕过、reference RESTRICT、relation 全量替换不 404）

**Interfaces:**
- Produces: 族B/C 基线落表；全量口径 L2 18+24=42、L3 7+10=17。

- [ ] **Step 1: 全量跑**

```bash
cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_data_full_crud.py tests/test_data_full_field_types.py tests/test_data_full_workflow.py tests/test_data_full_relations.py -q
npx playwright test e2e/data-full
```

Expected: L2 42 passed；L3 17 passed（连续两遍）。DTEST 残留清扫（五表 SQL，同计划① Task 5 方法）零残留。

- [ ] **Step 2: 02 文档追加族B/族C表（编号/用例/层/断言要点/执行结果），01 补环境事实**

族B 表 20 行（TD-B01–B20）、族C 表 14 行（TD-C01–C14），执行结果按实测。发现与 Global Constraints 不符的回改代码以实测为准并同步 01。

- [ ] **Step 3: Commit**

```bash
git add docs/data-testing/
git commit -m "docs(data-testing): 族B/C 用例落表——L2 42 例 + L3 17 例全量绿"
```

自查：`git status` 干净；全量绿；截图在 `e2e/screenshots/data-full/`。

---

## 计划③–⑤ 接续说明（不在本计划内实施）

- **计划③ 族D/E**：分支（diff/merge/lock/switch/restore）+ 跨项目依赖全链路；导入（xlsx 造数/预览/retry-result）、ETL（dry_run/cancel/幂等）、导出（test/debug/execute 内容断言/batchExport）、菜单导出回环。
- **计划④ 族F/G/H**：触发规则/校验脚本/Webhook（本地 stub）/行动作；列视图+查询台；评论/时间线/备份。
- **计划⑤ 收口**：02 全表定稿、首轮全量报告（03）、user-guide 核对、（04 已建档随缺陷滚动登记）。
