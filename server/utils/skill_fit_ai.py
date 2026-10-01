"""SkillOpt 任务拟合的 AI 能力层（设计 docs/design/ai/SkillOpt任务拟合设计.md §5/§5b）。

四个能力，共用一条 AI 设置通道（同 action_check_extractor：
get_ai_settings / get_http_session，失败 RuntimeError → 路由层转 502）：

  generate_steps(definition_text)   定义全文 → fit.steps 草案（{"steps":[…]} JSON）
  apply_steps(path, steps)          回写 frontmatter fit.steps（保留其余字段与正文）
  preview_steps(steps, attempt_id)  建议 steps 对历史 attempt 轨迹的试算预览
                                    （纯计算不落库，spec §5「预览试算」）
  diagnose_result(result_id, ...)   partial/diverged 结果行的偏差诊断（结构化
                                    {cause, suggestions[], revised_steps}，落
                                    diagnosis 列；按 (result_id, def_hash,
                                    轨迹签名) 进程内缓存——轨迹变化缓存自然失效）

`_llm_json(system, user)` 是「LLM 调用 + JSON 对象解析」的内部薄函数，
generate/diagnose 共用——测试的打桩点即它。红线同提炼器：只产出建议/草案，
本模块没有执行侧路径。
"""
import hashlib
import json
import logging
import re
from collections import OrderedDict

import requests
import yaml

from utils.ai_query import get_ai_settings, get_http_session
from utils.agent_ledger import validate_pg_regex
from utils.skill_fit import match_steps
from db import get_db as _default_get_db

log = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(r'\A---\r?\n(.*?)\r?\n---\r?\n', re.S)

_MAX_DEF_CHARS = 8000
_MAX_PROMPT_CHARS = 8000
_TRACE_TAIL = 30                      # 诊断用户提示附带的轨迹尾部条数
_MAX_STEPS = 8

# 诊断缓存：(result_id, def_hash, trace_sig) → diagnosis。定义 hash 或轨迹
# 任一变化 key 即变——重新拟合后旧缓存自然失效。有界，防长驻进程膨胀。
_DIAG_CACHE_MAX = 128
_diag_cache: 'OrderedDict[tuple, dict]' = OrderedDict()

# cause 枚举（设计 §5b）。非法值在 diagnose 内拒绝（RuntimeError → 502）。
_DIAG_CAUSES = ('definition_stale', 'step_redundant', 'order_deviation',
                'model_noncompliance', 'environment')


# ── AI 通道薄函数：调用 + 解析（测试打桩点） ─────────────────────────────

def _extract_json_object(text: str) -> dict:
    """从模型回复里抠出 JSON 对象（容忍 ```json 围栏与前后废话）。"""
    text = (text or '').strip()
    fence = re.search(r'```(?:json)?\s*(\{.*\})\s*```', text, re.S)
    if fence:
        text = fence.group(1)
    else:
        start, end = text.find('{'), text.rfind('}')
        if start == -1 or end <= start:
            raise ValueError('模型回复中未找到 JSON 对象')
        text = text[start:end + 1]
    obj = json.loads(text)
    if not isinstance(obj, dict):
        raise ValueError('模型回复不是 JSON 对象')
    return obj


def _llm_json(system: str, user: str) -> dict:
    """一次 LLM 调用并解析 JSON 对象。generate/diagnose 共用的打桩点。

    Raises RuntimeError: AI 未启用/未配 Key/调用失败/回复不可解析（路由层转 502）。"""
    cfg = get_ai_settings()
    if not cfg['enabled']:
        raise RuntimeError('AI 功能未启用，请在系统配置中开启')
    if not cfg['apiKey']:
        raise RuntimeError('AI 服务未配置 API Key')
    payload = json.dumps({
        'model': cfg['model'],
        'messages': [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user},
        ],
        'temperature': 0.1,
        'max_tokens': max(int(cfg.get('maxTokens') or 1024), 1024),
    }).encode('utf-8')
    headers = {'Content-Type': 'application/json',
               'Authorization': f"Bearer {cfg['apiKey']}"}
    try:
        resp = get_http_session().post(cfg['endpoint'], data=payload,
                                       headers=headers, timeout=cfg['timeout'])
    except requests.RequestException as e:
        raise RuntimeError(f'AI 服务连接失败: {e}')
    if resp.status_code >= 400:
        raise RuntimeError(f'AI 服务请求失败 ({resp.status_code}): {resp.text[:200]}')
    try:
        content = resp.json()['choices'][0]['message']['content']
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f'AI 服务返回格式异常: {e}')
    try:
        return _extract_json_object(content)
    except (ValueError, json.JSONDecodeError) as e:
        raise RuntimeError(f'AI 回复不可解析: {e}') from e


