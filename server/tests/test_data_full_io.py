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
    """批内重复 ID：默认整批 409 拒绝；continueOnError 放行后逐条记错。

    实测适配（brief 注意块，证据 routes/dynamic.py:1151-1154）：带重复 id 的
    每一条（含首次出现）都按 per-record error 跳过，并非「首条 upsert、
    后续跳过」——全重复批次 0 行写入；仅批内非重复记录正常处理。"""
    rid = uuid.uuid4().hex
    r = live.api('POST', f"/{pageh['collection']}/batch-create", admin,
                 {'records': [{'id': rid, 'data': {'name': '重复a'}},
                              {'id': rid, 'data': {'name': '重复b'}}]})
    assert r.status_code == 409  # 批内重复 ID，整批拒绝
    cont = live.api('POST', f"/{pageh['collection']}/batch-create", admin,
                    {'records': [{'id': rid, 'data': {'name': '重复a'}},
                                 {'id': rid, 'data': {'name': '重复b'}},
                                 {'id': rid + '-c', 'data': {'name': '重复c'}}],
                     'options': {'continueOnError': True}})
    assert cont.status_code < 300, f'{cont.status_code} {cont.text[:300]}'
    body = cont.json()
    assert body['created'] == 1 and body['failed'] == 2  # 重复两条均记错，唯一条写入
    names = _names(admin, pageh['collection'])
    assert sum(1 for n in names if n.startswith('重复')) == 1
    assert '重复c' in names and '重复a' not in names and '重复b' not in names


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
    assert r.status_code == 201, f'{r.status_code} {r.text[:300]}'
    run_id = r.json()['id']
    rr = live.api('POST', f'/importRuns/{run_id}/retry-result', admin,
                  {'resolvedRecordIds': ['row-9'], 'successDelta': 1,
                   'createdDelta': 1, 'updatedDelta': 0})
    assert rr.status_code < 300, f'{rr.status_code} {rr.text[:300]}'
    got = live.api('GET', f'/importRuns/{run_id}', admin)
    assert len(got.json()['failures']) == 0  # 已解决 failures 被删
    assert got.json()['run']['status'] == 'success'  # 重算


def test_td_e05_import_run_validation_and_pagination(admin, pageh):
    """缺参行为实测（brief 注意块适配）：POST /importRuns 无必填字段校验——
    routes/import_runs.py 全部 body.get 带默认，只给 pageId/collection/fileName
    也是 201，统计字段默认 0、failedCount=0 → status 'success'，而非 brief
    预期的 400；分页 limit/offset 严格生效。"""
    bad = live.api('POST', '/importRuns', admin,
                   {'pageId': pageh['page_id'], 'collection': pageh['collection'],
                    'fileName': 'DTEST-E-缺参.xlsx'})
    assert bad.status_code == 201, f'{bad.status_code} {bad.text[:300]}'  # 实测：无校验
    got = live.api('GET', f"/importRuns/{bad.json()['id']}", admin)
    assert got.status_code == 200
    assert got.json()['run']['failedCount'] == 0
    assert got.json()['run']['status'] == 'success'  # 缺省统计 → success
    lst = live.api('GET',
                   f"/importRuns?pageId={pageh['page_id']}&collection={pageh['collection']}&limit=1&offset=0",
                   admin)
    assert lst.status_code == 200
    assert len(lst.json()['runs']) <= 1 and 'total' in lst.json()


# ---------------------------------------------------------------------------
# ETL（TD-E06–E10）
# ---------------------------------------------------------------------------

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
    """实测适配（brief 注意块，证据 routes/etl_tasks.py 路由表）：产品没有
    GET /etlTasks/<task_id> 单任务端点——<task_id> 路径只挂 PUT/DELETE，
    GET 落 404。steps 落库断言改经列表端点 GET /etlTasks（返回含 steps 的
    完整任务字典）。"""
    t = _etl_task(admin, pageh)
    try:
        got = live.api('GET', f"/etlTasks/{t['id']}", admin)
        assert got.status_code == 404  # 产品无单任务 GET 端点
        lst = live.api('GET', '/etlTasks', admin)
        assert lst.status_code == 200
        mine = [x for x in lst.json() if x['id'] == t['id']]
        assert mine, f'新建任务应出现在列表: {t["id"]}'
        assert mine[0]['steps'][0]['type'] == 'json_input'
        assert mine[0]['steps'][1]['type'] == 'save_to_collection'
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


# ---------------------------------------------------------------------------
# 导出与菜单导出（TD-E11–E16）
# ---------------------------------------------------------------------------

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
    bad_id = None
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
        bad_id = bad.json().get('id')
        t2 = live.api('POST', f"/exportScripts/{bad_id}/test", admin,
                      {'collection': pageh['collection']})
        assert t2.status_code == 400 and t2.json().get('success') is False
    finally:
        if bad_id:  # T4-R1：t2 断言失败也回收 bad 脚本，不泄漏 DTEST-E-bad-*
            live.api('DELETE', f"/exportScripts/{bad_id}", admin)
        live.api('DELETE', f"/exportScripts/{s['id']}", admin)


def test_td_e13_export_execute_binary_and_binding_mismatch(admin, pageh):
    s = None
    other = None
    try:
        # T4-R1：setup 挪进 try——make_page 异常时不泄漏已创建的导出脚本
        s = _export_script(admin, pageh['collection'])
        other = live.make_page(admin, 'E', 'exp-other', fields=[NAME])
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
        if other is not None:
            live.drop_page(admin, other)
        if s is not None:
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
    """batchClear 清空整个 collection——用独立页，不碰模块共享 pageh。

    实测适配（brief 注意块，证据 utils/menu_export.py:143-181）：POST
    /menuExport 默认走绑定驱动模式——menu/页面级都无绑定导出脚本时该页被
    跳过，0 文件 → 400「所有导出任务均失败」。故导出前给 solo 页绑定一个
    页面级脚本（body 不传 scriptId，走产品默认路径），finally 一并回收。"""
    solo = live.make_page(admin, 'E', 'menuexp', fields=[NAME, QTY])
    s = None
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
        s = _export_script(admin, solo['collection'])  # 绑定驱动模式需绑定脚本
        z = live.api('POST', '/menuExport', admin,
                     {'menuIds': [solo['menu_id']], 'branchId': 'main'})
        assert z.status_code == 200 and z.content[:2] == b'PK'
        clear = live.api('POST', '/menuExport/batchClear', admin,
                         {'collections': [solo['collection']], 'branchId': 'main'})
        assert clear.status_code < 300
        assert _names(admin, solo['collection']) == {}  # batchClear 清空分支记录
    finally:
        if s is not None:
            live.api('DELETE', f"/exportScripts/{s['id']}", admin)
        live.drop_page(admin, solo)


def test_td_e16_export_missing_result_assignment_rejected(admin, pageh):
    """实际契约：脚本不给 result 赋值 → test 端点 400 success:false。"""
    r = live.api('POST', '/exportScripts', admin, {
        'name': f"DTEST-E-nores-{uuid.uuid4().hex[:8]}",
        'script': "x = 1\n", 'outputFormat': 'csv',
        'scope': 'page', 'boundCollection': pageh['collection']})
    assert r.status_code < 300, f'{r.status_code} {r.text[:300]}'  # T4-R1：失败给明确诊断
    script_id = r.json()['id']
    try:
        t = live.api('POST', f"/exportScripts/{script_id}/test", admin,
                     {'collection': pageh['collection']})
        assert t.status_code == 400 and t.json().get('success') is False
    finally:
        live.api('DELETE', f"/exportScripts/{script_id}", admin)
