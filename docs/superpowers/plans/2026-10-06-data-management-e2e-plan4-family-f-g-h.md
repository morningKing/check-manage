# 数据管理 E2E 测试体系 · 计划④：族F 自动化引擎 + 族G 视图查询 + 族H 支撑 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 覆盖族F（触发规则/校验脚本/Webhook/行动作）、族G（列视图/查询台）、族H（评论/时间线/备份），L2 27 例 + L3 3 例全绿，02/01/04 文档同步。

**Architecture:** 延续计划①②③模式。L2 三个新文件：`test_data_full_automation.py`（族F，含进程内 webhook stub）、`test_data_full_views_query.py`（族G）、`test_data_full_support.py`（族H）；L3 一个新 spec：`data-automation.spec.ts`（行动作按钮 + 列视图切换 + 评论时间线）。Webhook 用 pytest 进程内 `http.server` stub（127.0.0.1 高位端口，timeout=2、retries=0）。

**Tech Stack:** pytest + requests（L2）、Playwright + TS（L3）、Flask :3002 + Vite :5173。

**Spec:** [docs/superpowers/specs/2026-10-06-data-management-e2e-design.md](../specs/2026-10-06-data-management-e2e-design.md) §5 族F/G/H；前置计划①②③（基线 L2 73 + L3 22 全绿）。

## Global Constraints

- 测试资产 `DTEST-<族>-<用途>-<时间戳>`；每测试自建页/规则/脚本并 finally 清理；禁止触碰非 DTEST 数据。
- **红线（研究代理核实，违反即事故）**：绝不调用 `POST /backups/restore`、`/backups/factory-reset`（confirmText='RESET' 清空全部业务数据）、`menuExport/batchClear` 之外的批量清空；备份用例只建小数据集且测完 DELETE 备份（文件堆 server/backups/）。
- L2：`cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_data_full_<file>.py -v`；服务不在 skip（marker `data_full`）。
- L3：`npx playwright test e2e/data-full`；选择器以组件实读为准。
- 后端改代码必须手动重启 Flask；本计划预期零产品代码改动。
- **族F/G/H 已核实 API 事实**（2026-10-06 研究代理核实，file:line 备案；写测试勿凭记忆改写）：
  1. **触发规则**：`POST /triggerRules` `{name, description?, enabled=true, sourceCollection, triggerEvent='update', triggerCondition{}, targetCollection, actionType='create'|'update'|'runScript', actionConfig{}, executionOrder}` → 201 回显+id；perm admin.trigger_rules。**只在 create/update 后触发（无 delete 调用点）**；actionType：create=按 fieldMapping 插入目标（`$source.field|$source.id|$operator|$NOW|字面量`）、update=matchField/matchValue/updateFields、runScript=scriptId。同步但跑在主事务提交后的**独立连接**，失败只写 trigger_logs(error)+通知，**主请求不受影响**（dynamic.py except:pass）。条件不匹配=静默跳过**不写日志**；disabled 被过滤。logs：`GET /triggerRules/<id>/logs?limit=` → `[{id,ruleId,ruleName,sourceRecordId,targetRecordId,status,errorMessage,createdAt}]`。
  2. **校验脚本**：`POST /validationScripts` `{name, description?, script}` → 201；test 端点 `{record, action='create', oldData?, fields, collection?}` → `{success, errors, warnings, pendingRelations}`，异常 → 400 `{success:false, errors:[str]}`。脚本沙箱 locals：`record/action/old_data/fields/collection/add_error/add_warning/query/query_one/find_by/get_relations/set_relations`（60s 超时）。**绑定只能经 PUT /pageConfigs 的 `validationScript` 键**（POST /pageConfigs 不落该列）；create/update 时 errors → 400 `{"error":"校验失败","validationErrors":[...],"validationWarnings":[...]}`（warnings 非阻塞）；脚本异常 → 400 `校验脚本执行错误：...`；删除脚本会 NULL 掉绑定。
  3. **Webhook**：`POST /webhook/rules` `{name, enabled=true, sourceCollections[], triggerEvent create|update|delete|merge|manual, triggerTiming 'before'|'after'='after', triggerCondition{}, webhookUrl, secret?, timeout=30, retries=3, executionOrder, rollbackOnFailure}` → 201；perm admin.webhooks。匹配=事件+timing+(sourceCollections 空=全部)。**before/after 都在请求线程内同步执行（含重试 sleep(1)）**——stub 必须 127.0.0.1 + timeout=2 + retries=0；before 失败 → 400 `{"error":"Before webhook blocked the operation","webhookErrors":[...]}`（delete 无 before）；签名头 `X-Webhook-Timestamp`(unix s) + `X-Webhook-Signature`=HMAC-SHA256-hex(`"{timestamp}.{payload_json}"`, key=secret) + `X-Webhook-Event`；成功=2xx；attempts=retries+1；`rollbackOnFailure` 是死配置（dynamic.py 不消费，不得断言回滚）。`manual` 事件永不自动触发。test 端点 `{customPayload?}` → `{success, logId, responseStatus, errorMessage, retryCount}`（真发 HTTP）。logs：`GET /webhook/rules/<id>/logs?success=&limit≤200` → `{logs:[{id,webhookUrl,eventType,requestPayload,responseStatus,responseBody,retryCount,success,createdAt}]}`；`GET /webhook/logs` → `{logs, total}`。
  4. **行动作**：pageConfigs.rowActions[] 项 `{id, label, actionType:'webhook'|'aiTask', webhookRuleId(须存在且 triggerEvent='manual'), statusField+runningValue+doneValue+failedValue(四者必须齐), visibleWhen{field,...}?, responseMapping[{jsonKey,column,required}]?, paramFields[]?, roles[], enabled}`；标量字段校验（paramFields 禁 relation/reference/quoteSelect/autoSequence）。run：`POST /<c>/<rid>/row-actions/<aid>/run` `{params?}` → `{ok:true, status:'running'|'submitted', statusField, runningValue}`；**webhook 分支异步**（共享线程池+稍后 write_back 版本+1）；错误映射：未知动作 404 `行操作不存在`、记录 404 `记录不存在`、disabled 400 `该行操作已停用`、角色 403 `权限不足`、条件 409、busy 409（stale 解锁阈值 max(300s, timeout*(retries+1))，测试不得依赖快速重试）；effect 按用户当前分支。**Open API**：`POST /api/v1/collections/<c>/<r>/row-actions/<a>/run` X-API-Key（绑页 key），响应同构。
  5. **列视图**（前缀 `/column-views`）：GET `/<page_id>/views` → `{views:[{id(int),name,isPublic,isDefault,columns,sortConfig,filterConfig,groupConfig}], defaultViewId}`（public+自己 private）；POST `{name!非空, isPublic, columns[], sortConfig?[], filterConfig?[], groupConfig?}` → 201；同名 400 `同名视图已存在`（public 按 page、private 按 page+creator）；非 admin 建 public → 403；PUT creator-or-admin；**DELETE 默认视图 → 400 `不能删除默认视图`**；`PUT /<pid>/views/<vid>/default` 仅 admin+public 且清除旧默认；copy → `<name> - 副本`(2…)。前端 localStorage `view:<pageId>` 覆盖默认；`getTableColumns(currentView)` 过滤表格列，工具栏切换器显示视图名。
  6. **查询台**：`GET /query/collections` → `[{collection,name,fields:[{fieldName,label,controlType,targetCollection?,options?}]}]`；`POST /query/execute` `{collection!, query(mongo 风格，label 自动重映射), lookup?, select?, sort?, skip?, limit(1..2000 默认200)}` → `{data:[{...记录,createdAt,updatedAt}], total, skip, limit, columns}`；无聚合管道；缺 collection → 400 `请指定集合`；未知 → 404 `集合不存在: x`；语法错 → 400 `查询语法错误: …`；仅 login_required（无页面级权限）。
  7. **评论**：`POST /comments/<c>/<rid>` `{content!(空→400 评论内容不能为空), mentions[]?}` → 201 全行；write_required；**不校验记录存在**（可造孤儿，须按 id 删）；GET → 行数组 ASC；PUT/DELETE author-or-admin 否则 403 `无权编辑此评论`/`无权删除此评论`。
  8. **时间线**：`GET /timeline/<c>/<rid>` → 按 timestamp 合并排序：`{type:'comment',...}` + `{type:'statusChange'|'change',action,content,fieldChanges[],branchName,...}`；**op-log 按用户当前分支过滤（comment 不过滤）**；写入者=dynamic.py 的 log_operation（create/update/delete）。
  9. **备份**（全 perm admin.backup）：`POST /backups` `{note?}` → 201 元数据（**同步全库 ZIP 导出**，数据集小才可测）；GET 列表；`GET /<id>/download` → binary zip；`DELETE /<id>` 删行+文件；settings GET/PUT `{enabled,interval,retentionCount}`；**restore/factory-reset 红线绝不调用**。
  10. **L3 入口**：行动作=DataTable 行下拉 extra-actions → RowActionRunner 对话框（参数表单）；评论/时间线=查看弹窗「评论 / 变更历史」区 RecordTimeline（发/编/删+合并时间线）；列视图切换器=DynamicPage 工具栏（显示 currentView.name）；查询台/备份=admin settings hub `/admin/query`、`/admin/backup`。spec 裁定：族F 管理页 L2 足够，L3 只做行动作按钮流。
  11. **残留**：trigger_logs/webhook_logs/trigger_rules/webhook_rules/column_views/comments/operation_logs 无级联清理——规则/视图/评论显式删，日志行容忍（sweep 备案）。
