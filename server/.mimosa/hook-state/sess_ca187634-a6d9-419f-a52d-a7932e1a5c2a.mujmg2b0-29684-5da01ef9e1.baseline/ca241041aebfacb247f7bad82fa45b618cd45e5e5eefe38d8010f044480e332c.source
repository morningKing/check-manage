"""SkillOpt 采集与治理（Spec P2）。

- collect_skill_invocations(attempt_id, ...)：attempt 收敛后从 manifests +
  工具证据推断 Skill 调用（source=heuristic, inferred）写入
  ai_skill_invocations；runtime 已确认的行不被降级。
- record_runtime_skill_event：OpenCode 插件（baize-trace.js）上报的
  skill 工具调用 → confirmed 落库 + manifests/事件标注，把 invoked 从
  inferred 升级为 confirmed。
- apply_retention：审计事件分层保留（明文 payload 默认 30 天，行默认
  180 天）；attempts/diagnoses/invocations 永存。
- ensure_runtime_plugin：随启动把插件写入 OPENCODE_GLOBAL_DIR/plugin/
  （随项目自动安装并注入运行时环境）。
"""

import json
import logging
import secrets

from db import get_db

logger = logging.getLogger(__name__)

RUNTIME_PLUGIN_NAME = 'baize-trace.js'


def _utcnow():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


# ── 调用记录（唯一实现） ─────────────────────────────────────────────────

def upsert_invocation(session_id: str, attempt_id: str | None, skill: str,
                      *, skill_hash: str | None = None, source: str = 'runtime',
                      evidence_level: str = 'confirmed', status: str | None = None,
                      completed_at=None, tool_calls: int | None = None,
                      duration_ms: int | None = None,
                      evidence_refs: list | None = None) -> str | None:
    """写入/升级一次 Skill 调用记录。

    - runtime 行：evidence_level=confirmed，不被 heuristic 降级；
    - heuristic 行：仅在无 runtime 行时创建（DO UPDATE 里 runtime 优先）。
    """
    outcome = None
    if status:
        if status in ('completed', 'running'):
            outcome = 'completed' if status == 'completed' else None
        elif status == 'error':
            outcome = 'failed'
    # skill_hash 归一为 ''（非 NULL）：UNIQUE (attempt_id, skill_name, skill_hash)
    # 对 NULL 视为互异，NULL 会造成 runtime 上报（无 hash）每次一行、永不冲突。
    skill_hash = skill_hash or ''
    iid = 'inv_' + secrets.token_hex(6)
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO ai_skill_invocations "
                    "(id, session_id, attempt_id, skill_name, skill_hash, source, "
                    " evidence_level, outcome, completed_at, tool_calls, "
                    " duration_ms, evidence_refs) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (attempt_id, skill_name, skill_hash) DO UPDATE SET "
                    " source = CASE WHEN EXCLUDED.source = 'runtime' THEN 'runtime' "
                    "   ELSE ai_skill_invocations.source END, "
                    " evidence_level = CASE "
                    "   WHEN EXCLUDED.evidence_level = 'confirmed' THEN 'confirmed' "
                    "   ELSE ai_skill_invocations.evidence_level END, "
                    " outcome = COALESCE(EXCLUDED.outcome, "
                    "   ai_skill_invocations.outcome), "
                    " completed_at = COALESCE(EXCLUDED.completed_at, "
                    "   ai_skill_invocations.completed_at)",
                    (iid, session_id, attempt_id, skill, skill_hash, source,
                     evidence_level, outcome, completed_at, tool_calls,
                     duration_ms,
                     json.dumps(evidence_refs or [], ensure_ascii=False)))
            conn.commit()
        return iid
    except Exception as e:
        logger.warning('upsert_invocation failed skill=%s: %s', skill, e)
        return None


# ── 运行时事件上报（插件回调） ───────────────────────────────────────────