# ── 生成器（§5）：定义全文 → fit.steps 草案 ─────────────────────────────

_GENERATE_SYSTEM = """你是 AI 技能定义的"步骤生成器"。给定技能/代理定义全文,产出 \
fit.steps 步骤草案:该类任务**必经的、可用工具调用记录验证**的动作序列。

只输出 JSON 对象,不要多余文字,形如:
{"steps": [{"id": "clone", "name": "克隆仓库", "expect": [{"tool": "bash", \
"args_pattern": "git clone\\\\s+\\\\S+"}]}]}

规则:
1. steps 按执行顺序排列,宁缺勿滥,最多 8 步;id 用短英文 slug,name 用简短中文;
2. expect 是该步的期望工具调用数组:tool 是 OpenCode 工具名(bash/read/write/edit/\
grep/glob),args_pattern 是 POSIX 正则,匹配工具入参拼接文本(形如 "command=git clone …"、\
"filePath=C:\\…\\a.md"),所以要匹配值本身而不是 key;文件名/路径中的点要转义;
3. 不虚构定义里不存在的仓库/脚本/文件;无法用工具调用验证的语义要求不入步;
4. 只输出 JSON,以 { 开头、} 结尾。"""


def generate_steps(definition_text: str) -> list[dict]:
    """从定义全文生成 fit.steps 草案。解析失败/形状非法 → RuntimeError。"""
    raw = _llm_json(_GENERATE_SYSTEM,
                    f"# 定义全文\n{(definition_text or '').strip()[:_MAX_DEF_CHARS]}")
    steps = raw.get('steps') if isinstance(raw, dict) else None
    if not isinstance(steps, list) or not steps:
        raise RuntimeError('生成结果缺少 steps 数组')
    if len(steps) > _MAX_STEPS:
        raise RuntimeError(f'steps 超过上限 {_MAX_STEPS} 步')
    for i, s in enumerate(steps):
        if not isinstance(s, dict) or not str(s.get('id') or '').strip():
            raise RuntimeError(f'steps[{i}] 必须是含 id 的对象')
        expect = s.get('expect') or []
        if not isinstance(expect, list):
            raise RuntimeError(f'steps[{i}].expect 必须是数组')
        for e in expect:
            if not isinstance(e, dict) or not str(e.get('tool') or '').strip():
                raise RuntimeError(f'steps[{i}].expect 项必须是含 tool 的对象')
    return steps


# ── 回写（§5）：合并 frontmatter fit.steps，保留其余字段与正文 ────────────

