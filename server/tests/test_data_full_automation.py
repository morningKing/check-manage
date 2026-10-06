"""族F 自动化引擎 —— L2 live-server API 层。

触发规则+校验脚本（TD-F01–F08）/ Webhook（TD-F09–F12）/ 行动作（TD-F13–F15，
Task 2/3 续加本文件）。契约锚点（计划④ Global Constraints，已按源码定稿）：
- 触发只在 create/update 后同步执行（dynamic.py :713/:947 独立 get_db 连接、
  主事务提交后），失败不影响主请求；条件/事件不匹配→静默跳过不写日志；
  disabled 被过滤；无 delete 触发调用点（event='delete' 规则永不触发）。
- triggerCondition 是**扁平 {field, value}**（引擎只读这两个键，无 type 键）；
  guard 为 `cond_field and cond_value is not None`——value='' 也参与比较。
  triggerEvent 引擎另支持 'fieldChange'（仅 update 且字段值变化时触发），
  本计划契约限定 create/update，不覆盖。
- actionType 'create'：actionConfig.fieldMapping {目标字段: $source.field|
  $source.id|$operator|$NOW|字面量}；'update'：{matchField, matchValue,
  updateFields}——只 UPDATE data->>matchField 匹配到的**既有**行，从不新建；
  'runScript'：{scriptId} → 线程内 run_validation_script，抛异常→error 日志。
  dynamic_data.collection 无外键 → 动作写「已删页面」的 collection 会**成功**
  （孤儿行+reseed 空转）并记 success——故失败注入用 runScript 引用必崩脚本。
- 校验脚本：POST /validationScripts {name, description?, script}；test 端点
  {record, action, fields, collection} → **success == (errors 为空)**（errors
  非空也是 200）；异常 → 400 {success:false, errors:[str]}。沙箱 locals 预初始化
  errors=[]/warnings=[] 并注入 add_error/add_warning/record/action/old_data/
  fields/collection/query/find_by/...（脚本直接调 add_error 即可，无需兜底前缀）。
  绑定只能 PUT /pageConfigs/<id> {validationScript}；create/update errors→
  400 {"error":"校验失败","validationErrors":[...],"validationWarnings":[...]}，
  warnings 非阻塞；脚本异常→400 校验脚本执行错误：...；删脚本 NULL 化绑定。
"""
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
        pytest.skip('dev 栈未启动（Vite :5173 代理 /api → Flask :3002）')
    return live.login()


def _src_dst(admin, purpose):
    """双页拓扑助手：源页+目标页。make_page#2 失败时回收 #1，不留孤儿。"""
    src = live.make_page(admin, 'F', f'{purpose}-src', fields=[NAME])
    try:
        dst = live.make_page(admin, 'F', f'{purpose}-dst', fields=[NAME])
    except Exception:
        live.drop_page(admin, src)
        raise
    return src, dst


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


def _drop_rule(admin, rule_id):
    live.api('DELETE', f'/triggerRules/{rule_id}', admin)


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
    src, dst = _src_dst(admin, 'trg')
    rule = None
    try:
        rule = _trigger(admin, src['collection'], dst['collection'], enabled=False)
        got = live.api('GET', f"/triggerRules/{rule['id']}", admin)
        assert got.status_code == 200 and got.json()['enabled'] is False
        put = live.api('PUT', f"/triggerRules/{rule['id']}", admin, {'enabled': True})
        assert put.status_code < 300
        got2 = live.api('GET', f"/triggerRules/{rule['id']}", admin)
        assert got2.status_code == 200 and got2.json()['enabled'] is True
        unknown = live.api('GET', '/triggerRules/DTEST-nope', admin)
        assert unknown.status_code == 404
    finally:
        if rule:
            _drop_rule(admin, rule['id'])
        live.drop_page(admin, src)
        live.drop_page(admin, dst)


def test_td_f02_trigger_update_action_updates_existing_target(admin):
    # 引擎语义：actionType='update' 只改写目标 collection 中
    # data->>matchField == matchValue 的**既有**行，从不新建——故预置目标行。
    src, dst = _src_dst(admin, 'trg2')
    rule = None
    try:
        _rec(admin, dst['collection'], '改名后')  # 预置目标行
        rule = _trigger(admin, src['collection'], dst['collection'], event='update',
                        condition={'field': 'name', 'value': '改名后'},
                        action='update',
                        mapping={'matchField': 'name', 'matchValue': '$source.name',
                                 'updateFields': {'name': '触发乙改'}})
        _rec(admin, src['collection'], '触发甲')  # create 不触发（event=update）
        got = live.api('GET', f'/{dst["collection"]}?all=true', admin).json()
        assert [x['name'] for x in got['data']] == ['改名后'], 'create 不应触发 update 规则'
        rec = _rec(admin, src['collection'], '触发乙')
        _upd(admin, src['collection'], rec['id'], rec['_version'], name='改名后')
        target = _wait(lambda: [x for x in live.api(
            'GET', f'/{dst["collection"]}?all=true', admin).json()['data']
            if x.get('name') == '触发乙改'], label='目标行被 update 动作改写')
        assert target, 'update 触发应改写既有目标行'
        logs = live.api('GET', f"/triggerRules/{rule['id']}/logs", admin).json()
        assert len(logs) == 1 and logs[0]['status'] == 'success'
    finally:
        if rule:
            _drop_rule(admin, rule['id'])
        live.drop_page(admin, src)
        live.drop_page(admin, dst)


