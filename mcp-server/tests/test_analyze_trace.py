"""Unit tests for tools.analyze_trace and tools.query_sessions (mocked DB).

Tests cover:
  - analyze_trace: session lookup, tool call extraction, subtask tree,
    performance aggregation, source resolution, error cases
  - query_sessions: multi-filter query, source type inference, truncation
  - combined flow: query_sessions → analyze_trace (Skill workflow)
"""

import json
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest


def _ctx(role="developer"):
    from context import ToolContext
    return ToolContext(session_id="s1", user_id="u1", role=role)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers: build mock cursor return values
# ─────────────────────────────────────────────────────────────────────────────

def _session_row(
    sid="sess_abc",
    status="failed",
    error_message="JSON parse error",
    batch_id=None,
    scan_task_id=None,
    source_record_id=None,
    agent="build",
    model="qwen-plus",
    batch_name=None,
    batch_agent=None,
    scan_task_name=None,
):
    """Return a tuple matching the analyze_trace SELECT columns."""
    created = datetime(2026, 9, 2, 10, 0, 0, tzinfo=timezone.utc)
    last_active = datetime(2026, 9, 2, 10, 5, 30, tzinfo=timezone.utc)
    return (
        sid, "user1", "Test Session", status, error_message,      # 0-4
        batch_id, scan_task_id, source_record_id,                 # 5-7
        agent, model, created, last_active,                       # 8-11
        batch_name, batch_agent, scan_task_name,                  # 12-14
    )


def _msg_row(msg_id, role, content, meta=None, created=None):
    """Return a tuple matching the analyze_trace message SELECT."""
    if created is None:
        created = datetime(2026, 9, 2, 10, 0, 10, tzinfo=timezone.utc)
    return (msg_id, role, content, meta, created)


def _subtask_row(
    st_id="ses_child_001",
    agent="explore",
    description="Analyze data",
    status="completed",
    error_message=None,
    prompt="Analyze the CSV structure",
    created=None,
    completed=None,
):
    if created is None:
        created = datetime(2026, 9, 2, 10, 0, 15, tzinfo=timezone.utc)
    if completed is None:
        completed = datetime(2026, 9, 2, 10, 1, 0, tzinfo=timezone.utc)
    return (st_id, agent, description, status, error_message, prompt, created, completed)


def _qs_row(
    sid="sess_001",
    status="completed",
    agent="build",
    error_message=None,
    scan_task_id=None,
    batch_id=None,
    api_key_id=None,
    batch_agent=None,
    batch_name=None,
    msg_count=5,
    total_duration=12000,
    total_tokens=5000,
    total_cost=0.015,
):
    """Return a tuple matching the query_sessions SELECT columns."""
    created = datetime(2026, 9, 1, 15, 0, 0, tzinfo=timezone.utc)
    last_active = datetime(2026, 9, 1, 15, 2, 0, tzinfo=timezone.utc)
    return (
        sid, status, agent, error_message,              # 0-3
        created, last_active, "Last message preview",   # 4-6
        scan_task_id, batch_id, api_key_id,             # 7-9
        batch_agent, batch_name,                        # 10-11
        msg_count, total_duration, total_tokens, total_cost,  # 12-15
    )


# ═════════════════════════════════════════════════════════════════════════════
# analyze_trace tests
# ═════════════════════════════════════════════════════════════════════════════

