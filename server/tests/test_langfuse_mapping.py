from datetime import datetime, timezone
import re

import pytest

from utils.langfuse_mapping import (
    Observation,
    map_open_code_message,
    map_subtask_tree,
    map_tool_part,
)
from utils.langfuse_config import observation_id, trace_id_for


CONTEXT = {
    "trace_id": trace_id_for("session-root", "batch-1", "initial"),
    "session_id": "session-root",
    "batch_id": "batch-1",
    "model": "claude-test",
    "agent": "general",
}


def test_text_generation_maps_content_and_metadata():
    message = {
        "info": {
            "id": "msg-1",
            "role": "assistant",
            "modelID": "claude-test",
            "time": {"created": 1_700_000_000_000, "completed": 1_700_000_000_250},
            "finish": "stop",
        },
        "parts": [{"id": "part-1", "type": "text", "text": "hello"}],
    }

    observations = map_open_code_message(message, CONTEXT)

    assert observations == [
        Observation(
            kind="generation",
            id=observation_id("generation", "msg-1:part-1"),
            parent_id=None,
            trace_id=trace_id_for("session-root", "batch-1", "initial"),
            session_id="session-root",
            name="generation",
            input=None,
            output="hello",
            metadata={
                "message_id": "msg-1",
                "part_id": "part-1",
                "model": "claude-test",
                "agent": "general",
                "batch_id": "batch-1",
            },
            status="completed",
            start_time=datetime.fromtimestamp(1_700_000_000, tz=timezone.utc),
            end_time=datetime.fromtimestamp(1_700_000_000.25, tz=timezone.utc),
        )
    ]


def test_mapping_keeps_w3c_ids_and_shared_trace_link():
    observations = map_open_code_message(
        {
            "info": {"id": "msg-format", "role": "assistant"},
            "parts": [{"id": "part-format", "type": "text", "text": "ok"}],
        },
        CONTEXT,
    )

    assert re.fullmatch(r"[0-9a-f]{32}", observations[0].trace_id)
    assert re.fullmatch(r"[0-9a-f]{16}", observations[0].id)
    assert observations[0].trace_id == CONTEXT["trace_id"]


def test_tool_part_maps_mcp_and_failed_tool_with_stable_name():
    part = {
        "id": "tool-1",
        "type": "tool",
        "tool": "mcp__files__read",
        "time": {"start": 1_700_000_000_000, "end": 1_700_000_000_300},
        "state": {
            "status": "error",
            "input": {"path": "README.md"},
            "error": "permission denied",
        },
    }

    observation = map_tool_part(part, CONTEXT)

    assert observation.kind == "span"
    assert observation.name == "execute_tool"
    assert observation.status == "failed"
    assert observation.input == {"path": "README.md"}
    assert observation.output == "permission denied"
    assert observation.metadata["tool"] == "mcp__files__read"
    assert observation.metadata["part_id"] == "tool-1"
    assert observation.start_time == datetime.fromtimestamp(1_700_000_000, tz=timezone.utc)
    assert observation.end_time == datetime.fromtimestamp(1_700_000_000.3, tz=timezone.utc)


def test_task_tool_uses_real_child_session_id_and_invoke_agent_name():
    part = {
        "id": "task-part",
        "type": "tool",
        "tool": "task",
        "sessionID": "parent-session",
        "state": {
            "status": "completed",
            "input": {"subagent_type": "review", "description": "check it"},
            "metadata": {"sessionId": "child-session"},
        },
    }

    observation = map_tool_part(part, CONTEXT)

    assert observation.name == "invoke_agent"
    assert observation.session_id == "child-session"
    assert observation.metadata["task_id"] == "child-session"
    assert observation.metadata["parent_session_id"] == "parent-session"
    assert observation.metadata["agent"] == "review"


def test_missing_timestamps_are_safe_and_message_errors_fail_generation():
    message = {
        "info": {
            "id": "msg-error",
            "role": "assistant",
            "error": {"name": "ProviderAuthError", "data": {"message": "no key"}},
        },
        "parts": [{"type": "text", "text": "partial"}],
    }

    observation = map_open_code_message(message, CONTEXT)[0]

    assert observation.status == "failed"
    assert observation.start_time is None
    assert observation.end_time is None
    assert observation.output == "partial"


