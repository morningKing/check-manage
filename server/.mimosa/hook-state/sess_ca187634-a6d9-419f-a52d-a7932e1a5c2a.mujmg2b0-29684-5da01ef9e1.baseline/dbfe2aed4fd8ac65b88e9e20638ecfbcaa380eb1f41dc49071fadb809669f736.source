"""utils/skillopt.py —— SkillOpt 采集/上报/插件安装/保留策略（此前零测试）。"""
import sys, os
from unittest.mock import MagicMock, patch
from contextlib import contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import utils.skillopt as so


def _mock_db(fetchone=None, fetchall=None):
    cur = MagicMock()
    cur.fetchone.return_value = fetchone
    cur.fetchall.return_value = fetchall or []
    conn = MagicMock()
    conn.cursor.return_value.__enter__ = lambda s: cur
    conn.cursor.return_value.__exit__ = lambda s, *a: None
    conn.__enter__ = lambda s: conn
    conn.__exit__ = lambda s, *a: None

    @contextmanager
    def fake():
        yield conn
    return fake, cur, conn


# ── upsert_invocation ────────────────────────────────────────────────────

def test_upsert_maps_status_to_outcome():
    fake, cur, conn = _mock_db()
    with patch('utils.skillopt.get_db', fake):
        iid = so.upsert_invocation('sess_1', 'att_1', 'pdf', status='error')
    assert iid and iid.startswith('inv_')
    sql, params = cur.execute.call_args.args
    assert 'ON CONFLICT (attempt_id, skill_name, skill_hash)' in sql
    assert params[7] == 'failed'                  # status=error → outcome=failed


def test_upsert_running_has_null_outcome_and_hash_normalized():
    fake, cur, _ = _mock_db()
    with patch('utils.skillopt.get_db', fake):
        so.upsert_invocation('sess_1', 'att_1', 'pdf', skill_hash=None,
                             status='running')
    params = cur.execute.call_args.args[1]
    assert params[7] is None                      # running → outcome NULL
    assert params[4] == ''                        # NULL hash 归一 ''（UNIQUE 冲突语义）


def test_upsert_db_failure_returns_none_not_raise():
    def boom(*a, **k):
        raise RuntimeError('db down')
    with patch('utils.skillopt.get_db', boom):
        assert so.upsert_invocation('s', 'a', 'pdf') is None


# ── _platform_session_id ─────────────────────────────────────────────────

def test_platform_id_passthrough():
    assert so._platform_session_id('sess_abc') == 'sess_abc'
    assert so._platform_session_id('') == ''


def test_opencode_id_maps_to_platform_session():
    fake, cur, _ = _mock_db(fetchone=('sess_real',))
    with patch('utils.skillopt.get_db', fake):
        assert so._platform_session_id('ses_oc123') == 'sess_real'


def test_unmappable_opencode_id_returns_empty():
    fake, cur, _ = _mock_db(fetchone=None)
    with patch('utils.skillopt.get_db', fake):
        assert so._platform_session_id('ses_unknown') == ''


# ── record_runtime_skill_event ───────────────────────────────────────────

def test_runtime_event_missing_fields_rejected():
    assert so.record_runtime_skill_event({'sessionID': ''}) == \
        {'ok': False, 'reason': 'missing fields'}


def test_runtime_event_confirms_invocation_and_manifest():
    fake, cur, _ = _mock_db(fetchone=('sess_real',))
    with patch('utils.skillopt.get_db', fake), \
         patch('utils.execution_audit.get_attempts',
               lambda sid, limit=3: [{'id': 'att_9'}]), \
         patch('utils.execution_audit.set_manifest_result') as smr, \
         patch('utils.execution_audit.record_event') as rev:
        out = so.record_runtime_skill_event({
            'skillName': 'pdf', 'sessionID': 'ses_oc1', 'messageID': 'm1',
            'partID': 'p1', 'status': 'completed', 'title': 't'})
    assert out == {'ok': True}
    smr.assert_called_once()
    assert smr.call_args.kwargs['invoked'] == 'confirmed'
    assert smr.call_args.kwargs['runtime_loaded'] == 'confirmed'
    rev.assert_called_once()                      # skill.invoke 事件
    sql = cur.execute.call_args.args[0]
    assert 'ai_skill_invocations' in sql          # confirmed 行落库