def test_td_f03_trigger_condition_mismatch_no_log_hit_fires(admin):
    src, dst = _src_dst(admin, 'trg3')
    rule = None
    try:
        rule = _trigger(admin, src['collection'], dst['collection'], event='create',
                        condition={'field': 'name', 'value': '命中词'})
        # ① 不命中：静默跳过，不写日志、不建目标
        _rec(admin, src['collection'], '不命中')
        assert live.api('GET', f'/{dst["collection"]}?all=true', admin).json()['total'] == 0
        logs = live.api('GET', f"/triggerRules/{rule['id']}/logs", admin).json()
        assert logs == []
        # ② 命中：create 动作建目标 + success 日志
        _rec(admin, src['collection'], '命中词')
        _wait(lambda: live.api('GET', f'/{dst["collection"]}?all=true',
                               admin).json()['total'] == 1, label='命中触发')
        logs = live.api('GET', f"/triggerRules/{rule['id']}/logs", admin).json()
        assert len(logs) == 1 and logs[0]['status'] == 'success'
    finally:
        if rule:
            _drop_rule(admin, rule['id'])
        live.drop_page(admin, src)
        live.drop_page(admin, dst)


def test_td_f04_trigger_failure_does_not_fail_main_request(admin):
    # 失败注入：runScript 引用必崩脚本（1/0）——引擎线程内执行抛
    # ZeroDivisionError → error 日志；主请求仍 201（dynamic.py 外层 try/except）。
    # 注：dynamic_data.collection 无外键，写「已删页面」的 create 动作不会失败
    # （成功落孤儿行），故不能用「目标页先删」制造失败。
    src, dst = _src_dst(admin, 'trg4')
    rule = None
    crash = None
    try:
        crash = _val_script(admin, VAL_CRASH)
        rule = _trigger(admin, src['collection'], dst['collection'], event='create',
                        condition={'field': 'name', 'value': '引爆'},
                        action='runScript', mapping={'scriptId': crash['id']})
        r = live.api('POST', f'/{src["collection"]}', admin,
                     {'id': uuid.uuid4().hex, 'name': '引爆'})
        assert r.status_code == 201, f'触发失败不应影响主请求，得 {r.status_code}'

        def _err_log():
            logs = live.api('GET', f'/triggerRules/{rule["id"]}/logs', admin).json()
            return next((x for x in logs if x.get('status') == 'error'), None)

        err = _wait(_err_log, label='error 日志出现')
        assert 'division by zero' in (err.get('errorMessage') or ''), f'{err}'
    finally:
        if rule:
            _drop_rule(admin, rule['id'])
        if crash:
            live.api('DELETE', f"/validationScripts/{crash['id']}", admin)
        live.drop_page(admin, src)
        live.drop_page(admin, dst)


def test_td_f05_trigger_delete_event_never_fires(admin):
    """实际契约：无 delete 触发调用点——delete 事件规则永不触发。"""
    src, dst = _src_dst(admin, 'trg5')
    rule = None
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
        if rule:
            _drop_rule(admin, rule['id'])
        live.drop_page(admin, src)
        live.drop_page(admin, dst)


# ---- 校验脚本 TD-F06–F08 ----

# 沙箱 locals 预初始化 errors=[]/warnings=[]（utils/script_runner.py
# run_validation_script），脚本直接调 add_error/add_warning 即可——
# 无需（brief 草稿里的）`errors = errors or []` 兜底前缀。
VAL_OK = ("if not (record.get('name') or '').strip():\n"
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
        # 实际契约：success == (errors 为空)——errors 非空也是 200，success=False
        t = live.api('POST', f"/validationScripts/{s['id']}/test", admin,
                     {'record': {'name': ''}, 'action': 'create',
                      'fields': [NAME], 'collection': 'test'})
        assert t.status_code == 200
        assert t.json()['success'] is False
        assert t.json()['errors'] == ['名称不能为空']
        assert t.json()['warnings'] == []
        t2 = live.api('POST', f"/validationScripts/{s['id']}/test", admin,
                      {'record': {'name': '合法', 'qty': 999}, 'action': 'create',
                       'fields': [NAME], 'collection': 'test'})
        assert t2.status_code == 200
        assert t2.json()['success'] is True
        assert t2.json()['errors'] == [] and t2.json()['warnings'] == ['数量偏大']
    finally:
        live.api('DELETE', f"/validationScripts/{s['id']}", admin)


def test_td_f07_validation_binding_blocks_create_and_update(admin):
    page = live.make_page(admin, 'F', 'val-bind', fields=[
        NAME, {'id': 'f2', 'label': '数量', 'fieldName': 'qty',
               'controlType': 'number', 'required': False, 'order': 2}])
    s = _val_script(admin, VAL_OK)
    crash = None
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
    finally:
        if crash:
            live.api('DELETE', f"/validationScripts/{crash['id']}", admin)
        live.api('DELETE', f"/validationScripts/{s['id']}", admin)  # 删除 NULL 化绑定
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
