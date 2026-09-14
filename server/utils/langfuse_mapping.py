"""Pure OpenCode-to-Langfuse observation mapping helpers."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any, Mapping

from utils.langfuse_config import (
    is_valid_observation_id,
    is_valid_trace_id,
    observation_id,
    trace_id_for,
)


@dataclass(frozen=True)
class Observation:
    kind: str
    id: str
    parent_id: str | None
    trace_id: str
    session_id: str | None
    name: str
    input: Any
    output: Any
    metadata: dict[str, Any]
    status: str
    start_time: datetime | None
    end_time: datetime | None


def _value(source: Mapping[str, Any] | None, *names: str) -> Any:
    if not source:
        return None
    for name in names:
        if name in source and source[name] is not None:
            return source[name]
    return None


def _timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if abs(number) >= 100_000_000_000:
        number /= 1000
    try:
        return datetime.fromtimestamp(number, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _times(source: Mapping[str, Any] | None) -> tuple[datetime | None, datetime | None]:
    time = _value(source, "time") or {}
    return (
        _timestamp(_value(time, "created", "start", "started")),
        _timestamp(_value(time, "completed", "end", "ended")),
    )


def _context(context: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(context or {})


def _metadata(context: Mapping[str, Any], **values: Any) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for key in ("message_id", "part_id", "tool", "task_id", "parent_session_id"):
        value = context.get(key)
        if value is not None:
            metadata[key] = value
    for key in ("model", "agent", "batch_id"):
        value = context.get(key)
        if value is not None:
            metadata[key] = value
    metadata.update({key: value for key, value in values.items() if value is not None})
    return metadata


def _trace_id(context: Mapping[str, Any]) -> str:
    trace_id = context.get("trace_id")
    if is_valid_trace_id(trace_id):
        return str(trace_id)
    return trace_id_for(
        str(context.get("session_id") or "unknown"),
        context.get("batch_id"),
        context.get("turn_id"),
    )


def _status(info: Mapping[str, Any], default: str = "running") -> str:
    if info.get("error"):
        return "failed"
    finish = info.get("finish")
    if finish and finish not in {"tool-calls", "pending"}:
        return "completed"
    return default


def _tool_status(state: Mapping[str, Any]) -> str:
    status = state.get("status")
    if status in {"error", "failed"}:
        return "failed"
    if status in {"completed", "success"}:
        return "completed"
    return status or "running"


def _anonymous_message_id(message: Mapping[str, Any], context: Mapping[str, Any]) -> str:
    info = _value(message, "info") or {}
    stable_info = {
        "role": _value(info, "role"),
        "session": _value(info, "sessionID", "sessionId"),
        "model": _value(info, "modelID", "model"),
    }
    scope = {
        "session": context.get("session_id"),
        "task": context.get("task_id"),
        "parent": context.get("parent_id"),
    }
    stable_parts = []
    for part in _value(message, "parts") or []:
        state = _value(part, "state") or {}
        stable_parts.append(
            {
                "type": _value(part, "type"),
                "tool": _value(part, "tool"),
                "text": _value(part, "text"),
                "input": _value(state, "input"),
                "task_session": _value(
                    _value(state, "metadata") or {}, "sessionId", "sessionID"
                ),
            }
        )
    fingerprint = json.dumps(
        {"context": scope, "info": stable_info, "parts": stable_parts},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16]
    position = context.get("message_index")
    suffix = f":{position}" if position is not None else ""
    return f"anonymous:{digest}{suffix}"


def _part_id(part: Mapping[str, Any], context: Mapping[str, Any], index: int | None = None) -> str:
    explicit = _value(part, "id")
    if explicit is not None:
        return str(explicit)
    scope = context.get("message_id") or context.get("session_id") or context.get("trace_id", "root")
    position = context.get("part_index") if index is None else index
    return f"{scope}:tool:{position if position is not None else 0}"


def map_tool_part(part: Mapping[str, Any], context: Mapping[str, Any] | None) -> Observation:
    """Map one OpenCode tool part without performing I/O."""
    ctx = _context(context)
    state = _value(part, "state") or {}
    tool_name = _value(part, "tool") or "tool"
    part_id = _part_id(part, ctx)
    input_value = _value(state, "input")
    output_value = _value(state, "output", "result", "error")
    child_id = _value(_value(state, "metadata") or {}, "sessionId", "sessionID")
    is_task = tool_name == "task"
    session_id = child_id if is_task and child_id else ctx.get("session_id")
    name = "invoke_agent" if is_task else "execute_tool"
    kind = "span"
    start_time, end_time = _times(part)
    if start_time is None and end_time is None:
        start_time, end_time = _times(state)
    metadata = _metadata(
        ctx,
        part_id=part_id,
        tool=tool_name,
        task_id=child_id if is_task else None,
        parent_session_id=_value(part, "sessionID", "sessionId") if is_task else None,
        agent=_value(input_value, "subagent_type", "agent") if is_task else None,
    )
    return Observation(
        kind=kind,
        id=observation_id(kind, f"{name}:{part_id}"),
        parent_id=ctx.get("parent_id"),
        trace_id=_trace_id(ctx),
        session_id=session_id,
        name=name,
        input=input_value,
        output=output_value,
        metadata=metadata,
        status=_tool_status(state),
        start_time=start_time,
        end_time=end_time,
    )


def map_open_code_message(
    message: Mapping[str, Any], context: Mapping[str, Any] | None
) -> list[Observation]:
    """Map assistant text and tool parts from one REST message snapshot."""
    info = _value(message, "info") or {}
    if _value(info, "role") != "assistant":
        return []
    ctx = _context(context)
    message_id = _value(info, "id") or _value(message, "id")
    if message_id is None:
        message_id = _anonymous_message_id(message, ctx)
    ctx.update(
        message_id=message_id,
        model=_value(info, "modelID", "model") or ctx.get("model"),
        session_id=_value(info, "sessionID", "sessionId") or ctx.get("session_id"),
    )
    start_time, end_time = _times(info)
    observations: list[Observation] = []
    seen: set[str] = set()
    for index, part in enumerate(_value(message, "parts") or []):
        part_type = _value(part, "type")
        part_id = _value(part, "id") or f"index-{index}"
        if part_id in seen:
            continue
        seen.add(part_id)
        if part_type == "tool":
            part_context = dict(ctx, part_index=index)
            observations.append(map_tool_part(part, part_context))
        elif part_type == "text" and _value(part, "text") is not None:
            observations.append(
                Observation(
                    kind="generation",
                    id=observation_id("generation", f"{message_id}:{part_id}"),
                    parent_id=ctx.get("parent_id"),
                    trace_id=_trace_id(ctx),
                    session_id=ctx.get("session_id"),
                    name="generation",
                    input=None,
                    output=_value(part, "text"),
                    metadata=_metadata(ctx, part_id=part_id),
                    status=_status(info),
                    start_time=start_time,
                    end_time=end_time,
                )
            )
    return observations


def _message_list(messages: Any, session_id: str | None) -> list[Mapping[str, Any]]:
    if isinstance(messages, Mapping):
        selected = messages.get(session_id, []) if session_id is not None else []
        return list(selected or [])
    selected = []
    for message in messages or []:
        info = _value(message, "info") or {}
        owner = _value(info, "sessionID", "sessionId")
        if session_id is None or owner in {None, session_id}:
            selected.append(message)
    return selected


def _status_rank(status: str) -> int:
    return {"pending": 0, "running": 1, "completed": 2, "failed": 2}.get(status, 1)


def _merge_observation(previous: Observation, current: Observation) -> Observation:
    previous_rank = _status_rank(previous.status)
    current_rank = _status_rank(current.status)
    if current_rank > previous_rank or (
        current_rank == previous_rank
        and (current.output is not None or current.input is not None)
    ):
        preferred, other = current, previous
    else:
        preferred, other = previous, current
    previous_output = previous.output
    current_output = current.output
    if previous_output is not None and current_output is not None:
        output = (
            previous_output
            if len(str(previous_output)) >= len(str(current_output))
            else current_output
        )
    else:
        output = previous_output if previous_output is not None else current_output
    metadata = dict(other.metadata)
    metadata.update(preferred.metadata)
    return Observation(
        kind=preferred.kind,
        id=preferred.id,
        parent_id=preferred.parent_id or other.parent_id,
        trace_id=preferred.trace_id or other.trace_id,
        session_id=preferred.session_id or other.session_id,
        name=preferred.name,
        input=preferred.input if preferred.input is not None else other.input,
        output=output,
        metadata=metadata,
        status=preferred.status,
        start_time=preferred.start_time or other.start_time,
        end_time=preferred.end_time or other.end_time,
    )


def map_subtask_tree(
    subtasks: Any,
    messages: Any,
    context: Mapping[str, Any] | None,
) -> list[Observation]:
    """Flatten nested subtask records into deduplicated parent-linked observations."""
    ctx = _context(context)
    output: list[Observation] = []
    seen: set[str] = set()

    def visit(records: Any, parent_id: str | None, parent_session: str | None) -> None:
        if isinstance(records, Mapping):
            records = list(records.values())
        for record in records or []:
            sid = _value(record, "id", "session_id", "sessionID")
            if not sid:
                continue
            task_id = observation_id("invoke_agent", str(sid))
            invoke = Observation(
                kind="span",
                id=task_id,
                parent_id=parent_id,
                trace_id=_trace_id(ctx),
                session_id=str(sid),
                name="invoke_agent",
                input=_value(record, "description", "input"),
                output=None,
                metadata=_metadata(
                    ctx,
                    task_id=sid,
                    parent_session_id=parent_session,
                    agent=_value(record, "agent"),
                    model=_value(record, "model") or ctx.get("model"),
                ),
                status=_value(record, "status") or "running",
                start_time=_timestamp(_value(record, "started_at", "start_time")),
                end_time=_timestamp(_value(record, "ended_at", "end_time")),
            )
            if invoke.id not in seen:
                seen.add(invoke.id)
                output.append(invoke)
            else:
                position = next(index for index, item in enumerate(output) if item.id == invoke.id)
                output[position] = _merge_observation(output[position], invoke)
            child_context = dict(ctx)
            child_context.update(
                session_id=str(sid),
                parent_id=task_id,
                agent=_value(record, "agent") or ctx.get("agent"),
                model=_value(record, "model") or ctx.get("model"),
            )
            record_messages = record["messages"] if "messages" in record else messages
            for message_index, message in enumerate(_message_list(record_messages, sid)):
                message_context = dict(child_context, message_index=message_index)
                for observation in map_open_code_message(message, message_context):
                    if observation.id not in seen:
                        seen.add(observation.id)
                        output.append(observation)
                    else:
                        position = next(
                            index for index, item in enumerate(output) if item.id == observation.id
                        )
                        output[position] = _merge_observation(output[position], observation)
            visit(_value(record, "subtasks") or [], task_id, str(sid))

    visit(subtasks, ctx.get("parent_id"), ctx.get("session_id"))
    return output


def normalize_observations(
    observations: list[Observation], application_session_id: str | None = None
) -> list[Observation]:
    """Canonicalize task spans and application session correlation.

    A task appears both as the parent's ``tool:'task'`` part and as the
    discovered subtask record.  They describe one invocation, so merge them
    by the real child session ID and use that stable ID for the parent links.
    The application session is deliberately separate from OpenCode session IDs
    and is applied to every exported observation.
    """
    output: list[Observation] = []
    positions: dict[str, int] = {}
    for observation in observations:
        task_id = observation.metadata.get("task_id") if observation.name == "invoke_agent" else None
        canonical_id = (
            task_id
            if task_id and is_valid_observation_id(task_id)
            else observation_id("invoke_agent", str(task_id))
            if task_id
            else observation.id
            if is_valid_observation_id(observation.id)
            else observation_id(observation.kind, observation.id)
        )
        parent_id = observation.parent_id
        if parent_id and parent_id.startswith("invoke_agent:"):
            parent_task = parent_id.split(":", 1)[1]
            parent_id = f"invoke_agent:{parent_task}"
        current = replace(
            observation,
            id=canonical_id,
            parent_id=parent_id,
            session_id=application_session_id or observation.session_id,
        )
        position = positions.get(canonical_id)
        if position is None:
            positions[canonical_id] = len(output)
            output.append(current)
        else:
            output[position] = _merge_observation(output[position], current)
    return output