- 既有事实沿用：三级菜单链；RESERVED 集合避开（collection 名不用 query/comments/timeline/webhook 等）。

---

### Task 1: 族F-1 触发规则 + 校验脚本 L2（TD-F01–F08）

**Files:**
- Create: `server/tests/test_data_full_automation.py`（TD-F01–F08；后续 Task 2/3 续加）

**Interfaces:**
- Produces: 触发+校验基线 8 例；`_src_dst(admin)` 双页拓扑助手。

- [ ] **Step 1: 写触发规则用例（TD-F01–F05）**

```python
"""族F 自动化引擎 —— L2 live-server API 层。

触发规则+校验脚本（TD-F01–F08）/ Webhook（TD-F09–F12）/ 行动作（TD-F13–F15）。
契约锚点（计划④ Global Constraints #1–#4）：触发只在 create/update 后、独立连接、
失败不影响主请求、条件不匹配不写日志、无 delete 触发；校验脚本绑定只能 PUT
pageConfigs.validationScript、errors 400 校验失败/warnings 非阻塞；webhook 请求
线程内同步（stub 本地+timeout2+retries0）、before 失败 400、签名 HMAC-SHA256、
manual 永不自动；行动作 run 立即返回后异步回写、四值配置必须齐、404/400/403 错误映射。
"""
import hashlib
import hmac
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import data_full_live as live

pytestmark = pytest.mark.data_full

NAME = {'id': 'f1', 'label': '名称', 'fieldName': 'name',
        'controlType': 'text', 'required': True, 'order': 1}


@pytest.fixture(scope='module')
def admin():
    if not live.server_up():
        pytest.skip('dev 栈未启动（Vite :5173 代理 /api → Flask :3002）')
    return live.login()


def _rec(admin, collection, name):
    r = live.api('POST', f'/{collection}', admin, {'id': uuid.uuid4().hex, 'name': name})
    assert r.status_code == 201
    return r.json()


def _upd(admin, collection, rid, version, **fields):
    r = live.api('PUT', f'/{collection}/{rid}', admin, {'_version': version, **fields})
    assert r.status_code < 300, f'{r.status_code} {r.text[:200]}'
    return r.json()


def _wait(pred, timeout_s=15, interval=0.5, label=''):
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        last = pred()
        if last:
            return last
        time.sleep(interval)
    raise AssertionError(f'等待超时: {label}; last={last}')


# ---- 触发规则 TD-F01–F05 ----

def _trigger(admin, src, dst, event='update', condition=None, action='create',
             enabled=True, mapping=None):
    body = {'name': f"DTEST-F-trg-{uuid.uuid4().hex[:8]}",
            'sourceCollection': src, 'triggerEvent': event,
            'targetCollection': dst, 'actionType': action,
            'enabled': enabled,
            'actionConfig': mapping if mapping is not None else {
                'fieldMapping': {'name': '$source.name', 'copyOf': '$source.id'}}}
    if condition is not None:
        body['triggerCondition'] = condition
    r = live.api('POST', '/triggerRules', admin, body)
    assert r.status_code == 201, f'{r.status_code} {r.text[:300]}'
    return r.json()


def test_td_f01_trigger_crud_and_disabled(admin):
    src = live.make_page(admin, 'F', 'trg-src', fields=[NAME])
    dst = live.make_page(admin, 'F', 'trg-dst', fields=[NAME])
    try:
        rule = _trigger(admin, src['collection'], dst['collection'], enabled=False)
        got = live.api('GET', f"/triggerRules/{rule['id']}", admin)
        assert got.status_code == 200 and got.json()['enabled'] is False
        put = live.api('PUT', f"/triggerRules/{rule['id']}", admin, {'enabled': True})
        assert put.status_code < 300
        unknown = live.api('GET', '/triggerRules/DTEST-nope', admin)
        assert unknown.status_code == 404
    finally:
        live.api('DELETE', f"/triggerRules/{rule['id']}", admin)
        live.drop_page(admin, src)
        live.drop_page(admin, dst)


def test_td_f02_trigger_update_action_creates_target(admin):
    src = live.make_page(admin, 'F', 'trg2-src', fields=[NAME])
    dst = live.make_page(admin, 'F', 'trg2-dst', fields=[NAME])
    try:
        _trigger(admin, src['collection'], dst['collection'], event='update',
                 condition={'field': 'name', 'type': 'fieldChange', 'value': ''},
                 action='update',
                 mapping={'matchField': 'name', 'matchValue': '$source.name',
                          'updateFields': {'name': '$source.name'}})
        _rec(admin, src['collection'], '触发甲')  # create 不触发（event=update）
        assert live.api('GET', f'/{dst["collection"]}?all=true', admin).json()['total'] == 0
        rec = _rec(admin, src['collection'], '触发乙')
        # 真实字段变更触发 update 事件
        _upd(admin, src['collection'], rec['id'], rec['_version'], name='触发乙改')
        target = _wait(lambda: [x for x in live.api(
            'GET', f'/{dst["collection"]}?all=true', admin).json()['data']
            if x.get('name') == '触发乙改'], label='目标记录')
        assert target, 'update 触发应写入目标'
    finally:
        # 触发规则无级联：先删规则再删页
        rules = live.api('GET', '/triggerRules', admin).json()
        for r in rules:
            if r['sourceCollection'] == src['collection']:
                live.api('DELETE', f"/triggerRules/{r['id']}", admin)
        live.drop_page(admin, src)
        live.drop_page(admin, dst)


def test_td_f03_trigger_condition_mismatch_no_log_and_failure_tolerated(admin):
    src = live.make_page(admin, 'F', 'trg3-src', fields=[NAME])
    dst = live.make_page(admin, 'F', 'trg3-dst', fields=[NAME])
    try:
        rule = _trigger(admin, src['collection'], dst['collection'], event='create',
                        condition={'field': 'name', 'type': 'equals', 'value': '命中词'})
        # ① 不命中：静默跳过，不写日志、不建目标
        _rec(admin, src['collection'], '不命中')
        assert live.api('GET', f'/{dst["collection"]}?all=true', admin).json()['total'] == 0
        logs = live.api('GET', f"/triggerRules/{rule['id']}/logs", admin).json()
        assert logs == []
        # ② 命中：create 动作建目标
        _rec(admin, src['collection'], '命中词')
        _wait(lambda: live.api('GET', f'/{dst["collection"]}?all=true',
                               admin).json()['total'] == 1, label='命中触发')
        logs = live.api('GET', f"/triggerRules/{rule['id']}/logs", admin).json()
        assert len(logs) == 1 and logs[0]['status'] != 'error'
    finally:
        live.api('DELETE', f"/triggerRules/{rule['id']}", admin)
        live.drop_page(admin, src)
        live.drop_page(admin, dst)


def test_td_f04_trigger_failure_does_not_fail_main_request(admin):
    src = live.make_page(admin, 'F', 'trg4-src', fields=[NAME])
    # 目标页先建后删——触发引擎写已删目标 → error 日志，主请求仍 201
    dst = live.make_page(admin, 'F', 'trg4-dst', fields=[NAME])
    _trigger(admin, src['collection'], dst['collection'], event='create',
             condition={'field': 'name', 'type': 'equals', 'value': '引爆'})
    live.drop_page(admin, dst)
    try:
        r = live.api('POST', f'/{src["collection"]}', admin,
                     {'id': uuid.uuid4().hex, 'name': '引爆'})
        assert r.status_code == 201, f'触发失败不应影响主请求，得 {r.status_code}'
        rules = live.api('GET', '/triggerRules', admin).json()
        rule_id = next(x['id'] for x in rules if x['sourceCollection'] == src['collection'])
        logs = _wait(lambda: live.api('GET', f'/triggerRules/{rule_id}/logs', admin).json()
                     or None, timeout_s=15, label='日志出现')
        assert any(x.get('status') == 'error' for x in logs), f'应记录 error: {logs}'
    finally:
        rules = live.api('GET', '/triggerRules', admin).json()
        for r in rules:
            if r['sourceCollection'] == src['collection']:
                live.api('DELETE', f"/triggerRules/{r['id']}", admin)
        live.drop_page(admin, src)


def test_td_f05_trigger_delete_event_never_fires(admin):
    """实际契约：无 delete 触发调用点——delete 事件规则永不触发。"""
    src = live.make_page(admin, 'F', 'trg5-src', fields=[NAME])
    dst = live.make_page(admin, 'F', 'trg5-dst', fields=[NAME])
    try:
        rule = _trigger(admin, src['collection'], dst['collection'], event='delete')
        rec = _rec(admin, src['collection'], '待删')
        assert live.api('DELETE', f"/{src['collection']}/{rec['id']}",
                        admin).status_code < 300
        time.sleep(2)
        assert live.api('GET', f'/{dst["collection"]}?all=true',
                        admin).json()['total'] == 0
        logs = live.api('GET', f"/triggerRules/{rule['id']}/logs", admin).json()
        assert logs == []
    finally:
        live.api('DELETE', f"/triggerRules/{rule['id']}", admin)
        live.drop_page(admin, src)
        live.drop_page(admin, dst)
```