def _platform_session_id(session_id: str) -> str:
    """OpenCode 内部会话 id（ses_…）→ 平台会话 id（sess_…）。

    插件在 OpenCode 宿主进程内只能看到 OpenCode 自己的 sessionID；库里
    ai_skill_invocations / ai_execution_attempts 的 session_id 外键指向
    ai_chat_sessions(id)，必须先映射，否则所有上报都撞外键约束。
    传进来的若已是平台 id（sess_ 前缀）则原样返回。"""
    if not session_id or session_id.startswith('sess_'):
        return session_id
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM ai_chat_sessions "
                    "WHERE opencode_session_id = %s "
                    "ORDER BY last_active_at DESC NULLS LAST LIMIT 1",
                    (session_id,))
                row = cur.fetchone()
                return row[0] if row else ''
    except Exception as e:
        logger.warning('platform session lookup failed %s: %s', session_id, e)
        return ''


def record_runtime_skill_event(payload: dict) -> dict:
    """OpenCode 插件上报 skill 工具调用（Spec P2：invoked → confirmed）。

    payload: {skillName, sessionID, messageID, partID, status, title}
    """
    skill = payload.get('skillName') or ''
    status = payload.get('status') or ''
    session_id = _platform_session_id(payload.get('sessionID') or '')
    if not session_id or not skill:
        return {'ok': False, 'reason': 'missing fields'}

    from utils import execution_audit
    attempts = execution_audit.get_attempts(session_id, limit=3)
    attempt = attempts[0] if attempts else None
    attempt_id = attempt['id'] if attempt else None
    ref = f"event:skill:{payload.get('partID', '')}"

    if attempt_id:
        execution_audit.set_manifest_result(
            attempt_id, skill,
            invoked='confirmed' if status in ('completed', 'running') else None,
            runtime_loaded='confirmed', evidence_refs=[ref])
        execution_audit.record_event(
            attempt_id, 'skill.invoke', session_id=session_id,
            message_id=payload.get('messageID'), part_id=payload.get('partID'),
            status=status, payload={'skill': skill, 'title': payload.get('title', '')})

    upsert_invocation(session_id, attempt_id, skill, source='runtime',
                      evidence_level='confirmed', status=status or None,
                      evidence_refs=[ref])
    return {'ok': True}


def mark_session_idle(session_id: str) -> None:
    """会话收敛：把该会话 running 的 invocations 收口为 completed（尽力）。

    插件上报的 sessionID 是 OpenCode 内部 id，先映射回平台会话 id。"""
    try:
        session_id = _platform_session_id(session_id)
        if not session_id:
            return
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE ai_skill_invocations SET outcome='completed', "
                    " completed_at=NOW() WHERE session_id=%s AND outcome IS NULL",
                    (session_id,))
            conn.commit()
    except Exception as e:
        logger.warning('mark_session_idle failed: %s', e)


# ── 启发式采集（attempt 收敛时兜底） ─────────────────────────────────────

def collect_skill_invocations(attempt_id: str, session_id: str,
                              manifests: list[dict], messages: list[dict]) -> int:
    """从 manifests + 工具证据推断调用（inferred）；runtime 已确认的行跳过。"""
    if not manifests:
        return 0
    existing_pairs, runtime_names = _existing_invocations(attempt_id)
    tool_count = sum(
        1 for m in messages if m.get('role') == 'assistant'
        for p in (m.get('content') or [])
        if isinstance(p, dict) and p.get('type') == 'tool_use')
    n = 0
    for m in manifests:
        if m.get('kind') != 'skill':
            continue
        name, h = m.get('name') or '', m.get('content_hash')
        # runtime 行（skill_hash=''）优先：同 skill 已确证就不再写 inferred 行
        if not name or not h or (name, h) in existing_pairs or name in runtime_names:
            continue
        upsert_invocation(session_id, attempt_id, name, skill_hash=h,
                          source='heuristic', evidence_level='inferred',
                          tool_calls=tool_count or None,
                          evidence_refs=['manifest:' + name])
        n += 1
    return n


