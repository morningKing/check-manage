"""族B 字段控制类型 —— L2 live-server API 层（TD-B01–B09）。

已核实契约（计划② Global Constraints #1–#7）：autoSequence 仅 create 服务端
分配且覆盖客户端值 / PUT 不重分配 / batch-create 不分配且 reseed；
autoTimestamp 与 compositeText 纯前端（服务端零处理，L2 断言该事实）；
select 无服务端选项校验；date/datetime 原样存储；上传扩展名白名单为字段级
fileConfig.allowedExtensions（缺 fieldName / 字段无配置 = 不限制，B09 断言
真实的服务端拦截路径）。
补充实测：create 响应回显客户端载荷（仅补 _version、去 _relations），不回带
服务端分配值——autoSequence 分配结果以 GET 回读为准（routes/dynamic.py
create_item 的 body 处理），故 B01–B04 一律 GET 后断言。
"""
import concurrent.futures
import uuid

import pytest

import data_full_live as live

pytestmark = pytest.mark.data_full

B_FIELDS = [
    {'id': 'f1', 'label': '名称', 'fieldName': 'name', 'controlType': 'text',
     'required': True, 'order': 1,
     'fileConfig': {'allowedExtensions': ['.txt']}},
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


def _post(admin, collection, data):
    return live.api('POST', f'/{collection}', admin, data)


def test_td_b01_autosequence_format_and_increment(admin, pageh):
    r1 = _post(admin, pageh['collection'], {'id': uuid.uuid4().hex, 'name': '甲'})
    r2 = _post(admin, pageh['collection'], {'id': uuid.uuid4().hex, 'name': '乙'})
    assert r1.status_code == 201 and r2.status_code == 201
    # create 响应不回带分配值，GET 回读存储记录
    g1 = live.api('GET', f"/{pageh['collection']}/{r1.json()['id']}", admin).json()
    g2 = live.api('GET', f"/{pageh['collection']}/{r2.json()['id']}", admin).json()
    # 服务端覆盖客户端值（客户端未传 sn），pad=len('999')=3
    assert g1['sn'] == 'DTS-001', f"得 {g1.get('sn')}"
    assert g2['sn'] == 'DTS-002'


def test_td_b02_autosequence_concurrent_no_duplicates(admin, pageh):
    """8 并发创建：计数行 FOR UPDATE + advisory lock 保证不重号。"""
    def create(i):
        return _post(admin, pageh['collection'],
                     {'id': uuid.uuid4().hex, 'name': f'并发{i}'}).json()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        recs = list(ex.map(create, range(8)))
    sns = [live.api('GET', f"/{pageh['collection']}/{rec['id']}", admin).json().get('sn')
           for rec in recs]
    assert all(sns), f'缺 sn: {sns}'
    assert len(set(sns)) == 8, f'重号: {sns}'


def test_td_b03_autosequence_put_not_reallocated(admin, pageh):
    rec = _post(admin, pageh['collection'],
                {'id': uuid.uuid4().hex, 'name': '改写'}).json()
    put = live.api('PUT', f"/{pageh['collection']}/{rec['id']}", admin,
                   {'sn': 'XX-999', '_version': rec['_version']})
    assert put.status_code < 300
    after = live.api('GET', f"/{pageh['collection']}/{rec['id']}", admin).json()
    assert after['sn'] == 'XX-999'  # PUT 存什么是什么
    nxt = _post(admin, pageh['collection'],
                {'id': uuid.uuid4().hex, 'name': '后续'}).json()
    got = live.api('GET', f"/{pageh['collection']}/{nxt['id']}", admin).json()
    assert got['sn'].startswith('DTS-')  # 计数器未受 PUT 影响，继续自增


def test_td_b04_autosequence_batch_no_alloc_and_reseed(admin, pageh):
    """batch-create 不分配：导入值原样保留，且计数器 reseed 到 GREATEST。"""
    r = live.api('POST', f"/{pageh['collection']}/batch-create", admin,
                 {'records': [{'id': uuid.uuid4().hex,
                               'data': {'name': '导入甲', 'sn': 'DTS-050'}} for _ in range(1)]})
    assert r.status_code < 300
    got = live.api('GET', f"/{pageh['collection']}", admin,
                   ).json() if False else live.api('GET', f"/{pageh['collection']}?keyword=导入甲", admin).json()
    assert got['data'][0]['sn'] == 'DTS-050'
    nxt = _post(admin, pageh['collection'],
                {'id': uuid.uuid4().hex, 'name': 'reseed后'}).json()
    got = live.api('GET', f"/{pageh['collection']}/{nxt['id']}", admin).json()
    assert got['sn'] == 'DTS-051', f'reseed 到 GREATEST 后应 051，得 {got["sn"]}'


def test_td_b05_select_no_server_validation(admin, pageh):
    """实际契约：服务端不校验选项，任意值直接入库（前端负责约束）。"""
    r = _post(admin, pageh['collection'],
              {'id': uuid.uuid4().hex, 'name': '非法值', 'sel': '不存在的选项'})
    assert r.status_code == 201
    assert live.api('GET', f"/{pageh['collection']}/{r.json()['id']}", admin).json()['sel'] == '不存在的选项'


def test_td_b06_date_datetime_stored_verbatim(admin, pageh):
    rec = _post(admin, pageh['collection'],
                {'id': uuid.uuid4().hex, 'name': '日期',
                 'd': '2026-01-02', 'dt': '2026-01-02 03:04:05'}).json()
    got = live.api('GET', f"/{pageh['collection']}/{rec['id']}", admin).json()
    assert got['d'] == '2026-01-02' and got['dt'] == '2026-01-02 03:04:05'  # 无归一化


def test_td_b07_autotimestamp_server_absent(admin, pageh):
    """实际契约：autoTimestamp 纯前端填充，API 直连创建服务端不补值。

    create 响应回显客户端载荷，须 GET 回读存储记录断言「服务端没存」。"""
    rec = _post(admin, pageh['collection'],
                {'id': uuid.uuid4().hex, 'name': '无ts'}).json()
    got = live.api('GET', f"/{pageh['collection']}/{rec['id']}", admin).json()
    assert 'ts' not in got, f'服务端不应存储 ts: {got.get("ts")}'


def test_td_b08_compositetext_server_absent(admin, pageh):
    """实际契约：compositeText 前端计算，API 直连创建服务端不计算。"""
    rec = _post(admin, pageh['collection'],
                {'id': uuid.uuid4().hex, 'name': '组合'}).json()
    got = live.api('GET', f"/{pageh['collection']}/{rec['id']}", admin).json()
    assert 'comp' not in got, f'服务端不应存储 comp: {got.get("comp")}'


def test_td_b09_file_upload_endpoint(admin, pageh):
    """上传→下载回读→扩展名白名单 400。记录值数组契约由 L3 UI 链路覆盖。"""
    r = live.upload_file(admin, 'hello 数据文件'.encode('utf-8'), 'dtest-b.txt',
                         collection=pageh['collection'], field_name='name')
    assert r.status_code == 201, f'{r.status_code} {r.text[:200]}'
    body = r.json()
    assert body['url'] == f"/api/data-files/{body['id']}/download"
    dl = live.api('GET', f"/data-files/{body['id']}/download", admin)
    assert dl.status_code == 200 and dl.content.decode('utf-8') == 'hello 数据文件'
    bad = live.upload_file(admin, b'x', 'dtest-b.sh',
                           collection=pageh['collection'], field_name='name')
    assert bad.status_code == 400  # 字段级白名单：.sh 不在 ['.txt'] → 服务端 400