注意（执行者核对）：condition 的 `type` 字段取值（equals/fieldChange）以 `server/utils/trigger_engine.py` 实读为准（`{field,type,value}` 或扁平结构——先读引擎的条件判断代码再定稿 payload，报告中注明实际形状）。

- [ ] **Step 2: 写校验脚本用例（TD-F06–F08）**

```python
# ---- 校验脚本 TD-F06–F08 ----

VAL_OK = ("errors = errors or []\n"
          "warnings = warnings or []\n"
          "if not (record.get('name') or '').strip():\n"
          "    add_error('名称不能为空')\n"
          "if (record.get('qty') or 0) > 100:\n"
          "    add_warning('数量偏大')\n")

VAL_CRASH = "1 / 0\n"


def _val_script(admin, script):
    r = live.api('POST', '/validationScripts', admin,
                 {'name': f"DTEST-F-val-{uuid.uuid4().hex[:8]}",
                  'description': '数据管理 e2e', 'script': script})
    assert r.status_code == 201, f'{r.status_code} {r.text[:300]}'
    return r.json()


def test_td_f06_validation_crud_and_test_endpoint(admin):
    s = _val_script(admin, VAL_OK)
    try:
        t = live.api('POST', f"/validationScripts/{s['id']}/test", admin,
                     {'record': {'name': ''}, 'action': 'create',
                      'fields': [NAME], 'collection': 'test'})
        assert t.status_code < 300 and t.json()['success'] is True
        assert t.json()['errors'] == ['名称不能为空']
        t2 = live.api('POST', f"/validationScripts/{s['id']}/test", admin,
                      {'record': {'name': '合法', 'qty': 999}, 'action': 'create',
                       'fields': [NAME], 'collection': 'test'})
        assert t2.json()['errors'] == [] and t2.json()['warnings'] == ['数量偏大']
    finally:
        live.api('DELETE', f"/validationScripts/{s['id']}", admin)


def test_td_f07_validation_binding_blocks_create_and_update(admin):
    page = live.make_page(admin, 'F', 'val-bind', fields=[
        NAME, {'id': 'f2', 'label': '数量', 'fieldName': 'qty',
               'controlType': 'number', 'required': False, 'order': 2}])
    s = _val_script(admin, VAL_OK)
    try:
        bind = live.api('PUT', f"/pageConfigs/{page['page_id']}", admin,
                        {'validationScript': s['id']})
        assert bind.status_code < 300, f'{bind.status_code} {bind.text[:300]}'
        bad = live.api('POST', f"/{page['collection']}", admin,
                       {'id': uuid.uuid4().hex, 'name': ''})
        assert bad.status_code == 400
        assert bad.json()['error'] == '校验失败'
        assert '名称不能为空' in bad.json()['validationErrors']
        rec = _rec(admin, page['collection'], '合法行')
        bad_upd = live.api('PUT', f"/{page['collection']}/{rec['id']}", admin,
                           {'name': '', '_version': rec['_version']})
        assert bad_upd.status_code == 400  # update 同样被拦
        crash = _val_script(admin, VAL_CRASH)
        bind2 = live.api('PUT', f"/pageConfigs/{page['page_id']}", admin,
                         {'validationScript': crash['id']})
        assert bind2.status_code < 300
        boom = live.api('POST', f"/{page['collection']}", admin,
                        {'id': uuid.uuid4().hex, 'name': '炸'})
        assert boom.status_code == 400 and '校验脚本执行错误' in boom.json()['error']
        live.api('DELETE', f"/validationScripts/{crash['id']}", admin)
    finally:
        live.api('DELETE', f"/validationScripts/{s['id']}", admin)  # 删除会 NULL 绑定
        live.drop_page(admin, page)


def test_td_f08_validation_warnings_nonblocking(admin):
    page = live.make_page(admin, 'F', 'val-warn', fields=[
        NAME, {'id': 'f2', 'label': '数量', 'fieldName': 'qty',
               'controlType': 'number', 'required': False, 'order': 2}])
    s = _val_script(admin, VAL_OK)
    try:
        live.api('PUT', f"/pageConfigs/{page['page_id']}", admin,
                 {'validationScript': s['id']})
        r = live.api('POST', f"/{page['collection']}", admin,
                     {'id': uuid.uuid4().hex, 'name': '警告但通过', 'qty': 500})
        assert r.status_code == 201  # warnings 非阻塞
    finally:
        live.api('DELETE', f"/validationScripts/{s['id']}", admin)
        live.drop_page(admin, page)
```

