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


def test_readvance_after_reject_finds_instance_and_progresses():
    """回退后重新办理上游并再次推进，实例应能被定位并前进（防回退后卡死）。"""
    with get_db() as conn:
        cur = conn.cursor(); _setup(cur)
        d = repo.get_definition(cur, 'wf-adv')
        d['stages'][1]['rejectTransition'] = {'from': '设计中', 'to': '退回'}
        d['stages'][1]['statusField'] = 'dstatus'
        repo.save_definition(cur, d)
        # 推进 s1→s2
        on_transition(cur, 'zzreq', 'req-a', 'status', '待评审', '已通过',
                      {'status': '待评审', 'title': '登录'}, {'status': '已通过', 'title': '登录'}, 'admin', 'admin')
        conn.commit()
        inst = repo.get_instance(cur, 'inst-adv'); down_id = inst['chain'][1]['recordId']
        # s2 驳回 → 回到 s1
        on_transition(cur, 'zzdesign', down_id, 'dstatus', '设计中', '退回',
                      {'dstatus': '设计中'}, {'dstatus': '退回'}, 'admin', 'admin', comment='补充')
        conn.commit()
        # 回退后定位上游记录的实例应成功
        found = repo.find_running_instance_by_record(cur, 'zzreq', 'req-a')
        assert found is not None and found['current_stage_id'] == 's1'
        # 重新推进 s1→s2，实例应再次前进
        on_transition(cur, 'zzreq', 'req-a', 'status', '待评审', '已通过',
                      {'status': '待评审', 'title': '登录'}, {'status': '已通过', 'title': '登录'}, 'admin', 'admin')
        conn.commit()
        inst2 = repo.get_instance(cur, 'inst-adv')
        # 回退后再推进必须复用同一下游记录，不得重复 spawn / chain 无限增长（D3 防重）
        cur.execute("SELECT COUNT(*) FROM dynamic_data WHERE collection='zzdesign'")
        down_count = cur.fetchone()[0]
    assert inst2['current_stage_id'] == 's2'
    assert len(inst2['chain']) == 2, f'chain 不应增长: {inst2["chain"]}'
    assert down_count == 1, f'回退后重推进重复 spawn 了下游记录: {down_count}'