class TestAnalyzeTrace:

    def _call(self, fake_db, mock_cursor, **kwargs):
        from tools.analyze_trace import handle
        with patch("tools.analyze_trace.get_db", fake_db):
            raw = handle({"session_id": "sess_abc", **kwargs}, _ctx())
        return json.loads(raw)

    # ── basic session metadata ───────────────────────────────────────────

    def test_returns_session_metadata(self, fake_db, mock_cursor):
        """Session fields are correctly mapped."""
        mock_cursor.fetchone.return_value = _session_row()
        mock_cursor.fetchall.return_value = []  # messages, subtasks, files

        result = self._call(fake_db, mock_cursor)

        assert result["session"]["id"] == "sess_abc"
        assert result["session"]["status"] == "failed"
        assert result["session"]["agent"] == "build"
        assert result["session"]["model"] == "qwen-plus"
        assert result["session"]["error_message"] == "JSON parse error"

    def test_nonexistent_session_raises(self, fake_db, mock_cursor):
        """Raises AnalyzeTraceError when session not found."""
        mock_cursor.fetchone.return_value = None

        from tools.analyze_trace import AnalyzeTraceError
        with patch("tools.analyze_trace.get_db", fake_db):
            with pytest.raises(AnalyzeTraceError, match="不存在"):
                from tools.analyze_trace import handle
                handle({"session_id": "sess_nonexist"}, _ctx())

    def test_missing_session_id_raises(self, fake_db, mock_cursor):
        from tools.analyze_trace import AnalyzeTraceError
        with pytest.raises(AnalyzeTraceError, match="required"):
            from tools.analyze_trace import handle
            handle({}, _ctx())

    # ── source resolution ────────────────────────────────────────────────

    def test_source_scan(self, fake_db, mock_cursor):
        mock_cursor.fetchone.return_value = _session_row(
            scan_task_id="task_001", source_record_id="rec_xyz", scan_task_name="Order Audit"
        )
        mock_cursor.fetchall.return_value = []

        result = self._call(fake_db, mock_cursor)

        assert result["source"]["type"] == "scan"
        assert result["source"]["id"] == "task_001"
        assert result["source"]["name"] == "Order Audit"
        assert result["source"]["record_id"] == "rec_xyz"

    def test_source_batch(self, fake_db, mock_cursor):
        mock_cursor.fetchone.return_value = _session_row(
            batch_id="batch_001", batch_name="Import Batch"
        )
        mock_cursor.fetchall.return_value = []

        result = self._call(fake_db, mock_cursor)

        assert result["source"]["type"] == "batch"
        assert result["source"]["id"] == "batch_001"
        assert result["source"]["name"] == "Import Batch"

    def test_source_interactive(self, fake_db, mock_cursor):
        mock_cursor.fetchone.return_value = _session_row()
        mock_cursor.fetchall.return_value = []

        result = self._call(fake_db, mock_cursor)

        assert result["source"]["type"] == "interactive"

    # ── tool call extraction ─────────────────────────────────────────────

    def test_extracts_tool_calls(self, fake_db, mock_cursor):
        mock_cursor.fetchone.return_value = _session_row()
        messages = [
            _msg_row("msg1", "user", [{"type": "text", "text": "Please process data"}]),
            _msg_row("msg2", "assistant", [
                {"type": "text", "text": "I'll read the file first"},
                {
                    "type": "tool_use",
                    "name": "read",
                    "input": {"file_path": "/data.csv"},
                    "result": "col1,col2\n1,2",
                    "status": "completed",
                    "durationMs": 500,
                },
                {
                    "type": "tool_use",
                    "name": "query_collection",
                    "input": {"collection": "orders", "filter": {"status": "open"}},
                    "result": {"data": [{"id": 1}], "total": 1},
                    "status": "completed",
                    "durationMs": 2000,
                },
            ], {"durationMs": 2500, "tokensInput": 3000, "tokensOutput": 500, "cost": 0.001}),
        ]
        mock_cursor.fetchall.side_effect = [messages, [], []]  # msgs, subtasks, files

        result = self._call(fake_db, mock_cursor)

        assert len(result["tool_calls"]) == 2
        assert result["tool_calls"][0]["name"] == "read"
        assert result["tool_calls"][0]["status"] == "completed"
        assert result["tool_calls"][0]["duration_ms"] == 500
        assert result["tool_calls"][1]["name"] == "query_collection"
        assert result["performance"]["tool_call_count"] == 2
        assert result["performance"]["total_duration_ms"] == 2500
        assert result["performance"]["total_cost"] == 0.001

    def test_extracts_reasoning_when_enabled(self, fake_db, mock_cursor):
        mock_cursor.fetchone.return_value = _session_row()
        messages = [
            _msg_row("msg1", "assistant", [
                {"type": "reasoning", "text": "I need to check the data structure first..."},
                {"type": "text", "text": "Let me analyze"},
            ], {"durationMs": 1000, "tokensInput": 1000, "tokensOutput": 200, "cost": 0.0005}),
        ]
        mock_cursor.fetchall.side_effect = [messages, [], []]

        result = self._call(fake_db, mock_cursor, include_reasoning=True)

        reasoning_calls = [tc for tc in result["tool_calls"] if tc["name"] == "_reasoning"]
        assert len(reasoning_calls) == 1
        assert "check the data" in reasoning_calls[0]["result"]

    def test_skips_reasoning_by_default(self, fake_db, mock_cursor):
        mock_cursor.fetchone.return_value = _session_row()
        messages = [
            _msg_row("msg1", "assistant", [
                {"type": "reasoning", "text": "Internal thinking..."},
                {"type": "text", "text": "Result"},
            ], {"durationMs": 500, "tokensInput": 500, "tokensOutput": 100, "cost": 0.0002}),
        ]
        mock_cursor.fetchall.side_effect = [messages, [], []]

        result = self._call(fake_db, mock_cursor)

        reasoning_calls = [tc for tc in result["tool_calls"] if tc["name"] == "_reasoning"]
        assert len(reasoning_calls) == 0

    # ── performance aggregation ──────────────────────────────────────────

    def test_aggregates_performance_metrics(self, fake_db, mock_cursor):
        mock_cursor.fetchone.return_value = _session_row()
        messages = [
            _msg_row("m1", "user", [{"type": "text", "text": "hello"}]),
            _msg_row("m2", "assistant", [{"type": "text", "text": "hi"}],
                     {"durationMs": 1000, "tokensInput": 500, "tokensOutput": 100, "cost": 0.001}),
            _msg_row("m3", "user", [{"type": "text", "text": "do task"}]),
            _msg_row("m4", "assistant", [{"type": "text", "text": "done"}],
                     {"durationMs": 2000, "tokensInput": 800, "tokensOutput": 200, "cost": 0.002}),
        ]
        mock_cursor.fetchall.side_effect = [messages, [], []]

        result = self._call(fake_db, mock_cursor)

        perf = result["performance"]
        assert perf["total_duration_ms"] == 3000
        assert perf["total_tokens_input"] == 1300
        assert perf["total_tokens_output"] == 300
        assert perf["total_cost"] == 0.003
        assert perf["message_count"] == 4
        assert perf["tool_call_count"] == 0

    # ── subtask extraction ───────────────────────────────────────────────

    def test_extracts_subtasks(self, fake_db, mock_cursor):
        mock_cursor.fetchone.return_value = _session_row()
        messages = [
            _msg_row("m1", "assistant", [
                {"type": "text", "text": "Delegating to explore agent"},
                {"type": "subtask_use", "subtaskId": "ses_child_001", "agent": "explore",
                 "description": "Analyze data", "status": "completed"},
            ], {"durationMs": 5000, "tokensInput": 2000, "tokensOutput": 500, "cost": 0.003}),
        ]
        subtask = _subtask_row()
        # fetchall: messages, subtasks, files
        mock_cursor.fetchall.side_effect = [messages, [subtask], []]
        # fetchone: session row, then subtask stats
        mock_cursor.fetchone.side_effect = [_session_row(), (3, 45000, 8000)]

        result = self._call(fake_db, mock_cursor)

        assert len(result["subtasks"]) == 1
        st = result["subtasks"][0]
        assert st["id"] == "ses_child_001"
        assert st["agent"] == "explore"
        assert st["status"] == "completed"
        assert st["message_count"] == 3
        assert st["duration_ms"] == 45000
        assert st["tokens_total"] == 8000

    def test_skips_subtasks_when_disabled(self, fake_db, mock_cursor):
        mock_cursor.fetchone.return_value = _session_row()
        mock_cursor.fetchall.side_effect = [[], []]  # messages, files (no subtasks query)

        result = self._call(fake_db, mock_cursor, include_subtasks=False)

        assert result["subtasks"] == []

    # ── file changes ─────────────────────────────────────────────────────

    def test_returns_file_changes(self, fake_db, mock_cursor):
        mock_cursor.fetchone.return_value = _session_row()
        files = [("outputs/result.json", "added"), ("uploads/data.csv", "modified")]
        mock_cursor.fetchall.side_effect = [[], [], files]  # msgs, subtasks, files

        result = self._call(fake_db, mock_cursor)

        assert len(result["files_changed"]) == 2
        assert result["files_changed"][0]["path"] == "outputs/result.json"
        assert result["files_changed"][0]["status"] == "added"

    # ── messages summary ─────────────────────────────────────────────────

    def test_messages_summary(self, fake_db, mock_cursor):
        mock_cursor.fetchone.return_value = _session_row()
        messages = [
            _msg_row("m1", "user", [{"type": "text", "text": "Process the CSV file please"}]),
            _msg_row("m2", "assistant", [
                {"type": "text", "text": "I'll read the file and process it"},
                {"type": "tool_use", "name": "read", "input": {}, "status": "completed"},
            ]),
        ]
        mock_cursor.fetchall.side_effect = [messages, [], []]

        result = self._call(fake_db, mock_cursor)

        summary = result["messages_summary"]
        assert len(summary) == 2
        assert summary[0]["role"] == "user"
        assert "Process the CSV" in summary[0]["text_preview"]
        assert summary[1]["role"] == "assistant"
        assert summary[1]["tool_calls"] == ["read"]

    # ── input truncation ─────────────────────────────────────────────────

    def test_truncates_long_input(self, fake_db, mock_cursor):
        mock_cursor.fetchone.return_value = _session_row()
        long_input = "x" * 1000
        messages = [
            _msg_row("m1", "assistant", [
                {"type": "tool_use", "name": "run_python",
                 "input": {"code": long_input}, "status": "completed", "durationMs": 1000},
            ]),
        ]
        mock_cursor.fetchall.side_effect = [messages, [], []]

        result = self._call(fake_db, mock_cursor)

        assert len(result["tool_calls"][0]["input"]) < 600  # truncated + "..."