- [ ] **Step 3: 跑绿**

```bash
cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_data_full_automation.py -v
```

Expected: 8 passed（两遍稳定）。

- [ ] **Step 4: Commit**

```bash
git add server/tests/test_data_full_automation.py
git commit -m "test(data-full): 族F L2 触发+校验 —— CRUD/条件命中/失败容忍/无delete触发/绑定拦截/警告非阻塞 8 例"
```

---

### Task 2: 族F-2 Webhook L2（TD-F09–F12，进程内 stub）

**Files:**
- Modify: `server/tests/test_data_full_automation.py`（追加 TD-F09–F12 与 stub fixture）

**Interfaces:**
- Produces: webhook 基线 4 例；`stub_server` fixture（模块级，127.0.0.1 动态端口，记录请求 headers/body/路径，可编程响应码）。

- [ ] **Step 1: stub fixture + 用例（TD-F09–F12）**

```python
# ---- Webhook TD-F09–F12 ----

class _StubHandler(BaseHTTPRequestHandler):
    server_version = 'DTESTStub/1.0'

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length)
        rec = {'path': self.path,
               'headers': {k: v for k, v in self.headers.items()
                           if k.lower() in ('x-webhook-timestamp', 'x-webhook-signature',
                                            'x-webhook-event', 'content-type')},
               'body': body.decode('utf-8', 'replace')}
        self.server.requests.append(rec)
        code = self.server.respond_code
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'ok': code < 400}).encode('utf-8'))

    def log_message(self, *a):  # 静音访问日志
        pass


@pytest.fixture(scope='module')
def stub():
    server = HTTPServer(('127.0.0.1', 0), _StubHandler)
    server.requests = []
    server.respond_code = 200
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()


def _webhook_rule(admin, stub, page, timing='after', event='update', retries=0,
                  secret='dtest-secret'):
    body = {'name': f"DTEST-F-hook-{uuid.uuid4().hex[:8]}",
            'sourceCollections': [page['collection']],
            'triggerEvent': event, 'triggerTiming': timing,
            'webhookUrl': f"http://127.0.0.1:{stub.server_address[1]}/hook",
            'secret': secret, 'timeout': 2, 'retries': retries}
    r = live.api('POST', '/webhook/rules', admin, body)
    assert r.status_code == 201, f'{r.status_code} {r.text[:300]}'
    return r.json()


def test_td_f09_webhook_rule_crud_and_manual_never_fires(admin, stub):
    page = live.make_page(admin, 'F', 'hook-cfg', fields=[NAME])
    try:
        rule = _webhook_rule(admin, stub, page, event='manual')
        lst = live.api('GET', '/webhook/rules', admin)
        assert any(x['id'] == rule['id'] for x in lst.json())
        # manual 永不自动触发：真实 update 后 stub 零命中
        hits = len(stub.requests)
        rec = _rec(admin, page['collection'], 'manual对象')
        _upd(admin, page['collection'], rec['id'], rec['_version'], name='manual对象改')
        time.sleep(2)
        assert len(stub.requests) == hits
        dele = live.api('DELETE', f"/webhook/rules/{rule['id']}", admin)
        assert dele.status_code < 300
    finally:
        live.drop_page(admin, page)


def test_td_f10_webhook_before_failure_blocks_and_success_passes(admin, stub):
    page = live.make_page(admin, 'F', 'hook-before', fields=[NAME])
    rule = _webhook_rule(admin, stub, page, timing='before', event='create')
    try:
        stub.respond_code = 500
        blocked = live.api('POST', f"/{page['collection']}", admin,
                           {'id': uuid.uuid4().hex, 'name': '被拦截'})
        assert blocked.status_code == 400
        assert blocked.json()['error'] == 'Before webhook blocked the operation'
        assert blocked.json().get('webhookErrors')
        stub.respond_code = 200
        ok = live.api('POST', f"/{page['collection']}", admin,
                      {'id': uuid.uuid4().hex, 'name': '放行'})
        assert ok.status_code == 201
    finally:
        stub.respond_code = 200
        live.api('DELETE', f"/webhook/rules/{rule['id']}", admin)
        live.drop_page(admin, page)


def test_td_f11_webhook_after_payload_signature_and_logs(admin, stub):
    page = live.make_page(admin, 'F', 'hook-after', fields=[NAME])
    secret = 'dtest-secret'
    rule = _webhook_rule(admin, stub, page, timing='after', event='create', secret=secret)
    try:
        before_count = len(stub.requests)
        rec = _rec(admin, page['collection'], '签名行')
        hit = _wait(lambda: (stub.requests[before_count:] or [None])[0],
                    timeout_s=15, label='stub 收到请求')
        payload = json.loads(hit['body'])
        assert hit['headers'].get('x-webhook-event') == 'create'
        ts = hit['headers'].get('x-webhook-timestamp')
        sig = hit['headers'].get('x-webhook-signature')
        expected = hmac.new(secret.encode(), f'{ts}.{hit["body"]}'.encode(),
                            hashlib.sha256).hexdigest()
        assert sig == expected, f'签名不符: {sig} != {expected}'
        assert rec['id'] in json.dumps(payload) or payload, 'payload 应含事件数据'
        logs = _wait(lambda: live.api(
            'GET', f"/webhook/rules/{rule['id']}/logs", admin).json()['logs'] or None,
            timeout_s=10, label='webhook 日志')
        assert logs[0]['success'] is True and logs[0]['responseStatus'] == 200
        all_logs = live.api('GET', '/webhook/logs?limit=50', admin)
        assert all_logs.status_code == 200 and 'total' in all_logs.json()
    finally:
        live.api('DELETE', f"/webhook/rules/{rule['id']}", admin)
        live.drop_page(admin, page)


def test_td_f12_webhook_test_endpoint_and_after_failure_logged(admin, stub):
    page = live.make_page(admin, 'F', 'hook-test', fields=[NAME])
    rule = _webhook_rule(admin, stub, page, timing='after', event='update')
    try:
        t = live.api('POST', f"/webhook/rules/{rule['id']}/test", admin, {})
        assert t.status_code < 300 and t.json()['success'] is True
        assert t.json()['responseStatus'] == 200
        stub.respond_code = 500
        rec = _rec(admin, page['collection'], '失败行')
        _upd(admin, page['collection'], rec['id'], rec['_version'], name='失败行改')
        _wait(lambda: [x for x in live.api(
            'GET', f"/webhook/rules/{rule['id']}/logs", admin).json()['logs']
            if x['success'] is False], timeout_s=15, label='失败日志')
        fail_logs = [x for x in live.api(
            'GET', f"/webhook/rules/{rule['id']}/logs?success=false",
            admin).json()['logs']]
        assert fail_logs and fail_logs[0]['responseStatus'] == 500
        assert live.api('GET', f"/{page['collection']}/{rec['id']}",
                        admin).json()['name'] == '失败行改'  # after 失败不影响业务
    finally:
        stub.respond_code = 200
        live.api('DELETE', f"/webhook/rules/{rule['id']}", admin)
        live.drop_page(admin, page)
```

