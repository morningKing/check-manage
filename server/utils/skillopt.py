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
  （随项目自动安装并注入运行时环境）。插件除 skill 上报外还注册
  tool.execute.before 钩子（P3-C3）：工具调用前向
  /ai/gate/internal/pre-check 校验 deny list（mode='pre' 期望），
  命中即抛错阻断该次工具调用；门禁不可达时放行（终态核对兜底）。
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
                      evidence_refs: list | None = None,
                      subtask_id: str | None = None) -> str | None:
    """写入/升级一次 Skill 调用记录。

    - runtime 行：evidence_level=confirmed，不被 heuristic 降级；
    - heuristic 行：仅在无 runtime 行时创建（DO UPDATE 里 runtime 优先）；
    - subtask_id：spec §5c，subagent 发起的调用标注其子代理 OC 会话 id。
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
                    " duration_ms, evidence_refs, subtask_id) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
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
                     json.dumps(evidence_refs or [], ensure_ascii=False),
                     subtask_id))
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
    传进来的若已是平台 id（sess_ 前缀）则原样返回。
    spec §5c：subagent 内 part 的 sessionID 是子代理自己的 OC 会话 id
    （即 ai_chat_subtasks.id），ai_chat_sessions 查不到时回退子代理表
    映射到根会话，否则子代理的 skill 上报会被静默丢弃。"""
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
                if row:
                    return row[0]
    except Exception as e:
        logger.warning('platform session lookup failed %s: %s', session_id, e)
        return ''
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT root_session_id FROM ai_chat_subtasks "
                            "WHERE id = %s", (session_id,))
                row = cur.fetchone()
                return row[0] if row else ''
    except Exception as e:
        logger.warning('subtask session lookup failed %s: %s', session_id, e)
        return ''


def record_runtime_skill_event(payload: dict) -> dict:
    """OpenCode 插件上报 skill 工具调用（Spec P2：invoked → confirmed）。

    payload: {skillName, sessionID, messageID, partID, status, title}
    spec §5c：subagent 内 part 的 sessionID 是子代理自己的 OC 会话 id
    （即 ai_chat_subtasks.id）——命中子代理时归属其根会话、以 subtask_id
    标注，并在返回里带回 subtaskId；attempt 归属沿用 get_attempts 既有
    逻辑（子代理无独立 attempt 时落根会话的 attempt）。
    """
    skill = payload.get('skillName') or ''
    status = payload.get('status') or ''
    oc_id = payload.get('sessionID') or ''
    session_id = _platform_session_id(oc_id)
    subtask_id = None
    if oc_id and not oc_id.startswith('sess_'):
        # 子代理甄别：sessionID 可能是 ai_chat_subtasks.id。主映射无论命中
        # 与否都要核对子代理表——命中时以子代理归属优先（root_session_id
        # 覆盖 session_id 并标注 subtask_id），未命中则保持主映射结果。
        # 子代理 id 是主键至多一行，取首行即可。
        try:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT root_session_id FROM ai_chat_subtasks "
                                "WHERE id = %s", (oc_id,))
                    rows = cur.fetchall()
            if rows:
                session_id, subtask_id = rows[0][0], oc_id
        except Exception as e:
            logger.warning('subtask lookup failed %s: %s', oc_id, e)
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
                      evidence_refs=[ref], subtask_id=subtask_id)
    result = {'ok': True}
    if subtask_id:
        result['subtaskId'] = subtask_id
    return result


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
// P3-C3：tool.execute.before 钩子在每次工具调用前向 /ai/gate/internal/
// pre-check 询问 deny list——命中(mode='pre' 期望 + tool/args 匹配)时抛错
// 阻断该次调用（OpenCode 把钩子抛出的错误作为该工具调用的错误结果回给
// 模型）。门禁服务不可达/超时时一律放行（可用性优先，终态核对仍兜底）。
const ENDPOINT = process.env.BAIZE_RUNTIME_EVENT_URL || '__ENDPOINT__'
const TOKEN = process.env.BAIZE_INTERNAL_TOKEN || '__TOKEN__'
const GATE_URL = process.env.BAIZE_GATE_PRECHECK_URL || '__GATE_ENDPOINT__'
const HEADERS = { 'content-type': 'application/json', 'x-internal-token': TOKEN }
const GATE_TIMEOUT_MS = 3000
// (sessionID → expiry)。服务端返回 rules=0（该会话无任何 pre 规则）时短路
// 后续工具调用的 pre-check 请求——无规则是常态，热路径零开销。
const _gateEmpty = new Map()
const GATE_EMPTY_TTL_MS = 30000

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

