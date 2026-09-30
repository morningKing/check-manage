"""Execution audit unit/integration tests (execution-audit Spec §21).

Covers: attempt lifecycle + prompt snapshot redaction, event seq, workspace
manifest scan, contract frontmatter parsing, step auditor verdicts
(completed_confirmed / completed_claimed / missing / failed / no_contract),
failure classification + recovery, and end-to-end ensure_diagnosis_for_session.
"""

import json
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils import execution_audit, trace_auditor, todo_trace, execution_contract


@pytest.fixture
def user_id(db_conn):
    uid = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, username, password_hash, display_name, role) "
            "VALUES (%s, %s, 'x', %s, 'developer')",
            (uid, f'ea_user_{uid[:8]}', f'EA {uid[:8]}'),
        )
        sid = 'sess_ea_' + uuid.uuid4().hex[:8]
        cur.execute(
            "INSERT INTO ai_chat_sessions (id, user_id, title, status) "
            "VALUES (%s, %s, 'audit-test', 'active')", (sid, uid))
    db_conn.commit()
    yield uid, sid
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_chat_sessions WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM users WHERE id = %s", (uid,))
    db_conn.commit()


def _add_message(db_conn, sid, role, content):
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_chat_messages (id, session_id, role, content) "
            "VALUES (%s, %s, %s, %s)",
            (f'msg_{uuid.uuid4().hex[:10]}', sid, role,
             json.dumps(content, ensure_ascii=False)))
    db_conn.commit()


def _tool(name, status='completed', input=None, result='ok'):
    return {'type': 'tool_use', 'name': name, 'status': status,
            'input': input or {}, 'result': result, 'durationMs': 100}


# ── Attempt + prompt snapshot ────────────────────────────────────────────

def test_attempt_lifecycle_and_prompt_redaction(user_id, db_conn, monkeypatch):
    monkeypatch.setenv('EXECUTION_AUDIT_PROMPT_PLAINTEXT', '0')
    uid, sid = user_id
    attempt_id = execution_audit.create_attempt(
        session_id=sid, source_type='interactive', operation='send',
        requested_agent='data-agent', effective_agent='data-agent',
        agent_resolution='requested',
        effective_model='prov/model-x',
        model_resolution='session_default',
        raw_user_content='hello', effective_prompt='SYSTEM+hello',
        augmentations={'memory_injected': True},
        workspace_path=None,
    )
    assert attempt_id

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT status, effective_prompt_hash, effective_prompt_len "
            "FROM ai_execution_attempts WHERE id=%s", (attempt_id,))
        status, eff_hash, eff_len = cur.fetchone()
        cur.execute(
            "SELECT effective_prompt, effective_prompt_hash, redaction_status, "
            " augmentations FROM ai_execution_prompt_snapshots WHERE attempt_id=%s",
            (attempt_id,))
        plain, snap_hash, redaction, aug = cur.fetchone()
        cur.execute(
            "SELECT agent_resolution, model_resolution FROM ai_execution_attempts "
            "WHERE id=%s", (attempt_id,))
        a_res, m_res = cur.fetchone()

    assert status == 'running'
    import hashlib
    assert eff_hash == hashlib.sha256('SYSTEM+hello'.encode()).hexdigest()
    assert eff_len == len('SYSTEM+hello')
    assert plain is None and redaction == 'redacted'   # plaintext NOT stored
    assert snap_hash == eff_hash
    assert aug['memory_injected'] is True
    assert a_res == 'requested' and m_res == 'session_default'

    execution_audit.record_event(attempt_id, 'tool.state', status='completed',
                                 payload={'tool': 'read'})
    execution_audit.record_event(attempt_id, 'session.idle', status='completed')
    with db_conn.cursor() as cur:
        cur.execute("SELECT event_seq, event_type FROM ai_execution_events "
                    "WHERE attempt_id=%s ORDER BY event_seq", (attempt_id,))
        evs = cur.fetchall()
    assert [e[1] for e in evs] == ['tool.state', 'session.idle']
    assert [e[0] for e in evs] == [1, 2]

    execution_audit.finish_latest_running(sid, 'completed')
    with db_conn.cursor() as cur:
        cur.execute("SELECT status, finished_at FROM ai_execution_attempts "
                    "WHERE id=%s", (attempt_id,))
        status, finished = cur.fetchone()
    assert status == 'completed' and finished is not None


def test_attempt_no_unknown_resolution_kept(user_id, db_conn):
    uid, sid = user_id
    attempt_id = execution_audit.create_attempt(
        session_id=sid, source_type='interactive', operation='send',
        agent_resolution='unknown', model_resolution='unknown',
        raw_user_content='x', effective_prompt='y')
    with db_conn.cursor() as cur:
        cur.execute("SELECT agent_resolution, effective_agent FROM "
                    "ai_execution_attempts WHERE id=%s", (attempt_id,))
        res, eff = cur.fetchone()
    assert res == 'unknown' and eff is None   # no fake "default"