注意（执行者核对）：签名串接格式（`{ts}.{body}` 是否带尾换行）以 `server/utils/webhook_engine.py:331-336` 实读为准；stub 固定返回 {"ok": true}，respond_code 由用例自行设置并在 finally 复位 200。

- [ ] **Step 2: 跑绿**

```bash
cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_data_full_automation.py -v
```

Expected: 12 passed（两遍稳定）。

- [ ] **Step 3: Commit**

```bash
git add server/tests/test_data_full_automation.py
git commit -m "test(data-full): 族F L2 webhook —— before拦截/签名/日志/after失败容忍/test端点 4 例（进程内 stub）"
```

---

### Task 3: 族F-3 行动作 L2（TD-F13–F15）

**Files:**
- Modify: `server/tests/test_data_full_automation.py`（追加 TD-F13–F15）

**Interfaces:**
- Consumes: stub fixture（Task 2）；webhook 规则须 triggerEvent='manual' 先于 pageConfig 创建。
- Produces: 行动作 L2 基线 3 例。

- [ ] **Step 1: 用例（TD-F13–F15）**

```python
# ---- 行动作 TD-F13–F15 ----

def _action_page(admin, stub):
    """manual webhook 规则先行（pageConfigs.rowActions 校验其存在），页带四值状态字段。"""
    rule = live.api('POST', '/webhook/rules', admin, {
        'name': f"DTEST-F-ra-{uuid.uuid4().hex[:8]}",
        'sourceCollections': [], 'triggerEvent': 'manual',
        'triggerTiming': 'after',
        'webhookUrl': f"http://127.0.0.1:{stub.server_address[1]}/row-action",
        'secret': '', 'timeout': 2, 'retries': 0}).json()
    fields = [
        NAME,
        {'id': 'f2', 'label': '处理状态', 'fieldName': 'ra_status',
         'controlType': 'text', 'required': False, 'order': 2},
        {'id': 'f3', 'label': '回执', 'fieldName': 'ra_receipt',
         'controlType': 'text', 'required': False, 'order': 3},
    ]
    page = live.make_page(admin, 'F', 'ra', fields=fields)
    put = live.api('PUT', f"/pageConfigs/{page['page_id']}", admin, {
        'rowActions': [{'id': 'approve', 'label': '审批',
                        'actionType': 'webhook', 'webhookRuleId': rule['id'],
                        'statusField': 'ra_status', 'runningValue': 'running',
                        'doneValue': 'done', 'failedValue': 'failed',
                        'responseMapping': [{'jsonKey': 'ok', 'column': 'ra_receipt'}],
                        'roles': [], 'enabled': True}]})
    assert put.status_code < 300, f'rowActions 绑定失败 {put.status_code} {put.text[:300]}'
    return page, rule


def test_td_f13_row_action_run_async_writeback(admin, stub):
    page, rule = _action_page(admin, stub)
    try:
        rec = _rec(admin, page['collection'], '审批对象')
        r = live.api('POST',
                     f"/{page['collection']}/{rec['id']}/row-actions/approve/run",
                     admin, {})
        assert r.status_code < 300, f'{r.status_code} {r.text[:300]}'
        assert r.json()['ok'] is True and r.json()['status'] in ('running', 'submitted')
        assert r.json()['statusField'] == 'ra_status'
        done = _wait(lambda: (lambda g: g.json().get('ra_status') in ('done', 'failed')
                              and g.json() or None)(
            live.api('GET', f"/{page['collection']}/{rec['id']}", admin)),
            timeout_s=20, label='回写终态')
        assert done['ra_status'] == 'done', f"应回写 done，得 {done['ra_status']}"
        assert done.get('ra_receipt') == 'true'  # stub 返回 {"ok": true}
    finally:
        live.api('DELETE', f"/webhook/rules/{rule['id']}", admin)
        live.drop_page(admin, page)


def test_td_f14_row_action_unknown_404_and_disabled_400(admin, stub):
    page, rule = _action_page(admin, stub)
    try:
        rec = _rec(admin, page['collection'], '错误对象')
        unk = live.api('POST',
                       f"/{page['collection']}/{rec['id']}/row-actions/nope/run", admin, {})
        assert unk.status_code == 404  # 行操作不存在
        dis = live.api('PUT', f"/pageConfigs/{page['page_id']}", admin, {
            'rowActions': [{'id': 'approve', 'label': '审批',
                            'actionType': 'webhook', 'webhookRuleId': rule['id'],
                            'statusField': 'ra_status', 'runningValue': 'running',
                            'doneValue': 'done', 'failedValue': 'failed',
                            'roles': [], 'enabled': False}]})
        assert dis.status_code < 300
        off = live.api('POST',
                       f"/{page['collection']}/{rec['id']}/row-actions/approve/run",
                       admin, {})
        assert off.status_code == 400 and off.json()['error'] == '该行操作已停用'
    finally:
        live.api('DELETE', f"/webhook/rules/{rule['id']}", admin)
        live.drop_page(admin, page)


def test_td_f15_row_action_open_api_entry(admin, stub):
    """Open API 入口与内部同构：X-API-Key（绑页）调 run → 同样回写。"""
    from data_full_live import BASE
    key = _mk_api_key(admin)
    page, rule = _action_page(admin, stub)
    try:
        rec = _rec(admin, page['collection'], '开放对象')
        r = live.api('POST', '/apiKeys', admin,
                     {'name': f'DTEST-F-key-{uuid.uuid4().hex[:6]}',
                      'pageBindings': [{'pageId': page['page_id'],
                                        'actions': ['read', 'update']}]})
        if r.status_code >= 300:
            r = live.api('POST', '/apiKeys', admin,
                         {'name': f'DTEST-F-key-{uuid.uuid4().hex[:6]}'})
        key_val = r.json()['key']
        try:
            import requests as _rq
            resp = _rq.post(
                f'{BASE}/api/v1/collections/{page["collection"]}/{rec["id"]}'
                f'/row-actions/approve/run',
                headers={'X-API-Key': key_val}, json={}, timeout=15)
            assert resp.status_code < 300, f'{resp.status_code} {resp.text[:300]}'
            assert resp.json()['ok'] is True
            done = _wait(lambda: (lambda g: g.json().get('ra_status') in ('done', 'failed')
                                  and g.json() or None)(
                live.api('GET', f"/{page['collection']}/{rec['id']}", admin)),
                timeout_s=20, label='开放入口回写')
            assert done['ra_status'] == 'done'
        finally:
            live.api('DELETE', f"/apiKeys/{r.json()['id']}", admin)
    finally:
        live.api('DELETE', f"/webhook/rules/{rule['id']}", admin)
        live.drop_page(admin, page)
```

