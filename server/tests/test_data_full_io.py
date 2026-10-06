"""族E 数据进出 —— L2 live-server API 层。

导入（TD-E01–E05）/ ETL（TD-E06–E10）/ 导出与菜单导出（TD-E11–E16）。
契约锚点（计划③ Global Constraints #8–#12）：batch-create 已存在 id=upsert UPDATE；
POST /importRuns 是纯历史登记（解析在前端 SheetJS）；ETL dryRun 同步回滚、真跑
async 202 + 轮询日志、save 恒写 main、cancel 已结束 409；导出脚本 python 必须给
result 赋值、execute 二进制、batchExport ZIP、menuExport 只有导出无导入。
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