def test_nested_subtasks_have_parent_links_and_duplicate_snapshots_are_removed():
    subtasks = [
        {
            "id": "child-session",
            "parent_id": None,
            "agent": "build",
            "description": "build it",
            "model": "child-model",
            "messages": [
                {
                    "info": {"id": "child-msg", "role": "assistant", "finish": "stop"},
                    "parts": [{"id": "child-text", "type": "text", "text": "done"}],
                }
            ],
            "subtasks": [
                {
                    "id": "grandchild-session",
                    "parent_id": "child-session",
                    "agent": "review",
                    "messages": [
                        {
                            "info": {"id": "grand-msg", "role": "assistant"},
                            "parts": [{"id": "grand-text", "type": "text", "text": "reviewed"}],
                        }
                    ],
                }
            ],
        },
        {
            "id": "child-session",
            "parent_id": None,
            "messages": [
                {
                    "info": {"id": "child-msg", "role": "assistant", "finish": "stop"},
                    "parts": [{"id": "child-text", "type": "text", "text": "done"}],
                }
            ],
        },
    ]

    observations = map_subtask_tree(subtasks, {}, CONTEXT)

    assert [item.name for item in observations] == [
        "invoke_agent",
        "generation",
        "invoke_agent",
        "generation",
    ]
    child_invoke, child_generation, grand_invoke, grand_generation = observations
    assert child_invoke.parent_id is None
    assert child_invoke.metadata["task_id"] == "child-session"
    assert child_generation.parent_id == child_invoke.id
    assert grand_invoke.parent_id == child_invoke.id
    assert grand_generation.parent_id == grand_invoke.id
    assert len({item.id for item in observations}) == 4


def test_message_mapping_ignores_user_messages_and_duplicate_parts():
    message = {
        "info": {"id": "msg-2", "role": "user"},
        "parts": [
            {"id": "same", "type": "text", "text": "prompt"},
            {"id": "same", "type": "text", "text": "prompt"},
        ],
    }

    assert map_open_code_message(message, CONTEXT) == []


def test_explicit_empty_child_messages_do_not_fall_back_to_shared_messages():
    shared_messages = [
        {
            "info": {"id": "parent-msg", "role": "assistant"},
            "parts": [{"id": "parent-text", "type": "text", "text": "parent"}],
        }
    ]
    observations = map_subtask_tree(
        [{"id": "child-empty", "messages": []}], shared_messages, CONTEXT
    )

    assert [item.name for item in observations] == ["invoke_agent"]


def test_absent_child_messages_still_use_shared_messages():
    shared_messages = [
        {
            "info": {"id": "shared-msg", "role": "assistant"},
            "parts": [{"id": "shared-text", "type": "text", "text": "shared"}],
        }
    ]
    observations = map_subtask_tree([{"id": "child-absent"}], shared_messages, CONTEXT)

    assert [item.name for item in observations] == ["invoke_agent", "generation"]


def test_duplicate_snapshots_upgrade_status_and_keep_richer_output():
    snapshots = [
        {
            "id": "child-progress",
            "status": "pending",
            "messages": [
                {
                    "info": {"id": "same-msg", "role": "assistant"},
                    "parts": [{"id": "same-part", "type": "text", "text": "short"}],
                }
            ],
        },
        {
            "id": "child-progress",
            "status": "completed",
            "messages": [
                {
                    "info": {"id": "same-msg", "role": "assistant", "finish": "stop"},
                    "parts": [
                        {"id": "same-part", "type": "text", "text": "richer output"}
                    ],
                }
            ],
        },
    ]

    observations = map_subtask_tree(snapshots, {}, CONTEXT)

    assert len(observations) == 2
    assert observations[0].status == "completed"
    assert observations[1].status == "completed"
    assert observations[1].output == "richer output"


def test_equal_status_duplicate_keeps_richer_output_and_terminal_status():
    snapshots = [
        {
            "id": "same-child",
            "status": "completed",
            "messages": [
                {
                    "info": {"id": "same-message", "role": "assistant", "finish": "stop"},
                    "parts": [{"id": "same-text", "type": "text", "text": "long answer"}],
                }
            ],
        },
        {
            "id": "same-child",
            "status": "completed",
            "messages": [
                {
                    "info": {"id": "same-message", "role": "assistant", "finish": "stop"},
                    "parts": [{"id": "same-text", "type": "text", "text": "short"}],
                }
            ],
        },
    ]

    observations = map_subtask_tree(snapshots, {}, CONTEXT)

    assert observations[0].status == "completed"
    assert observations[1].status == "completed"
    assert observations[1].output == "long answer"


