"""Execution audit collection (execution-audit Spec §8/§9).

Unified, best-effort, raise-free collectors for execution facts:
attempts, prompt snapshots, manifests, events. Every AI execution source
(interactive/batch/scan/kefu/open_api/trace_analysis) records the same
attempt shape at its dispatch boundary; the persistence listeners close
the attempt on turn completion.

Design rules (Spec §6.2/§18):
- Audit collection must NEVER break execution: all public helpers swallow
  and log failures unless EXECUTION_AUDIT_STRICT=1 (tests).
- Plaintext effective prompts are NOT stored by default
  (EXECUTION_AUDIT_PROMPT_PLAINTEXT=1 opts in); hashes/lengths always are.
- Events are append-only with per-attempt monotonic event_seq (idempotent
  via UNIQUE(attempt_id, event_seq)).
"""

import hashlib
import json
import logging
import os
import secrets
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _enabled() -> bool:
    return os.getenv('EXECUTION_AUDIT_ENABLED', '1').strip() not in ('0', 'false', 'no')


def _strict() -> bool:
    return os.getenv('EXECUTION_AUDIT_STRICT', '0').strip() == '1'


def _store_prompt_plaintext() -> bool:
    return os.getenv('EXECUTION_AUDIT_PROMPT_PLAINTEXT', '0').strip() == '1'


def sha256_text(text) -> str | None:
    if text is None:
        return None
    if isinstance(text, str):
        text = text.encode('utf-8', errors='replace')
    return hashlib.sha256(text).hexdigest()


def sha256_file(path: str) -> str | None:
    try:
        h = hashlib.sha256()
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _now():
    return datetime.now(timezone.utc)


def _safe(fn, *args, **kwargs):
    """Run an audit write; failures log but never propagate (unless strict)."""
    if not _enabled():
        return None
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        if _strict():
            raise
        logger.warning('execution_audit.%s failed: %s', fn.__name__, e)
        return None


# ── Attempt lifecycle ───────────────────────────────────────────────────

def create_attempt(session_id: str, *, source_type: str, operation: str = 'send',
                   source_id: str | None = None, parent_attempt_id: str | None = None,
                   requested_agent=None, effective_agent=None,
                   agent_resolution: str = 'unknown',
                   requested_model=None, effective_model=None,
                   model_resolution: str = 'unknown',
                   raw_user_content=None, effective_prompt=None,
                   prompt_version: str | None = None,
                   augmentations: dict | None = None,
                   context_snapshot: dict | None = None,
                   workspace_path: str | None = None) -> str | None:
    """Create an attempt + prompt snapshot at the dispatch boundary.

    Returns the attempt id, or None when auditing is disabled/failed.
    `agent_resolution`/`model_resolution` must say HOW the effective value
    was chosen ('requested'/'session_default'/'runtime_default'/...); an
    empty effective value keeps resolution 'unknown' — never pretend a
    default was used (Spec §8.1 constraints).
    """
    def _impl() -> str | None:
        from db import get_db
        attempt_id = 'att_' + secrets.token_hex(6)
        raw_hash = sha256_text(raw_user_content)
        eff_hash = sha256_text(effective_prompt)
        eff_len = len(effective_prompt) if isinstance(effective_prompt, str) else None
        a_res = agent_resolution
        m_res = model_resolution
        if a_res == 'unknown' and effective_agent:
            a_res = 'requested' if requested_agent else 'runtime_default'
        if m_res == 'unknown' and effective_model:
            m_res = 'requested' if requested_model else 'runtime_default'
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COALESCE(MAX(attempt_no), 0) + 1 FROM ai_execution_attempts "
                    "WHERE session_id = %s", (session_id,))
                attempt_no = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO ai_execution_attempts "
                    "(id, session_id, source_type, source_id, parent_attempt_id, "
                    " attempt_no, operation, requested_agent, effective_agent, "
                    " agent_resolution, requested_model, effective_model, "
                    " model_resolution, raw_prompt_hash, effective_prompt_hash, "
                    " effective_prompt_len, prompt_version, workspace_path, status, "
                    " started_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                    " 'running', NOW())",
                    (attempt_id, session_id, source_type, source_id,
                     parent_attempt_id, attempt_no, operation,
                     requested_agent or None, effective_agent or None,
                     a_res, requested_model or None,
                     effective_model or None, m_res,
                     raw_hash, eff_hash, eff_len, prompt_version,
                     workspace_path))
                cur.execute(
                    "INSERT INTO ai_execution_prompt_snapshots "
                    "(id, attempt_id, raw_user_content, effective_prompt, "
                    " raw_user_content_hash, effective_prompt_hash, "
                    " effective_prompt_len, augmentations, context_snapshot, "
                    " redaction_status) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    ('psnap_' + secrets.token_hex(6), attempt_id,
                     raw_user_content if _store_prompt_plaintext() else None,
                     effective_prompt if _store_prompt_plaintext() else None,
                     raw_hash, eff_hash, eff_len or 0,
                     json.dumps(augmentations or {}, ensure_ascii=False),
                     json.dumps(context_snapshot or {}, ensure_ascii=False),
                     'plaintext' if _store_prompt_plaintext() else 'redacted'))
            conn.commit()
        return attempt_id
    return _safe(_impl)