# ═════════════════════════════════════════════════════════════════════════════
# query_sessions tests
# ═════════════════════════════════════════════════════════════════════════════

class TestQuerySessions:

    def _call(self, fake_db, mock_cursor, **kwargs):
        from tools.query_sessions import handle
        with patch("tools.query_sessions.get_db", fake_db):
            raw = handle(kwargs, _ctx())
        return json.loads(raw)

    # ── basic query ──────────────────────────────────────────────────────

    def test_returns_sessions(self, fake_db, mock_cursor):
        mock_cursor.fetchall.return_value = [
            _qs_row(sid="s1", status="completed"),
            _qs_row(sid="s2", status="failed", error_message="timeout"),
        ]

        result = self._call(fake_db, mock_cursor)

        assert result["total"] == 2
        assert result["sessions"][0]["id"] == "s1"
        assert result["sessions"][0]["status"] == "completed"
        assert result["sessions"][1]["id"] == "s2"
        assert result["sessions"][1]["error_message"] == "timeout"

    def test_empty_result(self, fake_db, mock_cursor):
        mock_cursor.fetchall.return_value = []

        result = self._call(fake_db, mock_cursor)

        assert result["total"] == 0
        assert result["sessions"] == []

    # ── source type inference ────────────────────────────────────────────

    def test_infers_source_type_scan(self, fake_db, mock_cursor):
        mock_cursor.fetchall.return_value = [
            _qs_row(scan_task_id="task_001"),
        ]

        result = self._call(fake_db, mock_cursor)

        assert result["sessions"][0]["source_type"] == "scan"

    def test_infers_source_type_batch(self, fake_db, mock_cursor):
        mock_cursor.fetchall.return_value = [
            _qs_row(batch_id="batch_001"),
        ]

        result = self._call(fake_db, mock_cursor)

        assert result["sessions"][0]["source_type"] == "batch"

    def test_infers_source_type_open_api(self, fake_db, mock_cursor):
        mock_cursor.fetchall.return_value = [
            _qs_row(api_key_id="key_001"),
        ]

        result = self._call(fake_db, mock_cursor)

        assert result["sessions"][0]["source_type"] == "open_api"

    def test_infers_source_type_interactive(self, fake_db, mock_cursor):
        mock_cursor.fetchall.return_value = [
            _qs_row(),  # no batch/scan/api_key
        ]

        result = self._call(fake_db, mock_cursor)

        assert result["sessions"][0]["source_type"] == "interactive"

    # ── agent fallback ───────────────────────────────────────────────────

    def test_agent_falls_back_to_batch_agent(self, fake_db, mock_cursor):
        mock_cursor.fetchall.return_value = [
            _qs_row(agent=None, batch_id="b1", batch_agent="explore"),
        ]

        result = self._call(fake_db, mock_cursor)

        assert result["sessions"][0]["agent"] == "explore"

    # ── performance fields ───────────────────────────────────────────────

    def test_performance_fields(self, fake_db, mock_cursor):
        mock_cursor.fetchall.return_value = [
            _qs_row(msg_count=8, total_duration=45000, total_tokens=12000, total_cost=0.05),
        ]

        result = self._call(fake_db, mock_cursor)

        s = result["sessions"][0]
        assert s["duration_ms"] == 45000
        assert s["tokens_total"] == 12000
        assert s["cost"] == 0.05
        assert s["message_count"] == 8

    # ── truncation ───────────────────────────────────────────────────────

    def test_error_message_truncated(self, fake_db, mock_cursor):
        long_error = "E" * 500
        mock_cursor.fetchall.return_value = [
            _qs_row(error_message=long_error),
        ]

        result = self._call(fake_db, mock_cursor)

        assert len(result["sessions"][0]["error_message"]) <= 203  # 200 + "..."

    # ── filter with status parameter ─────────────────────────────────────

    def test_status_filter_passed_to_query(self, fake_db, mock_cursor):
        mock_cursor.fetchall.return_value = []

        self._call(fake_db, mock_cursor, status="failed")

        # Verify the SQL was called with the status parameter
        call_args = mock_cursor.execute.call_args
        params = call_args[0][1]
        assert "failed" in params

    # ── datetime serialization ───────────────────────────────────────────

    def test_datetime_serialized_to_iso(self, fake_db, mock_cursor):
        mock_cursor.fetchall.return_value = [
            _qs_row(),
        ]

        result = self._call(fake_db, mock_cursor)

        s = result["sessions"][0]
        assert s["created_at"] == "2026-09-01T15:00:00+00:00"
        assert s["last_active_at"] == "2026-09-01T15:02:00+00:00"