def apply_steps(path: str, steps: list[dict]) -> str:
    """把 steps 回写为定义文件 frontmatter 的 fit.steps（幂等覆盖）。

    先校验后写盘：任一 args_pattern 过不了 PG `~` 口径（validate_pg_regex）
    或 Python re 编译（终审 Fix 2：匹配引擎是 Python re——PG 合法而 Python
    非法的模式如 PG 独有断言 \\y，写盘后在匹配时才炸）→ ValueError 且
    **不写文件**；文件不存在 → FileNotFoundError（路由层 404）。
    返回文件路径。"""
    if not isinstance(steps, list):
        raise ValueError('steps 必须是数组')
    for i, s in enumerate(steps):
        if not isinstance(s, dict):
            raise ValueError(f'steps[{i}] 必须是对象')
        expect = s.get('expect') or []
        if not isinstance(expect, list):
            raise ValueError(f'steps[{i}].expect 必须是数组')
        for e in expect:
            if isinstance(e, dict) and e.get('args_pattern'):
                try:
                    validate_pg_regex(str(e['args_pattern']))
                except ValueError as ex:
                    raise ValueError(
                        f'steps[{i}] expect args_pattern {ex}') from ex
                try:
                    re.compile(str(e['args_pattern']))
                except re.error as ex:
                    raise ValueError(
                        f'steps[{i}] expect args_pattern 不是合法的 Python 正则'
                        f'（匹配引擎不兼容）: {ex}') from ex

    with open(path, encoding='utf-8') as f:
        text = f.read()
    meta: dict = {}
    body = text
    m = _FRONTMATTER_RE.match(text or '')
    if m:
        try:
            loaded = yaml.safe_load(m.group(1))
        except yaml.YAMLError as e:
            raise ValueError(f'原 frontmatter YAML 解析失败: {e}') from e
        if isinstance(loaded, dict):
            meta = loaded
        body = text[m.end():]
    fit = meta.get('fit')
    fit = dict(fit) if isinstance(fit, dict) else {}
    fit['steps'] = steps
    meta['fit'] = fit
    # width 拉满：长 args_pattern 被折叠换行会在重解析时折进空格，破坏正则
    dumped = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False,
                            default_flow_style=False, width=100000)
    with open(path, 'w', encoding='utf-8') as f:
        f.write('---\n' + dumped + '---\n' + body)
    return path


# ── 偏差诊断（§5b）：从库组装输入 → LLM 结构化诊断 → 落 diagnosis 列 ──────

_DIAGNOSE_SYSTEM = """你是 AI 任务执行的"偏差诊断器"。给定技能定义(fit.steps)、拟合 \
结果(per_step 证据)、实际工具调用轨迹尾部与任务 prompt,诊断"为什么该任务没有完全按 \
定义步骤执行"。

只输出 JSON 对象,不要多余文字,形如:
{"cause": "definition_stale", "suggestions": ["…"], \
"revised_steps": [{"id": "…", "name": "…", "expect": [{"tool": "bash", \
"args_pattern": "…"}]}]}

cause 只能取以下五类之一:
- definition_stale: 流程已变化,定义步骤该更新;
- step_redundant: 定义中存在多余/不必要的步骤;
- order_deviation: 动作都做了但次序与定义不一致;
- model_noncompliance: 模型未遵循定义(可能 prompt 不足);
- environment: 工具/环境报错导致未完成。

规则:
1. suggestions 是具体修改建议数组(改定义/改 prompt/改环境),2~5 条中文;
2. revised_steps 是修订后的完整 steps 草案(可直接回写 fit.steps),与定义同结构;
3. 不虚构轨迹中不存在的工具调用;args_pattern 用 POSIX 正则,只匹配值本身;
4. 只输出 JSON,以 { 开头、} 结尾。"""


def _load_fit_row(db_ctx, result_id: str) -> dict | None:
    with db_ctx() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, attempt_id, session_id, def_kind, def_name, "
                "       def_hash, status, score, per_step "
                "FROM ai_skill_fit_results WHERE id = %s", (result_id,))
            cols = [d[0] for d in cur.description]
            row = cur.fetchone()
    return dict(zip(cols, row)) if row else None


def _load_attempt_window(db_ctx, attempt_id: str) -> tuple:
    """attempt 的时窗；行缺失返回 (None, None)（轨迹查询自然为空）。"""
    with db_ctx() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT started_at, finished_at FROM ai_execution_attempts "
                "WHERE id = %s", (attempt_id,))
            row = cur.fetchone()
    return (row[0], row[1]) if row else (None, None)


