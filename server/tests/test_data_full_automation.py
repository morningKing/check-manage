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


# ---- Webhook TD-F09–F12 ----

class _StubHandler(BaseHTTPRequestHandler):
    server_version = 'DTESTStub/1.0'

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length)
        # 引擎按 title-case 发送（X-Webhook-Event 等，utils/webhook_engine.py:331-336）；
        # HTTP 头名大小写不敏感，stub 统一小写存档便于断言。
        rec = {'path': self.path,
               'headers': {k.lower(): v for k, v in self.headers.items()
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
    rule = None
    try:
        rule = _webhook_rule(admin, stub, page, event='manual')
        lst = live.api('GET', '/webhook/rules', admin)
        assert any(x['id'] == rule['id'] for x in lst.json())
        # manual 永不自动触发：真实 update 后 stub 零命中
        hits = len(stub.requests)
        rec = _rec(admin, page['collection'], 'manual对象')
        _upd(admin, page['collection'], rec['id'], rec['_version'], name='manual对象改')
        # webhook 在请求线程内同步执行（§4 事实#19），PUT 返回即确定，无需负等待
        assert len(stub.requests) == hits
    finally:
        if rule is not None:
            dele = live.api('DELETE', f"/webhook/rules/{rule['id']}", admin)
            assert dele.status_code < 300
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
        # 真实签名格式（utils/webhook_engine.py:327-329 + 393-405）：
        # payload_json = json.dumps(payload, ensure_ascii=False) 且原样作为
        # 请求体字节发送（data=payload_json.encode('utf-8')，无尾换行），
        # message = f'{timestamp}.{payload}'——故用收到的 body 原文重算即可。
        expected = hmac.new(secret.encode(), f'{ts}.{hit["body"]}'.encode(),
                            hashlib.sha256).hexdigest()
        assert sig == expected, f'签名不符: {sig} != {expected}'
        # payload 真实结构（utils/webhook_engine.py _build_payload :248-256 公共键
        # event/timing/timestamp/ruleId/ruleName/operator/branchId；:259-277 数据事件
        # 追加 collection/pageName/recordId；:296-300 after/create 追加 record=new_data）
        assert payload.get('recordId') == rec['id'], \
            f'payload.recordId 应为记录 id: {payload.get("recordId")!r}'
        assert payload.get('event') == 'create' and payload.get('timing') == 'after'
        assert payload.get('collection') == page['collection']
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
        fail = _wait(lambda: next((x for x in live.api(
            'GET', f"/webhook/rules/{rule['id']}/logs", admin).json()['logs']
            if x['success'] is False), None), timeout_s=15, label='失败日志')
        assert fail['responseStatus'] == 500
        # ?success= 过滤契约：per-rule 日志端点按 success 布尔过滤且 200
        # （routes/webhooks.py:300-306 条件种子，" AND ".join 拼 SQL）
        fls = live.api('GET', f"/webhook/rules/{rule['id']}/logs?success=false", admin)
        assert fls.status_code == 200, f'{fls.status_code} {fls.text[:300]}'
        assert isinstance(fls.json().get('logs'), list) and fls.json()['logs']
        assert all(x['success'] is False for x in fls.json()['logs'])
        tru = live.api('GET', f"/webhook/rules/{rule['id']}/logs?success=true", admin)
        assert tru.status_code == 200 and tru.json()['logs']
        assert all(x['success'] is True for x in tru.json()['logs'])
        # 全局日志端点同样支持 success 过滤
        glob = live.api('GET', '/webhook/logs?success=false&limit=200', admin)
        assert glob.status_code == 200 and 'total' in glob.json()
        match = [x for x in glob.json()['logs']
                 if x['ruleId'] == rule['id'] and x['success'] is False]
        assert match and match[0]['responseStatus'] == 500
        assert live.api('GET', f"/{page['collection']}/{rec['id']}",
                        admin).json()['name'] == '失败行改'  # after 失败不影响业务
    finally:
        stub.respond_code = 200
        live.api('DELETE', f"/webhook/rules/{rule['id']}", admin)
        live.drop_page(admin, page)


# ---- 行动作 TD-F13–F15 ----

def _action_page(admin, stub):
    """manual webhook 规则先行（pageConfigs.rowActions 校验其存在），页带四值状态字段。"""
    rule_r = live.api('POST', '/webhook/rules', admin, {
        'name': f"DTEST-F-ra-{uuid.uuid4().hex[:8]}",
        'sourceCollections': [], 'triggerEvent': 'manual',
        'triggerTiming': 'after',
        'webhookUrl': f"http://127.0.0.1:{stub.server_address[1]}/row-action",
        'secret': '', 'timeout': 2, 'retries': 0})
    assert rule_r.status_code == 201, f'{rule_r.status_code} {rule_r.text[:300]}'
    rule = rule_r.json()
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
        # stub 返回 {"ok": true}；实际契约（utils/row_action_engine.py）：
        # _map_response 取回的是布尔 True（:331-332），write_back 以 str(val)
        # 落库（:77-78 to_jsonb(%s::text)）——即 Python 的 'True'，不是 'true'。
        assert done.get('ra_receipt') == 'True'
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
    """Open API 入口与内部同构：X-API-Key 调 run → 同样异步回写。

    实际绑定模型（实读 routes/api_keys.py + auth.py:213 require_bound_key）：
    密钥**不绑页**，创建时即绑创建者（owner_user_id = 当前登录用户），
    require_bound_key 只拒 owner_user_id 为空的存量密钥——POST /apiKeys
    只要 name 即可（pageBindings 非存储字段）。另一道前置闸：Open API 只放行
    apiPublic + apiWritable 的页（routes/open_api.py check_collection_writable，
    两列默认 False，不打开直接 404 not public）；逐键 SET 的部分 PUT 不动
    rowActions（routes/page_configs.py:210-218）。
    """
    page, rule = _action_page(admin, stub)
    try:
        open_put = live.api('PUT', f"/pageConfigs/{page['page_id']}", admin,
                            {'apiPublic': True, 'apiWritable': True})
        assert open_put.status_code < 300, \
            f'apiPublic/apiWritable 打开失败 {open_put.status_code} {open_put.text[:300]}'
        key = live.api('POST', '/apiKeys', admin,
                       {'name': f'DTEST-F-key-{uuid.uuid4().hex[:6]}'})
        assert key.status_code == 201, f'{key.status_code} {key.text[:300]}'
        try:
            rec = _rec(admin, page['collection'], '开放对象')
            import requests as _rq
            resp = _rq.post(
                f'{live.BASE}/api/v1/collections/{page["collection"]}/{rec["id"]}'
                f'/row-actions/approve/run',
                headers={'X-API-Key': key.json()['key']}, json={}, timeout=15)
            assert resp.status_code < 300, f'{resp.status_code} {resp.text[:300]}'
            assert resp.json()['ok'] is True
            assert resp.json()['status'] in ('running', 'submitted')
            assert resp.json()['statusField'] == 'ra_status'
            done = _wait(lambda: (lambda g: g.json().get('ra_status') in ('done', 'failed')
                                  and g.json() or None)(
                live.api('GET', f"/{page['collection']}/{rec['id']}", admin)),
                timeout_s=20, label='开放入口回写')
            assert done['ra_status'] == 'done'
            assert done.get('ra_receipt') == 'True'
        finally:
            live.api('DELETE', f"/apiKeys/{key.json()['id']}", admin)
    finally:
        live.api('DELETE', f"/webhook/rules/{rule['id']}", admin)
        live.drop_page(admin, page)