# ═════════════════════════════════════════════════════════════════════════════
# Integration: analyze_trace + query_sessions combined flow
# ═════════════════════════════════════════════════════════════════════════════

class TestCombinedFlow:
    """Simulate the trace-analyzer Skill workflow: query_sessions → analyze_trace."""

    def test_find_failed_then_analyze(self, fake_db, mock_cursor):
        """Step 1: query_sessions finds failed sessions.
        Step 2: analyze_trace gets details of the first one."""

        from tools.query_sessions import handle as qs_handle
        from tools.analyze_trace import handle as at_handle

        # ── Step 1: query_sessions ───────────────────────────────────────
        mock_cursor.fetchall.return_value = [
            _qs_row(
                sid="sess_fail_1", status="failed", agent="build",
                error_message="timeout", scan_task_id="task_001",
                msg_count=3, total_duration=30000, total_tokens=8000, total_cost=0.01,
            ),
        ]

        with patch("tools.query_sessions.get_db", fake_db):
            qs_result = json.loads(qs_handle({"source_type": "scan", "status": "failed"}, _ctx()))

        assert qs_result["total"] == 1
        assert qs_result["sessions"][0]["id"] == "sess_fail_1"
        assert qs_result["sessions"][0]["source_type"] == "scan"

        # ── Step 2: analyze_trace ────────────────────────────────────────
        session_data = _session_row(
            sid="sess_fail_1", status="failed", error_message="timeout",
            scan_task_id="task_001", scan_task_name="Order Audit",
        )
        messages = [
            _msg_row("m1", "user", [{"type": "text", "text": "Process orders"}]),
            _msg_row("m2", "assistant", [
                {"type": "text", "text": "Starting"},
                {"type": "tool_use", "name": "run_python",
                 "input": {"code": "import time; time.sleep(60)"},
                 "status": "error", "durationMs": 30000},
            ], {"durationMs": 30000, "tokensInput": 2000, "tokensOutput": 300, "cost": 0.005}),
        ]

        mock_cursor.fetchone.return_value = session_data
        mock_cursor.fetchall.side_effect = [messages, [], []]  # msgs, subtasks, files

        with patch("tools.analyze_trace.get_db", fake_db):
            at_result = json.loads(at_handle({"session_id": "sess_fail_1"}, _ctx()))

        assert at_result["session"]["status"] == "failed"
        assert at_result["source"]["type"] == "scan"
        assert at_result["source"]["name"] == "Order Audit"
        assert len(at_result["tool_calls"]) == 1
        assert at_result["tool_calls"][0]["name"] == "run_python"
        assert at_result["tool_calls"][0]["status"] == "error"
        assert at_result["tool_calls"][0]["duration_ms"] == 30000
        assert at_result["performance"]["total_cost"] == 0.005