def finish_latest_running(session_id: str, status: str, *,
                          error_code: str | None = None,
                          error_message=None) -> str | None:
    """Close the most recent running attempt of a session (turn converged).

    Used by the persistence listeners on session.idle / session.error and by
    the batch worker's terminal markers."""
    def _impl() -> str | None:
        from db import get_db
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE ai_execution_attempts SET status=%s, "
                    " error_code=%s, error_message=%s, finished_at=NOW() "
                    "WHERE id = (SELECT id FROM ai_execution_attempts "
                    "  WHERE session_id=%s AND status IN ('accepted','running',"
                    "  'recovering') ORDER BY attempt_no DESC LIMIT 1) "
                    "RETURNING id",
                    (status, error_code,
                     (str(error_message)[:2000] if error_message else None),
                     session_id))
                row = cur.fetchone()
            conn.commit()
            return row[0] if row else None
    return _safe(_impl)


def set_manifest_result(attempt_id: str, name: str, *, invoked=None,
                        runtime_loaded=None, evidence_refs=None) -> None:
    """Attach post-run observations to a manifest row (e.g. a skill was
    observed being used via its tool calls)."""
    def _impl() -> None:
        from db import get_db
        sets, params = [], []
        if invoked is not None:
            sets.append('invoked=%s'); params.append(invoked)
        if runtime_loaded is not None:
            sets.append('runtime_loaded=%s'); params.append(runtime_loaded)
        if evidence_refs is not None:
            sets.append('evidence_refs=%s')
            params.append(json.dumps(evidence_refs, ensure_ascii=False))
        if not sets:
            return
        params.extend([attempt_id, name])
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE ai_execution_manifests SET {', '.join(sets)} "
                    "WHERE attempt_id=%s AND name=%s", params)
            conn.commit()
    _safe(_impl)


# ── Events ───────────────────────────────────────────────────────────────

def attach_attempt_to_turn(turn_id: str, attempt_id: str) -> None:
    """把 attempt id 记到 ai_chat_turns.attempt_id（P0 ownership：审计关联，
    非并发闸门）。best-effort。"""
    def _impl() -> None:
        from db import get_db
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE ai_chat_turns SET attempt_id = %s "
                            "WHERE id = %s", (attempt_id, turn_id))
            conn.commit()
    _safe(_impl)


def record_event(attempt_id: str, event_type: str, *, occurred_at=None,
                 session_id=None, parent_session_id=None, message_id=None,
                 part_id=None, tool_call_id=None, parent_part_id=None,
                 subtask_id=None, status=None, payload=None) -> str | None:
    """Append one immutable event. Idempotent enough: a fresh event_seq per
    call; retries of the SAME logical event should reuse the caller-chosen
    event id instead of calling this twice."""
    def _impl() -> str | None:
        from db import get_db
        event_id = 'evt_' + secrets.token_hex(7)
        occ = occurred_at or _now()
        if isinstance(occ, (int, float)):
            occ = datetime.fromtimestamp(occ, tz=timezone.utc)
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COALESCE(MAX(event_seq), 0) + 1 "
                    "FROM ai_execution_events WHERE attempt_id=%s", (attempt_id,))
                seq = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO ai_execution_events "
                    "(id, attempt_id, event_seq, event_type, occurred_at, "
                    " session_id, parent_session_id, message_id, part_id, "
                    " tool_call_id, parent_part_id, subtask_id, status, payload) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (event_id, attempt_id, seq, event_type, occ,
                     session_id, parent_session_id, message_id, part_id,
                     tool_call_id, parent_part_id, subtask_id, status,
                     json.dumps(payload or {}, ensure_ascii=False, default=str)))
            conn.commit()
        return event_id
    return _safe(_impl)


# ── Manifests ────────────────────────────────────────────────────────────