注意（执行者核对）：`_mk_api_key` 未定义——删除该行（后面已直接建 key）。API Key 的绑定形状（pageBindings/actions 或仅 name）以 `server/routes/api_keys.py` 实读为准：若创建必须绑页，用第一种；若无需绑页也能调（require_bound_key 语义核实），用第二种并在报告注明。responseMapping 的 jsonKey 取 stub 响应 `{"ok": true}` → ra_receipt='true' 字符串——以 row_action_engine 的映射实读为准（可能是布尔转字符串规则不同，按实测断言）。

- [ ] **Step 2: 跑绿 → Step 3: Commit**

```bash
cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_data_full_automation.py -v
# Expected: 15 passed（两遍稳定）
git add server/tests/test_data_full_automation.py
git commit -m "test(data-full): 族F L2 行动作 —— 异步回写/404与停用/Open API 同构 3 例"
```

---

### Task 4: 族G 列视图 + 查询台 L2（TD-G01–G07）

**Files:**
- Create: `server/tests/test_data_full_views_query.py`

**Interfaces:**
- Produces: 族G 基线 7 例。

- [ ] **Step 1: 写 test_data_full_views_query.py（TD-G01–G07）**

```python
"""族G 列视图 + 查询台 —— L2 live-server API 层（TD-G01–G07）。

契约锚点（计划④ Global Constraints #5/#6）：列视图同名 400/默认视图不可删/设默认
清旧且仅 admin+public/copy 副本命名；查询台缺集合 400/未知 404/语法错 400、
limit 1..2000、label 重映射。
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
        pytest.skip('dev 栈未启动')
    return live.login()


@pytest.fixture(scope='module')
def pageh(admin):
    page = live.make_page(admin, 'G', 'views', fields=[NAME, QTY])
    yield page
    # 列视图随页删（page_id 外键）——若实测不级联，按 id 显式删并报告
    live.drop_page(admin, page)


def test_td_g01_view_crud_and_dup_name(admin, pageh):
    r = live.api('POST', f"/column-views/{pageh['page_id']}/views", admin,
                 {'name': '精简视图', 'isPublic': False,
                  'columns': ['name']})
    assert r.status_code == 201, f'{r.status_code} {r.text[:300]}'
    vid = r.json()['id']
    dup = live.api('POST', f"/column-views/{pageh['page_id']}/views", admin,
                   {'name': '精简视图', 'isPublic': False, 'columns': ['name']})
    assert dup.status_code == 400  # 同名视图已存在
    lst = live.api('GET', f"/column-views/{pageh['page_id']}/views", admin)
    assert any(v['id'] == vid for v in lst.json()['views'])
    put = live.api('PUT', f"/column-views/{pageh['page_id']}/views/{vid}", admin,
                   {'columns': ['name', 'qty']})
    assert put.status_code < 300


def test_td_g02_default_view_single_and_undeletable(admin, pageh):
    v1 = live.api('POST', f"/column-views/{pageh['page_id']}/views", admin,
                  {'name': '默认甲', 'isPublic': True, 'columns': ['name']}).json()
    live.api('PUT', f"/column-views/{pageh['page_id']}/views/{v1['id']}/default", admin)
    v2 = live.api('POST', f"/column-views/{pageh['page_id']}/views", admin,
                  {'name': '默认乙', 'isPublic': True, 'columns': ['qty']}).json()
    live.api('PUT', f"/column-views/{pageh['page_id']}/views/{v2['id']}/default", admin)
    lst = live.api('GET', f"/column-views/{pageh['page_id']}/views", admin).json()
    assert lst['defaultViewId'] == v2['id']  # 旧默认被清除
    del_default = live.api('DELETE',
                           f"/column-views/{pageh['page_id']}/views/{v2['id']}", admin)
    assert del_default.status_code == 400  # 不能删除默认视图


def test_td_g03_view_copy_and_private_scope(admin, pageh):
    v = live.api('POST', f"/column-views/{pageh['page_id']}/views", admin,
                 {'name': '私甲', 'isPublic': False, 'columns': ['name']}).json()
    cp = live.api('POST',
                  f"/column-views/{pageh['page_id']}/views/{v['id']}/copy", admin)
    assert cp.status_code < 300
    assert '副本' in cp.json()['name']
    # 非 admin 用户看不到他人 private 视图
    role = live.api('POST', '/roles', admin,
                    {'name': f"DTEST-G-role-{uuid.uuid4().hex[:6]}",
                     'defaultPageAccess': 'read'})
    uid = None
    try:
        uname = f"dtest_g_user_{uuid.uuid4().hex[:6]}"
        u = live.api('POST', '/users', admin,
                     {'username': uname, 'password': 'Dtest#12345',
                      'displayName': 'G 视图探针', 'role': role.json()['id']})
        uid = u.json()['id']
        login = live.api('POST', '/auth/login', None,
                         {'username': uname, 'password': 'Dtest#12345'})
        other = {'Authorization': f"Bearer {login.json()['token']}"}
        others_list = live.api('GET',
                               f"/column-views/{pageh['page_id']}/views", other)
        names = [x['name'] for x in others_list.json()['views']]
        assert '私甲' not in names and cp.json()['name'] not in names
    finally:
        if uid:
            live.api('DELETE', f'/users/{uid}', admin)
        live.api('DELETE', f"/roles/{role.json()['id']}", admin)
        live.api('DELETE', f"/column-views/{pageh['page_id']}/views/{v['id']}", admin)
        live.api('DELETE',
                 f"/column-views/{pageh['page_id']}/views/{cp.json()['id']}", admin)


def test_td_g04_view_missing_page_404(admin):
    r = live.api('POST', '/column-views/page-DTEST-nope/views', admin,
                 {'name': '孤儿', 'isPublic': False, 'columns': []})
    assert r.status_code == 404  # 页面配置不存在


def test_td_g05_query_collections_listing(admin, pageh):
    r = live.api('GET', '/query/collections', admin)
    assert r.status_code == 200
    entry = next((c for c in r.json() if c['collection'] == pageh['collection']), None)
    assert entry, '查询台应列出 DTEST 页'
    assert any(f['fieldName'] == 'name' for f in entry['fields'])


def test_td_g06_query_execute_valid(admin, pageh):
    marker = f'查针{uuid.uuid4().hex[:6]}'
    live.api('POST', f"/{pageh['collection']}", admin,
             {'id': uuid.uuid4().hex, 'name': marker, 'qty': 66})
    r = live.api('POST', '/query/execute', admin,
                 {'collection': pageh['collection'],
                  'query': {'名称': marker}, 'limit': 10})
    assert r.status_code < 300, f'{r.status_code} {r.text[:300]}'
    body = r.json()
    assert body['total'] == 1 and body['data'][0]['name'] == marker
    assert body['limit'] == 10
    assert any(c['key'] == 'name' for c in body['columns'])


def test_td_g07_query_error_contract(admin, pageh):
    missing = live.api('POST', '/query/execute', admin, {'query': {}})
    assert missing.status_code == 400 and missing.json()['error'] == '请指定集合'
    unknown = live.api('POST', '/query/execute', admin,
                       {'collection': 'DTEST-ghost', 'query': {}})
    assert unknown.status_code == 404
    bad = live.api('POST', '/query/execute', admin,
                   {'collection': pageh['collection'], 'query': {'$or': 'not-a-list'}})
    assert bad.status_code == 400 and '查询语法错误' in bad.json()['error']
```

