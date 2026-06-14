import psycopg2.extras
from db import get_db
from utils import workflow_repo as repo
from utils.workflow_engine import on_transition
from tests.test_workflow_advance import _setup  # 复用两阶段 setup


def test_reject_moves_instance_back_and_resets_upstream():
    with get_db() as conn:
        cur = conn.cursor(); _setup(cur)
        d = repo.get_definition(cur, 'wf-adv')
        d['stages'][1]['rejectTransition'] = {'from': '设计中', 'to': '退回'}
        d['stages'][1]['statusField'] = 'dstatus'
        repo.save_definition(cur, d)
        # 先推进到 s2
        on_transition(cur, 'zzreq', 'req-a', 'status', '待评审', '已通过',
                      {'status': '待评审', 'title': '登录'}, {'status': '已通过', 'title': '登录'}, 'admin', 'admin')
        conn.commit()
        inst = repo.get_instance(cur, 'inst-adv'); down_id = inst['chain'][1]['recordId']
        # s2 驳回：dstatus 设计中→退回
        on_transition(cur, 'zzdesign', down_id, 'dstatus', '设计中', '退回',
                      {'dstatus': '设计中'}, {'dstatus': '退回'}, 'admin', 'admin', comment='需补充')
        conn.commit()
        inst2 = repo.get_instance(cur, 'inst-adv')
    assert inst2['current_stage_id'] == 's1'
    assert any(h['action'] == 'reject' and h['comment'] == '需补充' for h in inst2['history'])