def test_idless_tool_parts_get_distinct_contextual_ids():
    message = {
        "info": {"id": "tool-message", "role": "assistant"},
        "parts": [
            {"type": "tool", "tool": "bash", "state": {"status": "running"}},
            {"type": "tool", "tool": "bash", "state": {"status": "completed", "output": "ok"}},
        ],
    }

    observations = map_open_code_message(message, CONTEXT)

    assert len(observations) == 2
    assert observations[0].id != observations[1].id
    assert observations[0].metadata["part_id"] != observations[1].metadata["part_id"]


def test_anonymous_messages_and_parts_use_distinct_stable_fallback_scopes():
    messages = [
        {
            "info": {"role": "assistant"},
            "parts": [
                {"type": "text", "text": "first"},
                {"type": "tool", "tool": "bash", "state": {"status": "completed"}},
            ],
        },
        {
            "info": {"role": "assistant"},
            "parts": [
                {"type": "text", "text": "second"},
                {"type": "tool", "tool": "bash", "state": {"status": "completed"}},
            ],
        },
    ]
    snapshots = [{"id": "anonymous-child", "messages": messages}]

    first = map_subtask_tree(snapshots, {}, CONTEXT)
    second = map_subtask_tree(snapshots, {}, CONTEXT)
    first_ids = [item.id for item in first]

    assert len(first) == 5
    assert len(first_ids) == len(set(first_ids))
    assert first_ids == [item.id for item in second]


def test_identical_anonymous_messages_in_distinct_child_sessions_do_not_collide():
    identical_message = {
        "info": {"role": "assistant"},
        "parts": [{"type": "text", "text": "same response"}],
    }
    observations = map_subtask_tree(
        [
            {"id": "child-one", "messages": [identical_message]},
            {"id": "child-two", "messages": [identical_message]},
        ],
        {},
        CONTEXT,
    )

    generations = [item for item in observations if item.name == "generation"]
    assert len(generations) == 2
    assert generations[0].id != generations[1].id
    assert generations[0].id == map_subtask_tree(
        [{"id": "child-one", "messages": [identical_message]}], {}, CONTEXT
    )[1].id


def test_nested_task_uses_metadata_session_id_not_parent_subtask_session_id():
    subtasks = [
        {
            "id": "child-real",
            "messages": [
                {
                    "info": {"id": "child-delegation", "role": "assistant"},
                    "parts": [
                        {
                            "id": "task-tool",
                            "type": "tool",
                            "tool": "task",
                            "sessionID": "child-real",
                            "state": {
                                "status": "running",
                                "metadata": {"sessionId": "grandchild-real"},
                            },
                        }
                    ],
                }
            ],
        }
    ]

    observations = map_subtask_tree(subtasks, {}, CONTEXT)

    delegated = observations[-1]
    assert delegated.name == "invoke_agent"
    assert delegated.session_id == "grandchild-real"
    assert delegated.metadata["task_id"] == "grandchild-real"
    assert delegated.metadata["parent_session_id"] == "child-real"


def test_timestamps_use_unix_seconds_and_milliseconds_explicitly():
    seconds = map_open_code_message(
        {
            "info": {
                "id": "seconds",
                "role": "assistant",
                "time": {"created": 1_700_000_000, "completed": 1_700_000_001},
            },
            "parts": [{"id": "seconds-part", "type": "text", "text": "ok"}],
        },
        CONTEXT,
    )[0]
    milliseconds = map_open_code_message(
        {
            "info": {
                "id": "milliseconds",
                "role": "assistant",
                "time": {"created": 1_700_000_000_000, "completed": 1_700_000_001_000},
            },
            "parts": [{"id": "milliseconds-part", "type": "text", "text": "ok"}],
        },
        CONTEXT,
    )[0]

    assert seconds.start_time == datetime.fromtimestamp(1_700_000_000, tz=timezone.utc)
    assert milliseconds.start_time == datetime.fromtimestamp(1_700_000_000, tz=timezone.utc)


def test_malformed_timestamps_become_none():
    observations = map_open_code_message(
        {
            "info": {
                "id": "bad-time",
                "role": "assistant",
                "time": {"created": "not-a-time", "completed": float("nan")},
            },
            "parts": [{"id": "bad-time-part", "type": "text", "text": "ok"}],
        },
        CONTEXT,
    )

    assert observations[0].start_time is None
    assert observations[0].end_time is None


@pytest.mark.parametrize("status", ["pending", "running"])
def test_tool_status_is_preserved_as_non_failed(status):
    observation = map_tool_part(
        {
            "id": "tool-pending",
            "type": "tool",
            "tool": "bash",
            "state": {"status": status, "input": {"command": "pwd"}},
        },
        CONTEXT,
    )

    assert observation.status == status