def _load_trace(db_ctx, session_id, started_at, finished_at) -> list[dict]:
    """attempt 时窗内的账本工具调用时序（同 skill_fit._load_trace 口径；
    子代理调用落账带 root_session_id，天然含全部子代理；只取
    state='completed'——失败/在途调用不算「按定义执行」，否则污染
    preview 的贪心指针）。"""
    if not session_id:
        return []
    with db_ctx() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tool, args_text, occurred_at FROM agent_tool_calls "
                "WHERE root_session_id = %s "
                "  AND state = 'completed'\n"
                "  -- 只认 completed：失败/在途调用不算「按定义执行」（同 skill_fit 口径）\n"
                "  AND occurred_at BETWEEN %s AND COALESCE(%s, NOW()) "
                "ORDER BY occurred_at ASC NULLS LAST, id ASC",
                (session_id, started_at, finished_at))
            rows = cur.fetchall()
    return [{'tool': tool, 'args': (args_text or ''),
             'occurredAt': occurred_at.isoformat() if occurred_at else None}
            for (tool, args_text, occurred_at) in rows]


def _load_definition_text(db_ctx, attempt_id: str, def_kind: str,
                          def_name: str) -> str:
    """该结果行对应定义的全文（清单 path → 文件）。清单行/文件缺失 → ''
    （best-effort：诊断不因材料缺失整体失败）。"""
    with db_ctx() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT path FROM ai_execution_manifests "
                "WHERE attempt_id = %s AND kind = %s AND name = %s "
                "ORDER BY created_at, id LIMIT 1",
                (attempt_id, def_kind, def_name))
            row = cur.fetchone()
    if not row or not row[0]:
        return ''
    try:
        with open(row[0], encoding='utf-8', errors='replace') as f:
            return f.read(_MAX_DEF_CHARS)
    except OSError as e:
        log.warning('diagnose: 定义文件读取失败 %s: %s', row[0], e)
        return ''


def _load_task_prompt(db_ctx, attempt_id: str) -> str:
    """任务的 effective prompt 快照（无快照 → ''，best-effort 同上）。"""
    with db_ctx() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT effective_prompt FROM ai_execution_prompt_snapshots "
                "WHERE attempt_id = %s ORDER BY created_at DESC LIMIT 1",
                (attempt_id,))
            row = cur.fetchone()
    return (row[0] or '')[:_MAX_PROMPT_CHARS] if row and row[0] else ''


def _normalize_diagnosis(raw: dict) -> dict:
    cause = raw.get('cause')
    if cause not in _DIAG_CAUSES:
        raise RuntimeError(f'诊断 cause 非法: {cause!r}（只允许 '
                           f'{", ".join(_DIAG_CAUSES)}）')
    suggestions = raw.get('suggestions') if isinstance(
        raw.get('suggestions'), list) else []
    revised = raw.get('revised_steps')
    return {
        'cause': cause,
        'suggestions': [str(s) for s in suggestions if s],
        'revised_steps': revised if isinstance(revised, list) else [],
    }