注意（执行者核对）：G06 用 label「名称」验证自动重映射；若 label 重映射不生效改用 fieldName 并记录实际行为（query.py translate 逻辑实读）。G07 的非法查询样例（`$or` 非列表）以 mongo_query.translate 的报错路径为准，选一个确定抛「查询语法错误」的样例。G02/G03 清理：非默认视图删除；默认视图 v2 留给页删级联（若不级联，先 PUT 别的为默认再删——按实测处理并报告）。

- [ ] **Step 2: 跑绿 → Step 3: Commit**

```bash
cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_data_full_views_query.py -v
# Expected: 7 passed（两遍稳定）
git add server/tests/test_data_full_views_query.py
git commit -m "test(data-full): 族G L2 —— 列视图 CRUD/默认/私有域/副本 + 查询台目录/执行/错误契约 7 例"
```

---

### Task 5: 族H 评论/时间线/备份 L2（TD-H01–H05）

**Files:**
- Create: `server/tests/test_data_full_support.py`

**Interfaces:**
- Produces: 族H 基线 5 例（备份只建/列/下/删，红线不碰 restore）。

- [ ] **Step 1: 写 test_data_full_support.py（TD-H01–H05）**

```python
"""族H 评论/时间线/备份 —— L2 live-server API 层（TD-H01–H05）。

契约锚点（计划④ Global Constraints #7–#9）：评论空内容 400/author-or-admin 403 文本/
不校验记录存在；时间线 comment+op-log 合并且 op-log 按当前分支过滤；备份全库同步
ZIP、红线绝不调 restore/factory-reset、测完 DELETE 备份。
"""
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


def _second_user(admin):
    role = live.api('POST', '/roles', admin,
                    {'name': f"DTEST-H-role-{uuid.uuid4().hex[:6]}",
                     'defaultPageAccess': 'write'})
    uname = f"dtest_h_user_{uuid.uuid4().hex[:6]}"
    live.api('POST', '/users', admin,
             {'username': uname, 'password': 'Dtest#12345',
              'displayName': 'H 探针', 'role': role.json()['id']})
    login = live.api('POST', '/auth/login', None,
                     {'username': uname, 'password': 'Dtest#12345'})
    return (role.json()['id'],
            {'Authorization': f"Bearer {login.json()['token']}"},
            login.json().get('user', {}).get('id') or uname)


def test_td_h01_comment_crud_and_empty_rejected(admin, pageh):
    rec = _rec(admin, pageh['collection'], '评论宿主')
    r = live.api('POST', f"/comments/{pageh['collection']}/{rec['id']}", admin,
                 {'content': 'DTEST 评论甲', 'mentions': []})
    assert r.status_code == 201, f'{r.status_code} {r.text[:300]}'
    cid = r.json()['id']
    lst = live.api('GET', f"/comments/{pageh['collection']}/{rec['id']}", admin)
    assert any(c['id'] == cid for c in lst.json())
    put = live.api('PUT', f"/comments/{cid}", admin, {'content': 'DTEST 评论改'})
    assert put.status_code < 300
    empty = live.api('POST', f"/comments/{pageh['collection']}/{rec['id']}", admin,
                     {'content': '   '})
    assert empty.status_code == 400  # 评论内容不能为空
    dele = live.api('DELETE', f"/comments/{cid}", admin)
    assert dele.status_code < 300


def test_td_h02_comment_permission_author_vs_admin(admin, pageh):
    rec = _rec(admin, pageh['collection'], '权限宿主')
    role_id, user_headers, _uid = _second_user(admin)
    try:
        r = live.api('POST', f"/comments/{pageh['collection']}/{rec['id']}",
                     user_headers, {'content': '他人评论'})
        assert r.status_code == 201
        cid = r.json()['id']
        edit = live.api('PUT', f"/comments/{cid}", user_headers,
                        {'content': '自改'})
        assert edit.status_code < 300  # 作者可改自己的
        admin_edit_denied = live.api('PUT', f"/comments/{cid}", admin,
                                     {'content': 'admin 强改'})
        # 实际契约待核实：author-or-admin——admin 应可改（若 403 则契约是仅作者）
        assert admin_edit_denied.status_code in (200, 403)
        admin_del = live.api('DELETE', f"/comments/{cid}", admin)
        assert admin_del.status_code < 300  # admin 可删
    finally:
        live.api('DELETE', f'/users/{uid}' if uid else '/users/x', admin) if uid else None
        live.api('DELETE', f"/roles/{role_id}", admin)


def test_td_h03_timeline_merges_comment_and_change(admin, pageh):
    rec = _rec(admin, pageh['collection'], '时间线宿主')
    live.api('PUT', f"/{pageh['collection']}/{rec['id']}", admin,
             {'name': '时间线宿主改', '_version': rec['_version']})
    c = live.api('POST', f"/comments/{pageh['collection']}/{rec['id']}", admin,
                 {'content': '时间线评论'}).json()
    t = live.api('GET', f"/timeline/{pageh['collection']}/{rec['id']}", admin)
    assert t.status_code == 200
    entries = t.json()
    types = {e['type'] for e in entries}
    assert 'comment' in types and ({'change', 'statusChange'} & types)
    assert any(e.get('content') == '时间线评论' for e in entries if e['type'] == 'comment')
    ts_list = [e['timestamp'] for e in entries]
    assert ts_list == sorted(ts_list)  # 按 timestamp 排序


def test_td_h04_backup_create_list_download_delete(admin):
    r = live.api('POST', '/backups', admin, {'note': 'DTEST-H 备份冒烟'})
    assert r.status_code == 201, f'{r.status_code} {r.text[:300]}'
    bid = r.json()['id']
    try:
        lst = live.api('GET', '/backups', admin)
        assert any(b['id'] == bid for b in (lst.json() if isinstance(lst.json(), list)
                                            else lst.json().get('backups', [])))
        dl = live.api('GET', f'/backups/{bid}/download', admin)
        assert dl.status_code == 200 and dl.content[:2] == b'PK'
    finally:
        d = live.api('DELETE', f'/backups/{bid}', admin)
        assert d.status_code < 300  # 删行+文件，不留 server/backups 堆积


def test_td_h05_backup_settings_roundtrip(admin):
    g = live.api('GET', '/backups/settings', admin) if True else None
    g = live.api('GET', '/backup/settings', admin)
    if g.status_code == 404:
        g = live.api('GET', '/backups/settings', admin)
    assert g.status_code == 200, f'{g.status_code} {g.text[:200]}'
    body = g.json()
    payload = {'enabled': body.get('enabled', False),
               'interval': body.get('interval', 'daily'),
               'retentionCount': body.get('retentionCount', 3)}
    p = live.api('PUT', '/backup/settings', admin, payload) if g.status_code == 200 else None
    if p is None or p.status_code == 404:
        p = live.api('PUT', '/backups/settings', admin, payload)
    assert p.status_code < 300
```