# ── mark_session_idle ────────────────────────────────────────────────────

def test_mark_idle_closes_running_invocations():
    fake, cur, _ = _mock_db(fetchone=('sess_real',))
    with patch('utils.skillopt.get_db', fake):
        so.mark_session_idle('ses_oc1')
    sql, params = cur.execute.call_args.args
    assert 'SET outcome=\'completed\'' in sql
    assert params == ('sess_real',)               # 映射后的平台 id


def test_mark_idle_unmappable_noop():
    fake, cur, _ = _mock_db(fetchone=None)
    with patch('utils.skillopt.get_db', fake):
        so.mark_session_idle('ses_x')
    # 映射查询可以发生，但不允许有任何 invocations 收口 UPDATE
    assert not any('ai_skill_invocations' in str(c.args[0])
                   for c in cur.execute.call_args_list)


# ── collect_skill_invocations ────────────────────────────────────────────

def test_collect_skips_runtime_confirmed_skills():
    fake, cur, _ = _mock_db()
    manifests = [{'kind': 'skill', 'name': 'pdf', 'content_hash': 'h1'},
                 {'kind': 'skill', 'name': 'xlsx', 'content_hash': 'h2'},
                 {'kind': 'agent', 'name': 'a', 'content_hash': 'h3'}]
    # runtime 行已确认 pdf：按 skill_name 跳过，只为 xlsx 写 inferred 行
    with patch('utils.skillopt.get_db', fake), \
         patch.object(so, '_existing_invocations',
                      return_value=(set(), {'pdf'})):
        n = so.collect_skill_invocations('att_1', 'sess_1', manifests, [])
    assert n == 1                                 # 只写 xlsx


def test_collect_no_manifests_returns_zero():
    assert so.collect_skill_invocations('a', 's', [], []) == 0


def test_collect_counts_tool_calls_as_evidence():
    fake, cur, _ = _mock_db()
    messages = [{'role': 'assistant', 'content': [
        {'type': 'tool_use'}, {'type': 'tool_use'}]},
        {'role': 'user', 'content': [{'type': 'tool_use'}]}]  # user 不计
    with patch('utils.skillopt.get_db', fake), \
         patch.object(so, '_existing_invocations', return_value=(set(), set())):
        n = so.collect_skill_invocations('att_1', 'sess_1',
                                         [{'kind': 'skill', 'name': 'pdf',
                                           'content_hash': 'h'}], messages)
    assert n == 1
    params = cur.execute.call_args.args[1]
    assert params[9] == 2                         # tool_calls 列


# ── ensure_runtime_plugin ────────────────────────────────────────────────

def test_plugin_written_idempotent(tmp_path):
    gdir = tmp_path / 'global'
    endpoint = 'http://127.0.0.1:3002/ai/memory/internal/runtime-events'
    p1 = so.ensure_runtime_plugin(str(gdir), endpoint, 'tok')
    assert p1 and os.path.isfile(p1)
    body = open(p1, encoding='utf-8').read()
    assert endpoint in body and 'tok' in body     # endpoint/token 内嵌
    assert '__ENDPOINT__' not in body and '__TOKEN__' not in body
    mtime = os.path.getmtime(p1)
    p2 = so.ensure_runtime_plugin(str(gdir), endpoint, 'tok')
    assert p2 == p1 and os.path.getmtime(p2) == mtime  # 幂等不重写


def test_plugin_empty_dir_returns_none():
    assert so.ensure_runtime_plugin('', 'http://x') is None


# ── apply_retention ──────────────────────────────────────────────────────

def test_retention_clears_payload_then_deletes_rows():
    fake, cur, conn = _mock_db()
    cur.rowcount = 7
    with patch('utils.skillopt.get_db', fake), \
         patch.dict(os.environ, {'EXECUTION_AUDIT_EVENT_PLAINTEXT_DAYS': '30',
                                 'EXECUTION_AUDIT_EVENT_ROWS_DAYS': '180'}):
        out = so.apply_retention()
    assert out == {'payload_cleared': 7, 'rows_deleted': 7}
    stmts = [c.args[0] for c in cur.execute.call_args_list]
    assert any("payload = '{}'::jsonb" in s for s in stmts)
    assert any('DELETE FROM ai_execution_events' in s for s in stmts)
