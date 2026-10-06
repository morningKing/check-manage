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
    # 字段级 workflowConfig 与编排并存（GC #8）：statusField 启用工作流后，
    # 空 transitions 会以 400 拒掉一切非首写状态变更——推进 PUT 根本到不了
    # 编排层。故字段级声明与两条编排边一致的合法转换（roles 缺省=[] 放行
    # 所有角色），让 403 分支由编排 assignedRoles 门禁独立触发。
    page = live.make_page(admin, 'B', 'orch2', fields=[_status_field(transitions=[
        {'from': 'todo', 'to': 'doing', 'label': '开始'},
        {'from': 'doing', 'to': 'done', 'label': '完成'},
    ])])
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
