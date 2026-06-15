"""v2 并行多路分发回归：一个阶段扇出到多个并发分支，实例在所有分支结束后才完成。直连真实 DB。"""
import psycopg2.extras
from db import get_db
from utils import workflow_repo as repo
from utils.workflow_engine import on_transition

SRC, A, B = 'zzpsrc', 'zzpa', 'zzpb'


def _page(cur, coll, status_field):
    cur.execute("DELETE FROM dynamic_data WHERE collection=%s", (coll,))
    cur.execute("DELETE FROM dynamic_sequences WHERE collection=%s", (coll,))
    cur.execute("DELETE FROM page_configs WHERE id=%s", (f'page-{coll}',))
    cur.execute("INSERT INTO page_configs (id,name,fields) VALUES (%s,%s,%s)",
                (f'page-{coll}', coll, psycopg2.extras.Json(
                    [{'fieldName': 'code', 'controlType': 'autoSequence',
                      'sequenceConfig': {'prefix': 'P-', 'max': 999}, 'isPrimaryKey': True},
                     {'fieldName': status_field, 'controlType': 'select'},
                     {'fieldName': 'title', 'controlType': 'text'}])))


def _setup(cur):
    _page(cur, SRC, 'status')
    _page(cur, A, 'sta')
    _page(cur, B, 'stb')
    wf = {'id': 'wf-par', 'name': 'parallel', 'enabled': True,
          'stages': [
              {'id': 's1', 'name': '受理', 'collection': SRC, 'statusField': 'status',
               'advanceTransition': {'from': '待', 'to': '已'}, 'assignedRoles': ['admin'],
               'spawn': {'fieldMapping': {'title': '$source.title'}}},
              {'id': 's2', 'name': '法务', 'collection': A, 'statusField': 'sta',
               'advanceTransition': {'from': '审', 'to': '过'}},
              {'id': 's3', 'name': '财务', 'collection': B, 'statusField': 'stb',
               'advanceTransition': {'from': '审', 'to': '过'}},
          ],
          'edges': [
              {'id': 'e1', 'source': 's1', 'target': 's2', 'kind': 'advance'},  # 两条无条件边
              {'id': 'e2', 'source': 's1', 'target': 's3', 'kind': 'advance'},  # → 并行扇出
          ]}
    repo.save_definition(cur, wf)
    cur.execute("DELETE FROM workflow_instances WHERE id='inst-par'")
    cur.execute("INSERT INTO dynamic_data (id,collection,data,branch_id) VALUES ('rec-par',%s,%s,'main')",
                (SRC, psycopg2.extras.Json({'status': '待', 'title': '合同'})))
    repo.create_instance(cur, 'inst-par', 'wf-par', 's1', SRC, 'rec-par', 'admin')


def test_parallel_fanout_and_join_completion():
    with get_db() as conn:
        cur = conn.cursor(); _setup(cur); conn.commit()
        # 推进 s1 → 并行扇出到 s2 + s3
        on_transition(cur, SRC, 'rec-par', 'status', '待', '已',
                      {'status': '待', 'title': '合同'}, {'status': '已', 'title': '合同'}, 'admin', 'admin')
        conn.commit()
        inst = repo.get_instance(cur, 'inst-par')
        active1 = {a['stageId'] for a in inst['active_stages']}
        cur.execute("SELECT COUNT(*) FROM dynamic_data WHERE collection=%s", (A,)); ca = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM dynamic_data WHERE collection=%s", (B,)); cb = cur.fetchone()[0]
        # 取两个下游记录 id
        recA = next(a['recordId'] for a in inst['active_stages'] if a['stageId'] == 's2')
        recB = next(a['recordId'] for a in inst['active_stages'] if a['stageId'] == 's3')

        # 先结束 s2 分支 → 实例仍运行（s3 未完）
        on_transition(cur, A, recA, 'sta', '审', '过', {'sta': '审'}, {'sta': '过'}, 'admin', 'admin')
        conn.commit()
        inst2 = repo.get_instance(cur, 'inst-par')

        # 再结束 s3 分支 → 实例完成
        on_transition(cur, B, recB, 'stb', '审', '过', {'stb': '审'}, {'stb': '过'}, 'admin', 'admin')
        conn.commit()
        inst3 = repo.get_instance(cur, 'inst-par')

    assert active1 == {'s2', 's3'}, '推进应并行扇出到 s2 + s3'
    assert ca == 1 and cb == 1, '应各生成一条下游记录'
    assert inst2['status'] == 'running', 's2 结束但 s3 未完，实例应仍运行'
    assert {a['stageId'] for a in inst2['active_stages']} == {'s3'}
    assert inst3['status'] == 'completed', '所有并行分支结束后实例完成'
    assert inst3['active_stages'] == []


def test_conditional_fanout_multiple_matches():
    """多条条件边同时命中 → 多路分发到所有命中分支。"""
    with get_db() as conn:
        cur = conn.cursor(); _setup(cur)
        d = repo.get_definition(cur, 'wf-par')
        # 两条边都加同样命中的条件
        d['edges'] = [
            {'id': 'e1', 'source': 's1', 'target': 's2', 'kind': 'advance',
             'condition': {'field': 'big', 'op': '==', 'value': 'yes'}},
            {'id': 'e2', 'source': 's1', 'target': 's3', 'kind': 'advance',
             'condition': {'field': 'big', 'op': '==', 'value': 'yes'}},
        ]
        repo.save_definition(cur, d)
        cur.execute("UPDATE dynamic_data SET data = data || '{\"big\":\"yes\"}'::jsonb WHERE id='rec-par'")
        conn.commit()
        on_transition(cur, SRC, 'rec-par', 'status', '待', '已',
                      {'status': '待'}, {'status': '已', 'big': 'yes', 'title': '合同'}, 'admin', 'admin')
        conn.commit()
        inst = repo.get_instance(cur, 'inst-par')
    assert {a['stageId'] for a in inst['active_stages']} == {'s2', 's3'}
