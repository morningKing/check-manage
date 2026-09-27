"""并发/编排缺陷修复回归：分支隔离(D1)、下游状态播种(D7)。

复用 test_workflow_advance._setup 的两阶段定义（zzreq→zzdesign），engine 直连真实 DB。
"""
import psycopg2.extras
from db import get_db
from utils import workflow_repo as repo
from utils.workflow_engine import on_transition
from tests.test_workflow_advance import _setup


def test_advance_seeds_downstream_status_to_stage_from():
    """D7：spawn 下游时应把下游状态字段播种为下一阶段 advanceTransition.from（'设计中'），
    否则下游永远进不了可推进态。"""
    with get_db() as conn:
        cur = conn.cursor(); _setup(cur)
        # 给 s2 配 statusField=dstatus + advanceTransition.from=设计中
        d = repo.get_definition(cur, 'wf-adv')
        d['stages'][1]['statusField'] = 'dstatus'
        d['stages'][1]['advanceTransition'] = {'from': '设计中', 'to': '完成'}
        repo.save_definition(cur, d)
        conn.commit()
        on_transition(cur, 'zzreq', 'req-a', 'status', '待评审', '已通过',
                      {'status': '待评审', 'title': '登录'}, {'status': '已通过', 'title': '登录'},
                      'admin', 'admin')
        conn.commit()
        inst = repo.get_instance(cur, 'inst-adv'); down_id = inst['chain'][1]['recordId']
        cur.execute("SELECT data FROM dynamic_data WHERE id=%s", (down_id,))
        data = cur.fetchone()[0]
    assert data['dstatus'] == '设计中', f'下游状态未播种: {data}'


def test_spawn_and_reject_stay_in_source_branch():
    """D1：源记录在非 main 分支时，下游 spawn 与回退重置都必须落在同一分支。"""
    BR = 'zzbr1'
    with get_db() as conn:
        cur = conn.cursor(); _setup(cur)
        d = repo.get_definition(cur, 'wf-adv')
        d['stages'][1]['statusField'] = 'dstatus'
        d['stages'][1]['advanceTransition'] = {'from': '设计中', 'to': '完成'}
        d['stages'][1]['rejectTransition'] = {'from': '设计中', 'to': '退回'}
        repo.save_definition(cur, d)
        # 在分支 BR 放一份上游记录 + 一个绑定它的实例
        cur.execute("DELETE FROM dynamic_data WHERE collection='zzreq' AND branch_id=%s", (BR,))
        cur.execute("DELETE FROM dynamic_data WHERE collection='zzdesign' AND branch_id=%s", (BR,))
        cur.execute("DELETE FROM workflow_instances WHERE id='inst-br'")
        cur.execute("INSERT INTO dynamic_data (id,collection,data,branch_id) VALUES ('req-br','zzreq',%s,%s)",
                    (psycopg2.extras.Json({'status': '待评审', 'title': '登录'}), BR))
        repo.create_instance(cur, 'inst-br', 'wf-adv', 's1', 'zzreq', 'req-br', 'admin')
        conn.commit()
        # 推进：下游必须 spawn 到 BR 分支，而非 main
        on_transition(cur, 'zzreq', 'req-br', 'status', '待评审', '已通过',
                      {'status': '待评审', 'title': '登录'}, {'status': '已通过', 'title': '登录'},
                      'admin', 'admin', branch_id=BR)
        conn.commit()
        inst = repo.get_instance(cur, 'inst-br'); down_id = inst['chain'][1]['recordId']
        cur.execute("SELECT branch_id FROM dynamic_data WHERE id=%s", (down_id,))
        down_branch = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM dynamic_data WHERE id=%s AND branch_id='main'", (down_id,))
        in_main = cur.fetchone()[0]
        # 回退：上游重置也必须命中 BR 分支
        on_transition(cur, 'zzdesign', down_id, 'dstatus', '设计中', '退回',
                      {'dstatus': '设计中'}, {'dstatus': '退回'}, 'admin', 'admin',
                      comment='补充', branch_id=BR)
        conn.commit()
        cur.execute("SELECT data FROM dynamic_data WHERE id='req-br' AND branch_id=%s", (BR,))
        up = cur.fetchone()[0]
    assert down_branch == BR, f'下游记录落到了错误分支: {down_branch}'
    assert in_main == 0, '下游记录错误地泄漏到了 main 分支'
    assert up['status'] == '待评审', f'回退未在源分支重置上游状态: {up}'
    assert up.get('_rejectComment') == '补充'
    # cleanup branch rows
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM dynamic_data WHERE branch_id=%s", (BR,))
        cur.execute("DELETE FROM dynamic_sequences WHERE branch_id=%s", (BR,))
        cur.execute("DELETE FROM workflow_instances WHERE id='inst-br'")
        conn.commit()


def test_validate_definition_flags_nonprogressable_stage():
    """D6：阶段绑定的状态字段无 workflowConfig（或转换非法）时，validate_definition 给出告警；
    配置正确时无告警。"""
    coll = 'zzwfval'
    with get_db() as conn:
        cur = conn.cursor()
        # 状态字段无 workflowConfig
        cur.execute("DELETE FROM page_configs WHERE id=%s", (f'page-{coll}',))
        cur.execute("INSERT INTO page_configs (id,name,fields) VALUES (%s,%s,%s)",
                    (f'page-{coll}', coll, psycopg2.extras.Json(
                        [{'fieldName': 'st', 'controlType': 'select'}])))
        conn.commit()
        bad = {'name': 'v', 'enabled': True, 'stages': [
            {'id': 's1', 'name': '评审', 'collection': coll, 'statusField': 'st',
             'advanceTransition': {'from': 'a', 'to': 'b'}}]}
        w_bad = repo.validate_definition(cur, bad)
        # 加上合法 workflowConfig 后无告警
        cur.execute("UPDATE page_configs SET fields=%s WHERE id=%s",
                    (psycopg2.extras.Json([{'fieldName': 'st', 'controlType': 'select',
                                            'workflowConfig': {'enabled': True, 'transitions': [
                                                {'from': 'a', 'to': 'b'}]}}]), f'page-{coll}'))
        conn.commit()
        w_ok = repo.validate_definition(cur, bad)
        cur.execute("DELETE FROM page_configs WHERE id=%s", (f'page-{coll}',)); conn.commit()
    assert any('未启用工作流配置' in x for x in w_bad), w_bad
    assert w_ok == [], w_ok
