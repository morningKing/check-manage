import psycopg2.extras
from db import get_db
from utils import workflow_repo as repo


def _clean(cur):
    cur.execute("DELETE FROM workflow_instances WHERE id LIKE 'wf-t-%'")
    cur.execute("DELETE FROM workflow_definitions WHERE id LIKE 'wf-t-%'")


def test_definition_crud():
    with get_db() as conn:
        cur = conn.cursor(); _clean(cur); conn.commit()
        repo.save_definition(cur, {'id': 'wf-t-1', 'name': '需求流', 'enabled': True,
                                   'stages': [{'id': 's1', 'name': '评审', 'collection': 'req'}]})
        conn.commit()
        d = repo.get_definition(cur, 'wf-t-1')
        assert d['name'] == '需求流' and d['stages'][0]['id'] == 's1'
        assert any(x['id'] == 'wf-t-1' for x in repo.list_definitions(cur))
        cur.execute("DELETE FROM workflow_definitions WHERE id='wf-t-1'"); conn.commit()


def test_instance_lifecycle():
    with get_db() as conn:
        cur = conn.cursor(); _clean(cur)
        inst = repo.create_instance(cur, 'wf-t-inst', 'wf-t-1', 's1', 'req', 'r1', 'admin')
        conn.commit()
        got = repo.get_instance(cur, 'wf-t-inst')
        assert got['status'] == 'running' and got['current_stage_id'] == 's1'
        assert got['chain'][0]['recordId'] == 'r1'
        found = repo.find_running_instance_by_record(cur, 'req', 'r1')
        assert found['id'] == 'wf-t-inst'
        _clean(cur); conn.commit()
