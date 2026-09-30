"""Declared Plan projection (execution-audit Spec §11/§15).

Rebuilds the agent's SELF-declared steps from persisted todowrite/todoread
tool snapshots — the server-side twin of src/utils/todos.ts. This is the
agent's own plan, NEVER proof that the work actually happened; the auditor
(Spec §11.3) cross-checks it against contract steps and tool evidence.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

_TODO_TOOLS = {'todowrite', 'todoread'}


def _norm_status(s) -> str:
    return (s or 'pending') if s in ('pending', 'in_progress', 'completed',
                                     'cancelled') else 'pending'


def _extract_todos(part: dict) -> list[dict] | None:
    raw = None
    inp = part.get('input')
    if isinstance(inp, dict) and isinstance(inp.get('todos'), list):
        raw = inp['todos']
    if raw is None:
        res = part.get('result') or part.get('output')
        if isinstance(res, str):
            try:
                res = json.loads(res)
            except (ValueError, TypeError):
                res = None
        if isinstance(res, dict) and isinstance(res.get('todos'), list):
            raw = res['todos']
        elif isinstance(res, list):
            raw = res
    if not isinstance(raw, list):
        return None
    out = []
    for t in raw:
        if not isinstance(t, dict):
            continue
        out.append({
            'id': str(t.get('id') or ''),
            'content': str(t.get('content') or t.get('text') or ''),
            'status': _norm_status(t.get('status')),
        })
    return out or None


def build_declared_plan(messages: list[dict]) -> dict:
    """messages: [{id, role, content, created_at, seq?}] ordered by seq.
    Returns the latest snapshot plus per-step status-change counts."""
    latest: list[dict] = []
    source = None
    snapshots = 0
    status_changes: dict[str, int] = {}
    first_seen: dict[str, dict] = {}
    last_message_id = None

    for m in messages:
        for part in (m.get('content') or []):
            if not isinstance(part, dict) or part.get('type') != 'tool_use':
                continue
            if (part.get('name') or '').lower() not in _TODO_TOOLS:
                continue
            todos = _extract_todos(part)
            if todos is None:
                continue
            snapshots += 1
            source = (part.get('name') or '').lower()
            last_message_id = m.get('id')
            prev = {s['id']: s['status'] for s in latest}
            for t in todos:
                if prev.get(t['id']) != t['status']:
                    status_changes[t['id']] = status_changes.get(t['id'], 0) + 1
                if t['id'] not in first_seen:
                    first_seen[t['id']] = {
                        'message_id': m.get('id'),
                        'seq': m.get('seq'),
                    }
            latest = todos

    for s in latest:
        s['status_changes'] = status_changes.get(s['id'], 0)
        s['first_seen'] = first_seen.get(s['id'])

    completed = sum(1 for s in latest if s['status'] == 'completed')
    return {
        'declared_steps': latest,
        'snapshot_count': snapshots,
        'source': source,
        'last_message_id': last_message_id,
        'completed_count': completed,
        'total_count': len(latest),
        'evidence_level': 'declared_plan',
    }