def diagnose_result(result_id: str, get_db=None) -> dict:
    """对一条 partial/diverged 拟合结果做偏差诊断（设计 §5b）。

    输入材料按 result_id 从库组装（结果行 + 清单定义全文 + 任务 prompt +
    attempt 轨迹）；产出 {cause, suggestions[], revised_steps} 写入结果行
    diagnosis 列并返回。按 (result_id, def_hash, 轨迹签名) 进程内缓存——
    同签名直接返回缓存，不重复打 LLM；重新拟合后轨迹/def_hash 变化，缓存
    自然失效。

    Raises LookupError: 结果行不存在（路由层 404）；
           ValueError: 状态非 partial/diverged（路由层 400）；
           RuntimeError: AI 失败/cause 非法（路由层 502）。"""
    db_ctx = get_db or _default_get_db
    fit = _load_fit_row(db_ctx, result_id)
    if not fit:
        raise LookupError(f'拟合结果不存在: {result_id}')
    if fit['status'] not in ('partial', 'diverged'):
        raise ValueError(f'仅 partial/diverged 结果可诊断（当前 '
                         f'{fit["status"]}）')

    started_at, finished_at = _load_attempt_window(db_ctx, fit['attempt_id'])
    trace = _load_trace(db_ctx, fit['session_id'], started_at, finished_at)
    # 轨迹签名：tool+args 序列的聚合 hash（不含时间戳，签名稳定）
    digest = hashlib.sha256(json.dumps(
        [(t['tool'], t['args']) for t in trace], ensure_ascii=False)
        .encode('utf-8')).hexdigest()
    cache_key = (result_id, fit['def_hash'] or '', digest)
    hit = _diag_cache.get(cache_key)
    if hit is not None:
        _diag_cache.move_to_end(cache_key)
        return hit

    definition_text = _load_definition_text(
        db_ctx, fit['attempt_id'], fit['def_kind'], fit['def_name'])
    prompt = _load_task_prompt(db_ctx, fit['attempt_id'])
    user_parts = [
        f"# 定义全文（{fit['def_kind']}/{fit['def_name']}）\n"
        f"{definition_text.strip() or '（定义全文不可得）'}",
        f"# 拟合结果（status={fit['status']} score={fit['score']}）\n"
        f"{json.dumps(fit['per_step'] or [], ensure_ascii=False)}",
        f"# 实际工具调用轨迹尾部（时序）\n"
        f"{json.dumps(trace[-_TRACE_TAIL:], ensure_ascii=False) or '[]'}",
    ]
    if prompt:
        user_parts.append(f"# 任务 prompt\n{prompt}")
    raw = _llm_json(_DIAGNOSE_SYSTEM, '\n\n'.join(user_parts))
    diagnosis = _normalize_diagnosis(raw)

    with db_ctx() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE ai_skill_fit_results SET diagnosis = %s::jsonb "
                "WHERE id = %s",
                (json.dumps(diagnosis, ensure_ascii=False), result_id))
    _diag_cache[cache_key] = diagnosis
    while len(_diag_cache) > _DIAG_CACHE_MAX:
        _diag_cache.popitem(last=False)
    log.info('diagnose: result=%s cause=%s trace_sig=%s…',
             result_id, diagnosis['cause'], digest[:8])
    return diagnosis


# ── 预览试算（§5）：建议 steps vs 历史 attempt 轨迹，纯计算不落库 ─────────

def preview_steps(steps: list[dict], attempt_id: str, get_db=None) -> dict:
    """把建议的 steps 对某个历史 attempt 的实际轨迹做贪心匹配（Task 2 的
    match_steps），返回 per_step/steps_total/steps_hit/score/status 预览。
    纯试算——不写任何表。

    Raises ValueError: steps 形状非法（缺 id / expect 非数组）或 args_pattern
           不是合法的 Python 正则（终审 Fix 2，路由层 400）；
           LookupError: attempt 不存在（路由层 404）。"""
    if not isinstance(steps, list) or not steps:
        raise ValueError('steps 必须是非空数组')
    for i, s in enumerate(steps):
        if not isinstance(s, dict) or not str(s.get('id') or '').strip():
            raise ValueError(f'steps[{i}] 必须是含 id 的对象')
        expect = s.get('expect') or []
        if not isinstance(expect, list):
            raise ValueError(f'steps[{i}].expect 必须是数组')
        for e in expect:
            if isinstance(e, dict) and e.get('args_pattern'):
                try:
                    re.compile(str(e['args_pattern']))
                except re.error as ex:
                    raise ValueError(
                        f'steps[{i}] expect args_pattern 不是合法的 Python 正则'
                        f'（匹配引擎不兼容）: {ex}') from ex

    db_ctx = get_db or _default_get_db
    with db_ctx() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT session_id, started_at, finished_at "
                "FROM ai_execution_attempts WHERE id = %s", (attempt_id,))
            row = cur.fetchone()
    if not row:
        raise LookupError(f'attempt 不存在: {attempt_id}')
    session_id, started_at, finished_at = row
    trace = _load_trace(db_ctx, session_id, started_at, finished_at)
    return match_steps(steps, trace)