def test_workspace_manifest_scan(tmp_path):
    ws = tmp_path / 'ws'
    (ws / '.opencode' / 'skills' / 'data-import').mkdir(parents=True)
    (ws / '.opencode' / 'skills' / 'data-import' / 'SKILL.md').write_text(
        '---\nname: data-import\n---\nbody', encoding='utf-8')
    (ws / 'AGENTS.md').write_text('guidance', encoding='utf-8')
    rows = execution_audit.scan_workspace_manifests(str(ws))
    kinds = {(r['kind'], r['name']) for r in rows}
    assert ('skill', 'data-import') in kinds
    assert ('guidance', 'AGENTS.md') in kinds
    skill = next(r for r in rows if r['kind'] == 'skill')
    assert skill['injected'] is True and skill['content_hash']


# ── Contract parsing ─────────────────────────────────────────────────────

def test_contract_from_skill_md(tmp_path):
    md = tmp_path / 'SKILL.md'
    md.write_text(
        '---\nname: demo\nexecution_contract:\n'
        '  steps:\n'
        '    - id: read_input\n      required: true\n'
        '      expected_tools: [read, read_upload]\n'
        '    - id: validate_schema\n      required: true\n'
        '      depends_on: [read_input]\n'
        '      expected_tools: [run_python]\n'
        '  forbidden_tools: []\n'
        '---\nbody', encoding='utf-8')
    c = execution_contract.contract_from_skill_md(str(md))
    assert c and [s['id'] for s in c['steps']] == ['read_input', 'validate_schema']
    assert c['steps'][1]['depends_on'] == ['read_input']


def test_contract_roundtrip_db(db_conn):
    contract = {'steps': [{'id': 'a', 'required': True,
                           'expected_tools': ['read']}],
                'forbidden_tools': [], 'success_conditions': []}
    execution_contract.upsert_contract('roundtrip-skill', owner_type='skill',
                                       owner_id='roundtrip-skill',
                                       source='explicit', contract=contract)
    got = execution_contract.get_active_contract('skill', 'roundtrip-skill')
    assert got and got['contract']['steps'][0]['id'] == 'a'
    # upsert deactivates the old version
    execution_contract.upsert_contract('roundtrip-skill', owner_type='skill',
                                       owner_id='roundtrip-skill',
                                       source='explicit', contract=contract)
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ai_execution_contracts "
                    "WHERE owner_id='roundtrip-skill' AND active")
        assert cur.fetchone()[0] == 1


# ── Step auditor verdicts ────────────────────────────────────────────────

def _contract():
    return {
        'steps': [
            {'id': 'read_input', 'required': True, 'expected_tools': ['read']},
            {'id': 'validate_schema', 'required': True,
             'depends_on': ['read_input'], 'expected_tools': ['run_python']},
            {'id': 'save_result', 'required': True,
             'depends_on': ['validate_schema'], 'expected_tools': ['save_artifact']},
        ],
        'forbidden_tools': [], 'success_conditions': [],
    }


def test_auditor_confirmed_missing_claimed(user_id, db_conn):
    uid, sid = user_id
    messages = [
        {'id': 'm1', 'role': 'user', 'content': [{'type': 'text', 'text': 'go'}]},
        {'id': 'm2', 'role': 'assistant', 'content': [
            _tool('read', 'completed', {'file_path': 'a.csv'}, 'col1,col2'),
            # validate_schema: SKIPPED entirely
            # save "result" via write instead of save_artifact → no evidence
            _tool('write', 'completed', {'file_path': 'out.md'}, 'written'),
        ]},
    ]
    todo_plan = todo_trace.build_declared_plan([
        {'id': 't1', 'content': [
            {'type': 'tool_use', 'name': 'todowrite',
             'input': {'todos': [
                 {'id': '1', 'content': 'read_input', 'status': 'completed'},
                 {'id': '2', 'content': 'validate_schema', 'status': 'completed'},
                 {'id': '3', 'content': 'save_result', 'status': 'completed'},
             ]}},
        ]},
    ])
    rows, status = trace_auditor.build_step_results(_contract(), todo_plan,
                                                    trace_auditor.tool_calls_from_messages(messages))
    by_id = {r['step_id']: r for r in rows}
    assert by_id['read_input']['status'] == 'completed_confirmed'
    assert by_id['validate_schema']['status'] == 'completed_claimed'
    assert '没有工具证据' in by_id['validate_schema']['reason']
    assert by_id['save_result']['status'] == 'completed_claimed'
    assert by_id['read_input']['evidence_refs'] == ['message:m2#tool0']