注意（执行者核对）：H05 的备份 settings 路由前缀（/backup/settings vs /backups/settings）与响应形状以 `server/routes/backups.py:170-193` 实读为准后定稿（删掉探测式写法，直接写正确路径）；H02 中 admin 改他人评论的契约（author-or-admin）以 `server/routes/comments.py:83-103` 实读定夺，断言收紧为确定值；H02 finally 的 user 删除行写成清晰条件语句。H04 若备份接口全库导出耗时长（>60s）在报告中记录实际耗时。

- [ ] **Step 2: 跑绿 → Step 3: Commit**

```bash
cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_data_full_support.py -v
# Expected: 5 passed（两遍稳定）
git add server/tests/test_data_full_support.py
git commit -m "test(data-full): 族H L2 —— 评论CRUD与权限/时间线合并/备份建列下删 5 例"
```

---

### Task 6: 族F/G/H L3 三例（TD-F16/G08/H06）

**Files:**
- Create: `e2e/data-full/data-automation.spec.ts`

**Interfaces:**
- Consumes: helpers；行动作页拓扑经 API 建（webhook 规则 + rowActions PUT，同 Task 3 模式）；stub 不需要（规则 url 可指向不存在的本地端口——L3 只验证 UI 触发与 running 状态出现，不等待回写）。
- Produces: 族F/G/H L3 基线 3 例。

- [ ] **Step 1: 写 data-automation.spec.ts（TD-F16/G08/H06）**

用例要点（先读 `src/components/dynamic/RowActionRunner.vue`、`src/components/common/DataTable.vue` extra-actions 下拉、`src/components/common/RecordTimeline.vue`、DynamicPage 工具栏列视图切换器（视图名显示）、DynamicPage.vue:583-594 评论/变更历史区，选择器以实读为准）：

- **TD-F16 行动作 UI 触发**：API 建拓扑（manual 规则指向死端口 url=http://127.0.0.1:1/x、timeout 2 retries 0 + 页带 ra_status 四值 + rowActions approve）→ UI 建一条记录 → 行下拉出现「审批」项 → 点击 → RowActionRunner 对话框出现（确认/参数表单）→ 确认 → 行状态字段显示 running（或提交反馈）→ API 轮询最终 failed（死端口失败回写 failed——比 done 更 deterministic）→ 断言。afterAll：删规则+删页。
- **TD-G08 列视图切换 UI**：API 建两个列视图（全列/仅名称列，public）→ UI 工具栏切换器选「仅名称」→ 表格列数变化（数量列表头消失）→ 切回全列视图 → 恢复 → 截图 view-switched。
- **TD-H06 评论/时间线 UI**：API 建 1 条记录 + 1 条 API 评论 → 打开查看弹窗「评论 / 变更历史」区 → 断言 API 评论可见 → UI 发一条新评论 → 列表出现 → UI 编辑自己评论 → 删除 → 截图 timeline-panel。afterAll 删页。

- [ ] **Step 2: 跑绿**

```bash
npx playwright test e2e/data-full/data-automation.spec.ts
```

Expected: 3 passed，两遍稳定；全量 `npx playwright test e2e/data-full` → 25 passed（22+3）。截图 automation-row-action / view-switched / timeline-panel。

- [ ] **Step 3: Commit**

```bash
git add e2e/data-full/data-automation.spec.ts
git commit -m "test(data-full): 族F/G/H L3 —— 行动作按钮流/列视图切换/评论时间线面板 3 例"
```

---

### Task 7: 全量跑绿 + 文档收口

**Files:**
- Modify: `docs/data-testing/02-数据管理功能测试用例.md`（族F 表 16 行、族G 表 8 行、族H 表 6 行）
- Modify: `docs/data-testing/01-测试方案.md`（环境事实 #17–#21）
- Modify: `docs/data-testing/04-缺陷记录与修复.md`（已知限制补录）

**Interfaces:**
- Produces: 全量口径 L2 73+27=100、L3 22+3=25。

- [ ] **Step 1: 全量跑**

```bash
cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_data_full_crud.py tests/test_data_full_field_types.py tests/test_data_full_workflow.py tests/test_data_full_relations.py tests/test_data_full_branches.py tests/test_data_full_io.py tests/test_data_full_automation.py tests/test_data_full_views_query.py tests/test_data_full_support.py -q
npx playwright test e2e/data-full
```

Expected: L2 100 passed；L3 25 passed（两遍）。DTEST 残留清扫扩至已知全部表（计划③十四表 + trigger_rules/webhook_rules/column_views/comments——日志类 trigger_logs/webhook_logs/operation_logs 如实报数备案）。

- [ ] **Step 2: 文档收口**

- 02：族F 表（TD-F01–F16 共 16 行）、族G 表（TD-G01–G08 共 8 行）、族H 表（TD-H01–H06 共 6 行），计数行「族F 共 16 例（L2 15 + L3 1）/ 族G 共 8 例（L2 7 + L3 1）/ 族H 共 6 例（L2 5 + L3 1），全部通过」+ 全量执行记录行（L2 100 / L3 25 两轮）。关键契约落单元格（F03 条件不匹配不写日志、F04 失败容忍、F05 无 delete 触发、F07 绑定拦截+脚本异常、F10 before 拦截、F11 签名、F13 异步回写、F15 Open API 同构、G02 默认视图、H04 备份下载）。
- 01 环境事实 #17–#21：#17 触发规则只在 create/update 后独立连接触发、失败不影响主请求、条件不匹配不写日志、无 delete 触发点；#18 校验脚本绑定只能 PUT pageConfigs.validationScript、errors 400 校验失败/warnings 非阻塞、删除脚本 NULL 绑定；#19 webhook before/after 均请求线程内同步含重试、签名 HMAC-SHA256 `{ts}.{body}`、manual 永不自动、rollbackOnFailure 死配置；#20 行动作 run 立即返回后异步回写、四值配置齐、stale 阈值 max(300s, timeout*(retries+1))；#21 列视图 public/private 域与默认单例、查询台无页面级权限、评论不校验记录存在、时间线 op-log 按当前分支过滤、备份全库同步导出。
- 04 已知限制追加：⑮ trigger_logs/webhook_logs/operation_logs 无清理端点（日志残留备案）；⑯ webhook rollbackOnFailure 死配置（dynamic.py 不消费）；⑰ 备份 restore/factory-reset 红线无自动化覆盖（人为破坏性操作）。

- [ ] **Step 3: Commit + 自查**

```bash
git add docs/data-testing/
git commit -m "docs(data-testing): 族F/G/H 用例落表——L2 100 例 + L3 25 例全量绿 + webhook 同步语义等已知限制登记"
```

自查：`git status` 干净；`git log --oneline` 含本计划 7 任务 commit；截图 automation-row-action / view-switched / timeline-panel 在 e2e/screenshots/data-full/。

---

## 计划⑤ 收口说明（不在本计划内实施）

- 03 首轮全量执行报告（九文件 L2 100 + 五 spec L3 25 + 时长与证据索引）。
- 残留一次性清理（data_files/import_runs/merge_records/merge_backups/project_versions/snapshots/user_current_project_branch/workflow 两表/trigger_rules/webhook_rules 日志类）。
- flaky 观察评估（TD-B15/B16/C12/C14）与 etl_async marker 分层。
- user-guide 核对（行为变更点）。