// 与 Python 侧 agent_ledger.args_to_text 同形：对象压成 k=value 行,
// 字符串直用、其余 JSON 序列化——两侧正则匹配同一文本形状。
function gateArgsText(args) {
  try {
    if (args == null) return ''
    if (typeof args === 'string') return args
    if (typeof args === 'object') {
      return Object.keys(args).map(k => {
        const v = args[k]
        if (typeof v === 'string') return `${k}=${v}`
        try { return `${k}=${JSON.stringify(v)}` } catch { return `${k}=${String(v)}` }
      }).join('\\n')
    }
    return JSON.stringify(args)
  } catch { return String(args) }
}

async function gatePreCheck(sessionID, tool, argsText) {
  const emptyAt = _gateEmpty.get(sessionID)
  if (emptyAt) {
    if (Date.now() - emptyAt <= GATE_EMPTY_TTL_MS) return { allow: true }
    _gateEmpty.delete(sessionID)
  }
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), GATE_TIMEOUT_MS)
  try {
    const res = await fetch(GATE_URL, {
      method: 'POST',
      headers: HEADERS,
      signal: ctrl.signal,
      body: JSON.stringify({ sessionId: sessionID, tool, argsText }),
    })
    if (!res.ok) return { allow: true }   // 非 2xx（含 token 失配 403）→ 放行
    const body = await res.json()
    if (body && body.rules === 0) _gateEmpty.set(sessionID, Date.now())
    return body || { allow: true }
  } finally {
    clearTimeout(timer)
  }
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
    async 'tool.execute.before'(input, output) {
      if (!GATE_URL || !input || !input.tool || !input.sessionID) return
      let body = null
      try {
        body = await gatePreCheck(input.sessionID, input.tool,
                                  gateArgsText(output && output.args))
      } catch (e) {
        console.error(`[baize-trace] gate pre-check unreachable → allow ` +
          `tool=${input.tool}: ${e && e.message}`)
        return
      }
      if (body && body.allow === false) {
        const reason = body.reason || `门禁拦截了工具 ${input.tool} 的调用`
        console.error(`[baize-trace] gate DENY tool=${input.tool} ` +
          `session=${input.sessionID}: ${reason}`)
        // 抛错即阻断：OC 把该错误作为本次工具调用的结果回给模型
        throw new Error(reason)
      }
    },
  }
}
"""


def ensure_runtime_plugin(global_dir: str, endpoint: str, token: str = '',
                          gate_endpoint: str = '') -> str | None:
    """写入 <OPENCODE_GLOBAL_DIR>/plugin/baize-trace.js（幂等）。

    endpoint 与 internal token 都直接嵌入插件文件（serve 子进程环境不可靠，
    内嵌值保证开箱即用；BAIZE_RUNTIME_EVENT_URL / BAIZE_INTERNAL_TOKEN env
    优先级更高，便于部署侧覆写）。gate_endpoint 是 P3-C3 PreToolUse 校验
    端点（/ai/gate/internal/pre-check）；留空时插件模板里的占位符替换为
    空串，pre-check 钩子短路放行（等价于未启用 deny list 拦截）。
    内容变化会在下次启动时重写插件文件——OC serve 需重启才重新加载。"""
    import os
    if not global_dir:
        return None
    try:
        pdir = os.path.join(global_dir, 'plugin')
        os.makedirs(pdir, exist_ok=True)
        path = os.path.join(pdir, RUNTIME_PLUGIN_NAME)
        js = PLUGIN_JS.replace('__ENDPOINT__', endpoint or '') \
                      .replace('__TOKEN__', token or '') \
                      .replace('__GATE_ENDPOINT__', gate_endpoint or '')
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