def test_auditor_failed_step_and_no_contract(user_id, db_conn):
    uid, sid = user_id
    messages = [
        {'id': 'm1', 'role': 'assistant', 'content': [
            _tool('read', 'error', {'file_path': 'missing.csv'}, '404 not found'),
        ]},
    ]
    rows, status = trace_auditor.build_step_results(
        _contract(), todo_trace.build_declared_plan([]),
        trace_auditor.tool_calls_from_messages(messages))
    by_id = {r['step_id']: r for r in rows}
    assert by_id['read_input']['status'] == 'failed'
    assert by_id['validate_schema']['status'] == 'missing'
    assert by_id['validate_schema']['reason'].startswith('契约必需步骤')

    rows2, status2 = trace_auditor.build_step_results(None, {}, [])
    assert rows2 == [] and status2 == 'no_contract'


def test_auditor_out_of_order(user_id, db_conn):
    uid, sid = user_id
    messages = [
        {'id': 'm1', 'role': 'assistant', 'content': [
            _tool('run_python', 'completed', {'code': 'x'}, 'ok'),
            _tool('read', 'completed', {'file_path': 'a.csv'}, 'data'),
            _tool('save_artifact', 'completed', {'path': 'o.xlsx'}, 'saved'),
        ]},
    ]
    rows, status = trace_auditor.build_step_results(
        _contract(), todo_trace.build_declared_plan([]),
        trace_auditor.tool_calls_from_messages(messages))
    by_id = {r['step_id']: r for r in rows}
    assert by_id['read_input']['status'] == 'completed_confirmed'
    assert by_id['validate_schema']['status'] == 'out_of_order'
    assert by_id['save_result']['status'] == 'completed_confirmed'


# ── Failure classification + recovery ────────────────────────────────────

def test_failure_classification_and_recovery(user_id, db_conn):
    uid, sid = user_id
    messages = [
        {'id': 'm1', 'role': 'assistant', 'content': [
            _tool('query_collection', 'error', {'collection': 'orders'},
                  '404 not found: collection orders'),
            _tool('query_collection', 'error', {'collection': 'orders'},
                  '404 not found: collection orders'),
            _tool('run_python', 'completed', {'code': 'df'}, 'ok'),
        ]},
    ]
    calls = trace_auditor.tool_calls_from_messages(messages)
    f = trace_auditor.classify_failure(calls[0])
    assert f['failure_type'] == 'resource_not_found'
    assert f['evidence_refs'] == ['message:m1#tool0']
    trace_auditor._recovery_for(f, calls, 0)
    assert f['recovery']['same_input_retry'] is True
    assert f['recovery']['recovered'] is False

    timeout_call = _tool('run_python', 'error', {'code': 'x'}, 'killed')
    timeout_call['durationMs'] = 45000
    f2 = trace_auditor.classify_failure(timeout_call)
    assert f2['failure_type'] == 'timeout'


# ── End-to-end diagnosis build ───────────────────────────────────────────

def test_ensure_diagnosis_for_session(user_id, db_conn):
    uid, sid = user_id
    skill_name = f'demo-skill-{uuid.uuid4().hex[:6]}'  # 避免与其他测试注册的契约耦合
    attempt_id = execution_audit.create_attempt(
        session_id=sid, source_type='interactive', operation='send',
        effective_model='prov/m', model_resolution='session_default',
        raw_user_content='q', effective_prompt='aug q',
        workspace_path=None)
    execution_audit.save_manifests(attempt_id, [
        {'kind': 'skill', 'name': skill_name, 'source': 'session',
         'path': None, 'content_hash': 'deadbeef', 'injected': True},
    ])
    execution_audit.finish_latest_running(sid, 'completed')
    _add_message(db_conn, sid, 'user', [{'type': 'text', 'text': 'q'}])
    _add_message(db_conn, sid, 'assistant', [
        {'type': 'tool_use', 'name': 'read', 'status': 'completed',
         'input': {'file_path': 'a.csv'}, 'result': 'data', 'durationMs': 100},
        {'type': 'text', 'text': 'done'},
    ])

    report = trace_auditor.ensure_diagnosis_for_session(sid)
    assert report['execution']['attempt_id'] == attempt_id
    assert report['execution']['model']['effective'] == 'prov/m'
    assert report['contract']['status'] == 'unknown_due_to_missing_data'
    assert report['tool_failures'] == []
    assert 0 < report['data_completeness']['score'] <= 1
    assert report['status'] in ('completed', 'partial')

    with db_conn.cursor() as cur:
        cur.execute("SELECT status, report FROM ai_execution_diagnoses "
                    "WHERE attempt_id=%s", (attempt_id,))
        st, rep = cur.fetchone()
    assert st in ('completed', 'partial')
    assert rep['execution']['attempt_id'] == attempt_id


def test_ensure_diagnosis_no_attempt(user_id, db_conn):
    uid, sid = user_id
    report = trace_auditor.ensure_diagnosis_for_session(sid)
    assert report['status'] == 'no_attempt'