def scan_workspace_manifests(workspace_path: str | None) -> list[dict]:
    """Inventory the definitions actually visible to this execution's
    workspace (Spec §2.2: injected ≠ loaded ≠ invoked — this only ever
    claims `injected/available`).

    Returns rows for: platform/session skills under .opencode/skills,
    project agents under .opencode/agent, AGENTS.md guidance. opencode.json
    is returned separately by the caller via workspace_config hash on the
    attempt row."""
    out: list[dict] = []
    if not workspace_path or not os.path.isdir(workspace_path):
        return out
    oc = os.path.join(workspace_path, '.opencode')
    skills_root = os.path.join(oc, 'skills')
    if os.path.isdir(skills_root):
        for name in sorted(os.listdir(skills_root)):
            skill_md = os.path.join(skills_root, name, 'SKILL.md')
            if os.path.isfile(skill_md):
                out.append({
                    'kind': 'skill', 'name': name, 'source': 'session',
                    'path': skill_md, 'content_hash': sha256_file(skill_md),
                    'injected': True, 'injection_status': 'success',
                })
    agent_root = os.path.join(oc, 'agent')
    if os.path.isdir(agent_root):
        for fn in sorted(os.listdir(agent_root)):
            if fn.endswith('.md'):
                p = os.path.join(agent_root, fn)
                out.append({
                    'kind': 'agent', 'name': fn[:-3], 'source': 'project',
                    'path': p, 'content_hash': sha256_file(p),
                    'injected': True, 'injection_status': 'success',
                })
    agents_md = os.path.join(workspace_path, 'AGENTS.md')
    if os.path.isfile(agents_md):
        out.append({
            'kind': 'guidance', 'name': 'AGENTS.md', 'source': 'generated',
            'path': agents_md, 'content_hash': sha256_file(agents_md),
            'injected': True, 'injection_status': 'success',
        })
    return out


def save_manifests(attempt_id: str, manifests: list[dict]) -> int:
    """Persist manifest rows. Each dict: kind,name,source,path,content_hash,
    injected,injection_status,selected,invoked,runtime_loaded (missing keys
    default to the schema's unknown defaults)."""
    def _impl() -> int:
        from db import get_db
        rows = manifests or []
        with get_db() as conn:
            with conn.cursor() as cur:
                for m in rows:
                    cur.execute(
                        "INSERT INTO ai_execution_manifests "
                        "(id, attempt_id, kind, name, source, path, content_hash, "
                        " injected, injection_status, selected, invoked, "
                        " runtime_loaded) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        ('man_' + secrets.token_hex(6), attempt_id,
                         m.get('kind', 'skill'), m.get('name', ''),
                         m.get('source', 'unknown'), m.get('path'),
                         m.get('content_hash'),
                         bool(m.get('injected', False)),
                         m.get('injection_status', 'unknown'),
                         m.get('selected', 'unknown'),
                         m.get('invoked', 'unknown'),
                         m.get('runtime_loaded', 'unknown')))
            conn.commit()
        return len(rows)
    return _safe(_impl) or 0


def collect_and_save_workspace_manifests(attempt_id: str,
                                         workspace_path: str | None) -> int:
    return save_manifests(attempt_id, scan_workspace_manifests(workspace_path))


# ── Read helpers (admin APIs / auditor) ─────────────────────────────────

def get_attempts(session_id: str, limit: int = 20) -> list[dict]:
    def _impl() -> list[dict]:
        from db import get_db
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, source_type, source_id, parent_attempt_id, "
                    " attempt_no, operation, requested_agent, effective_agent, "
                    " agent_resolution, requested_model, effective_model, "
                    " model_resolution, raw_prompt_hash, effective_prompt_hash, "
                    " effective_prompt_len, status, error_code, error_message, "
                    " started_at, finished_at, workspace_path "
                    "FROM ai_execution_attempts WHERE session_id=%s "
                    "ORDER BY attempt_no DESC LIMIT %s", (session_id, limit))
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, r)) for r in cur.fetchall()]
    return _safe(_impl) or []


def get_manifests(attempt_id: str) -> list[dict]:
    def _impl() -> list[dict]:
        from db import get_db
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT kind, name, source, path, content_hash, injected, "
                    " injection_status, runtime_loaded, selected, invoked "
                    "FROM ai_execution_manifests WHERE attempt_id=%s "
                    "ORDER BY kind, name", (attempt_id,))
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, r)) for r in cur.fetchall()]
    return _safe(_impl) or []


def get_events(attempt_id: str, limit: int = 500) -> list[dict]:
    def _impl() -> list[dict]:
        from db import get_db
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT event_seq, event_type, occurred_at, session_id, "
                    " parent_session_id, message_id, part_id, tool_call_id, "
                    " parent_part_id, subtask_id, status, payload "
                    "FROM ai_execution_events WHERE attempt_id=%s "
                    "ORDER BY event_seq LIMIT %s", (attempt_id, limit))
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, r)) for r in cur.fetchall()]
    return _safe(_impl) or []