def _existing_invocations(attempt_id: str) -> tuple[set, set]:
    """返回 ((skill_name, skill_hash) 对集合, 已有 runtime 行的 skill_name 集合）。"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT skill_name, skill_hash, source FROM ai_skill_invocations "
                        "WHERE attempt_id = %s", (attempt_id,))
            rows = cur.fetchall()
            return ({(r[0], r[1]) for r in rows},
                    {r[0] for r in rows if r[2] == 'runtime'})


# ── 运行时插件自动安装 ───────────────────────────────────────────────────

PLUGIN_JS = """// Baize runtime trace plugin (auto-installed by Baize server).
// Reports skill load/invoke lifecycle so SkillOpt can prove actual skill
// usage (invoked=confirmed) instead of heuristic inference.
// Endpoint 与上报 token 由服务端安装时嵌入；同名 env 变量存在时优先。
const ENDPOINT = process.env.BAIZE_RUNTIME_EVENT_URL || '__ENDPOINT__'
const TOKEN = process.env.BAIZE_INTERNAL_TOKEN || '__TOKEN__'

async function report(body) {
  if (!ENDPOINT) return
  try {
    await fetch(ENDPOINT, {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-internal-token': TOKEN },
      body: JSON.stringify(body),
    })
  } catch { /* 上报失败不影响执行 */ }
}

export const BaizeTracePlugin = async () => {
  return {
    async event({ event }) {
      const type = event?.type || ''
      const props = event?.properties || {}
      const part = props.part || {}
      if (type === 'message.part.updated' && part?.type === 'tool'
          && String(part.tool || '').toLowerCase() === 'skill') {
        const state = part.state || {}
        const input = state.input || {}
        const m = /Loaded skill:\\s*([\\w.-]+)/.exec(state.title || '')
        await report({
          kind: 'skill',
          skillName: input.name || input.skill || (m && m[1]) || '',
          sessionID: props.sessionID || part.sessionID || '',
          messageID: part.messageID || '',
          partID: part.id || '',
          status: state.status || '',
          title: state.title || '',
        })
        return
      }
      if (type === 'session.idle') {
        await report({ kind: 'session.idle', sessionID: props.sessionID || '' })
      }
    },
  }
}
"""


def ensure_runtime_plugin(global_dir: str, endpoint: str, token: str = '') -> str | None:
    """写入 <OPENCODE_GLOBAL_DIR>/plugin/baize-trace.js（幂等）。

    endpoint 与 internal token 都直接嵌入插件文件（serve 子进程环境不可靠，
    内嵌值保证开箱即用；BAIZE_RUNTIME_EVENT_URL / BAIZE_INTERNAL_TOKEN env
    优先级更高，便于部署侧覆写）。"""
    import os
    if not global_dir:
        return None
    try:
        pdir = os.path.join(global_dir, 'plugin')
        os.makedirs(pdir, exist_ok=True)
        path = os.path.join(pdir, RUNTIME_PLUGIN_NAME)
        js = PLUGIN_JS.replace('__ENDPOINT__', endpoint or '') \
                      .replace('__TOKEN__', token or '')
        current = open(path, encoding='utf-8').read() if os.path.exists(path) else ''
        if current != js:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(js)
        return path
    except Exception as e:
        logger.warning('ensure_runtime_plugin failed: %s', e)
        return None


# ── 保留策略（明文 30 天 / 行 180 天） ──────────────────────────────────

def apply_retention() -> dict:
    import os
    plain_days = int(os.getenv('EXECUTION_AUDIT_EVENT_PLAINTEXT_DAYS', '30'))
    rows_days = int(os.getenv('EXECUTION_AUDIT_EVENT_ROWS_DAYS', '180'))
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE ai_execution_events SET payload = '{}'::jsonb, "
                " redaction_status = 'expired' "
                "WHERE received_at < NOW() - (%s || ' days')::interval "
                "  AND payload <> '{}'::jsonb", (plain_days,))
            plain = cur.rowcount
            cur.execute(
                "DELETE FROM ai_execution_events "
                "WHERE received_at < NOW() - (%s || ' days')::interval", (rows_days,))
            rows = cur.rowcount
        conn.commit()
    return {'payload_cleared': plain, 'rows_deleted': rows}
