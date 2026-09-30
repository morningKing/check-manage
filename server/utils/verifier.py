"""verifier 判官——门禁第 4 类原语的执行体（设计 §6-§9）。

方案 B：终态核对时在子会话工作区开一个隐藏 OpenCode 会话，平台部署的
baize-verifier agent（只读工具）按材料逐条核对 rubric，以 JSON 契约返回
verdict。会话用完即弃：不落 ai_chat_messages/ai_chat_subtasks/账本，
平台只保留 verdict 与证据引用（经 merge_verifier_results 进 gate 结果）。

核对分组（2026-09-30，subagent 粒度）：
- 期望不带 subagents → 「全树组」：材料 = 子任务最终回复全文 + 全树工具
  调用轨迹（含子代理行），一次判官会话核对全部此类期望；
- 期望带 subagents（名单）→ 「定向组」：材料 = 名单内 agent 的全部子代理
  会话消息（ai_chat_subtask_messages，按 seq 时序）+ 这些子代理的工具调用
  轨迹；同一名单组合为一组、每组一次判官会话，verdict 即「该组材料满足
  rubric」。结果行携带 agent 标注（None=全树）。
判定时机不变：任务子会话每轮终态各核对一轮（子代理终态即判无增益——
verdict 最终仍要在任务终态聚合进 gate，提前判只会引入异步判官与轮询
路径的并发纠缠）。

轮询读取 OpenCode 原始消息形状 {'info': {...}, 'parts': [...]}——role/
finish/time 在 info 里、parts 在顶层；完成判定与 batch_engine 同口径
（time.completed 非空且 finish 非 continuation 'tool-calls'/'tool_use'）。
"""
import json
import logging
import os
import re
import time

log = logging.getLogger(__name__)

VERIFIER_AGENT_NAME = 'baize-verifier'
POLL_INTERVAL_SEC = 2.0
DEFAULT_TIMEOUT_SEC = int(os.getenv('AI_BATCH_VERIFIER_TIMEOUT_SEC', '180') or 180)
DEFAULT_STALL_SEC = int(os.getenv('AI_BATCH_VERIFIER_STALL_SEC', '60') or 60)
_MAX_REPLY_CHARS = 8000
_MAX_TRACE_ROWS = 100
_MAX_ARG_CHARS = 200
_MAX_SUBAGENT_MSG_ROWS = 40      # 每个子代理会话的消息封顶
_MAX_SUBAGENT_MSG_CHARS = 500    # 每条子代理消息文本截断

_AGENT_MD = """---
description: 平台判官：只读取证并逐条核对动作门禁 rubric（由平台自动部署）
mode: primary
tools:
  read: true
  grep: true
  list: true
  write: false
  edit: false
  bash: false
  task: false
  patch: false
---
你是动作门禁判官。用户消息里给出：待核对的期望清单（按名编号）、以及核对
材料（子任务最终回复全文 / 工具调用轨迹摘要 / 指定子代理的会话消息，视期望
而定）。你的职责：

1. 逐条核对每条期望；需要文件证据时用 read/grep/list 在当前工作区取证；
2. 每条给 verdict（"passed" / "failed" / "inconclusive"）、reasons（中文，逐条、具体）、
   evidence（引用你实际打开的文件:行或轨迹/消息条目；禁止凭空断言）。
   材料不足以核实某条时（例如 rubric 要求多次委派但材料只见一次，或要求的
   文件/消息不存在），verdict 给 "inconclusive" 并在 reasons 说明缺什么——
   不要凭不完整的材料下 failed 结论；
3. 只输出一个 JSON 对象（最后输出），形如：
   {"results": [{"name": "<期望名>", "verdict": "passed|failed|inconclusive",
                 "reasons": ["…"], "evidence": "…"}]}
   results 必须覆盖期望清单里的每一个 name，一个不多一个不少；
4. 不修改任何文件；JSON 输出后立即结束，不要追加任何文字。
"""


def ensure_verifier_agent(global_dir: str):
    """启动时部署 baize-verifier agent（幂等；内容不变不重写）。
    部署后需重启 OC serve 才被加载——与插件同一运维口径。"""
    if not global_dir:
        return None
    try:
        agent_dir = os.path.join(global_dir, 'agent')
        os.makedirs(agent_dir, exist_ok=True)
        path = os.path.join(agent_dir, f'{VERIFIER_AGENT_NAME}.md')
        if os.path.isfile(path):
            with open(path, 'r', encoding='utf-8') as f:
                if f.read() == _AGENT_MD:
                    return path
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(_AGENT_MD)
        log.info('verifier agent installed: %s', path)
        return path
    except Exception as e:  # noqa: BLE001 —— 部署失败不阻断启动
        log.warning('verifier agent deploy failed: %s', e)
        return None


def _default_get_db():
    from db import get_db
    return get_db()


def _default_client():
    """生产默认判官客户端：沿用仓库惯例惰性构造 OpenCodeClient(OPENCODE_BASE_URL)
    ——调用时才建，不在模块 import 时构造（同 batch_engine._client 与交互路径）。
    utils.opencode_client 是类模块而非客户端实例，不可直接当 client 用。"""
    from utils.opencode_client import OpenCodeClient
    from config import OPENCODE_BASE_URL
    return OpenCodeClient(OPENCODE_BASE_URL)


def _parts_text(content) -> str:
    """消息 content（jsonb parts 数组）→ text 片段拼接。"""
    parts = content or []
    return '\n'.join(p.get('text', '') for p in parts
                     if isinstance(p, dict) and p.get('type') == 'text')


def _group_key(subagents) -> tuple:
    """期望的分组键：无名单 = (None,)（全树组）；有名单 = 排序后的名字元组
    （同一名单组合为一组、一次判官会话，verdict 为该组材料的 all-of 判定）。"""
    if not subagents:
        return (None,)
    return tuple(sorted({str(a).strip() for a in subagents if str(a).strip()}))


def collect_materials(session_id: str, get_db=None):
    """组装判官材料并按核对粒度分组。无 verifier 期望 → None（零开销跳过）。

    返回 {'groups': [
      {'agents': None,                       # 全树组（至多一个）
       'checks': [{'name','rubric'}],
       'reply': str, 'trace': [...]},
      {'agents': ('dev',...),                # 定向组：材料限定到名单内子代理
       'checks': [...],
       'subagent_segments': [{'agent','subtask_id','messages':[{'role','text'}]}],
       'trace': [...]},
    ]}"""
    db_ctx = get_db or _default_get_db
    with db_ctx() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT name, effect_spec, subagents FROM action_expectations "
                "WHERE scope_id = %s AND scope_type IN ('session','tree') "
                "  AND check_type = 'verifier' ORDER BY id", (session_id,))
            rows = cur.fetchall()
            if not rows:
                return None
            buckets: dict = {}
            for name, effect_spec, subagents in rows:
                key = _group_key(subagents)
                buckets.setdefault(key, []).append(
                    {'name': name, 'rubric': (effect_spec or {}).get('rubric', '')})

            groups = []
            for key, checks in buckets.items():
                if key == (None,):
                    # 全树组：最终回复全文 + 全树轨迹（现状材料）
                    cur.execute(
                        "SELECT content FROM ai_chat_messages "
                        "WHERE session_id = %s AND role = 'assistant' "
                        "ORDER BY seq DESC LIMIT 1", (session_id,))
                    row = cur.fetchone()
                    reply = _parts_text(row[0]) if row else ''
                    cur.execute(
                        "SELECT oc_session_id, tool, args_text, state, occurred_at "
                        "FROM agent_tool_calls WHERE root_session_id = %s "
                        "ORDER BY occurred_at, id LIMIT %s",
                        (session_id, _MAX_TRACE_ROWS))
                    trace = _trace_rows(cur.fetchall())
                    groups.append({'agents': None, 'checks': checks,
                                   'reply': reply[:_MAX_REPLY_CHARS], 'trace': trace})
                    continue
                agents = list(key)
                # 定向组：名单内 agent 的全部子代理会话（可能多次委派多个）
                cur.execute(
                    "SELECT id, agent FROM ai_chat_subtasks "
                    "WHERE root_session_id = %s AND agent = ANY(%s) "
                    "ORDER BY created_at, id",
                    (session_id, agents))
                subtasks = cur.fetchall()
                segments, st_ids = [], []
                for st_id, st_agent in subtasks:
                    st_ids.append(st_id)
                    cur.execute(
                        "SELECT role, content FROM ai_chat_subtask_messages "
                        "WHERE subtask_id = %s ORDER BY seq LIMIT %s",
                        (st_id, _MAX_SUBAGENT_MSG_ROWS))
                    messages = [{'role': r[0],
                                 'text': _parts_text(r[1])[:_MAX_SUBAGENT_MSG_CHARS]}
                                for r in cur.fetchall()]
                    segments.append({'agent': st_agent, 'subtask_id': st_id,
                                     'messages': messages})
                trace = []
                if st_ids:
                    cur.execute(
                        "SELECT oc_session_id, tool, args_text, state, occurred_at "
                        "FROM agent_tool_calls WHERE root_session_id = %s "
                        "  AND subtask_id = ANY(%s) "
                        "ORDER BY occurred_at, id LIMIT %s",
                        (session_id, st_ids, _MAX_TRACE_ROWS))
                    trace = _trace_rows(cur.fetchall())
                groups.append({'agents': tuple(agents), 'checks': checks,
                               'subagent_segments': segments, 'trace': trace})
    return {'groups': groups}


def _trace_rows(rows) -> list:
    return [{'oc': r[0], 'tool': r[1],
             'args': (r[2] or '')[:_MAX_ARG_CHARS],
             'state': r[3], 'at': r[4].isoformat() if r[4] else None}
            for r in rows]


def build_verifier_prompt(group: dict) -> str:
    """按组渲染判官 prompt：全树组（回复+全树轨迹）vs 定向组（子代理会话
    消息+该组轨迹）。"""
    lines = ['# 待核对期望（每条都必须出现在 results 里）']
    if group['agents'] is not None:
        lines.append(f'（核对粒度：子代理 {"、".join(group["agents"])} 的执行）')
    for i, c in enumerate(group['checks'], 1):
        lines.append(f"{i}. name: {c['name']}\n   rubric: {c['rubric']}")

    if group['agents'] is None:
        lines += ['# 子任务最终回复全文', group['reply'] or '(空)']
    else:
        lines.append('# 指定子代理的会话消息（核对对象）')
        if not group['subagent_segments']:
            lines.append('(该名单下没有发现任何子代理会话——若 rubric 要求子代理'
                         '必须执行过，此为重要证据)')
        for seg in group['subagent_segments']:
            lines.append(f"## 子代理 {seg['agent']}（会话 {seg['subtask_id']}）")
            if not seg['messages']:
                lines.append('（无持久化消息）')
            for m in seg['messages']:
                lines.append(f"[{m['role']}] {m['text']}")

    lines.append('# 工具调用轨迹（按时序；oc=会话, tool, args, state）')
    for t in group['trace']:
        lines.append(f"- oc={t['oc']} tool={t['tool']} args={t['args']} "
                     f"state={t['state']} at={t['at']}")
    if not group['trace']:
        lines.append('(无工具调用记录)')
    lines.append('请逐条核对并只输出规定 JSON。')
    return '\n'.join(lines)


def parse_verdicts(text: str, expected_names: list) -> dict:
    """从判官回复提取 JSON 契约。缺名/非法 verdict/不可解析都不抛——
    返回 unresolved/error，由调用方按 inconclusive 处理（fail-closed）。"""
    by_name, err = {}, None
    candidates = re.findall(r'\{[\s\S]*\}', text or '')
    for cand in reversed(candidates):        # 契约要求最后输出——从后往前取
        try:
            data = json.loads(cand)
        except ValueError:
            continue
        entries = data.get('results') if isinstance(data, dict) else None
        if isinstance(entries, list):
            for e in entries:
                if (isinstance(e, dict) and e.get('name') in expected_names
                        and e.get('verdict') in ('passed', 'failed', 'inconclusive')):
                    by_name[e['name']] = e
            break
    else:
        err = 'verifier 回复不含可解析的 JSON 契约'
    results, unresolved = [], []
    for name in expected_names:
        e = by_name.get(name)
        if e is None:
            unresolved.append(name)
            continue
        results.append({'name': name, 'kind': 'verifier', 'check_type': 'verifier',
                        'status': e['verdict'],
                        'reasons': [str(x)[:200] for x in (e.get('reasons') or [])],
                        'evidence': str(e.get('evidence') or '')[:300],
                        'min_count': 1, 'effect_spec': None})
    return {'results': results, 'unresolved': unresolved, 'error': err}


def _agent_tag(group: dict):
    """结果行的 agent 标注：None=全树；定向组为名单逗号串。"""
    if group['agents'] is None:
        return None
    return ','.join(group['agents'])


def _verdict_row(name, verdict, reasons, evidence, agent):
    return {'name': name, 'kind': 'verifier', 'check_type': 'verifier',
            'status': verdict, 'reasons': reasons, 'evidence': evidence,
            'min_count': 1, 'effect_spec': None, 'agent': agent}


def _all_inconclusive(names, reason, agent=None):
    return [_verdict_row(n, 'inconclusive', [reason], '', agent) for n in names]


def _abort_quiet(oc, v_oc, session_id):
    try:
        oc.abort_session(v_oc)
    except Exception as e:  # noqa: BLE001
        log.warning('verifier abort failed sid=%s: %s', session_id, e)


def _agent_registered(oc, workspace_path) -> str | None:
    """agent 可用性预检：agent md 部署后需重启 OC serve 才被加载——serve 未
    重启时 send_prompt_async 会以默认 primary agent（含 write/bash/task）
    在受判工作区里跑判官轮。预检不过不建会话，直接故障路径 fail-closed。
    返回错误文案（None=可用）。"""
    agents = oc.list_agents(directory=workspace_path) or []
    agent_names = {a.get('name') if isinstance(a, dict) else str(a)
                   for a in agents}
    if VERIFIER_AGENT_NAME not in agent_names:
        return 'verifier agent 未在 OpenCode 注册（部署后需重启 OC serve）'
    return None


def _run_group(oc, group: dict, workspace_path, model: str, session_id: str,
               timeout: int, stall: int) -> dict:
    """单组一轮判官会话。返回 {'results': [...], 'error': str|None}；
    error 非空时 results 里该组全部条目为 inconclusive（fail-closed）。"""
    agent_tag = _agent_tag(group)
    names = [c['name'] for c in group['checks']]
    v_oc = None
    try:
        v_oc = oc.create_session(directory=workspace_path,
                                 title=f'verifier:{session_id[:8]}')
        oc.send_prompt_async(v_oc, build_verifier_prompt(group),
                             model=model or '', directory=workspace_path,
                             agent=VERIFIER_AGENT_NAME)
    except Exception as e:  # noqa: BLE001 —— 判官基础设施故障 → fail-closed
        log.warning('verifier session failed sid=%s: %s', session_id, e)
        return {'error': f'verifier 会话失败: {e}',
                'results': _all_inconclusive(names, f'verifier 会话失败: {e}',
                                             agent_tag)}
    deadline = time.monotonic() + timeout
    last_snapshot, last_change = None, time.monotonic()
    while True:
        now = time.monotonic()
        if now >= deadline:
            _abort_quiet(oc, v_oc, session_id)
            return {'error': f'verifier 轮询超时({timeout}s)',
                    'results': _all_inconclusive(names, 'verifier 轮询超时',
                                                 agent_tag)}
        if now - last_change >= stall:
            _abort_quiet(oc, v_oc, session_id)
            return {'error': f'verifier 输出停滞({stall}s)',
                    'results': _all_inconclusive(names, 'verifier 输出停滞',
                                                 agent_tag)}
        try:
            msgs = oc.get_messages(v_oc, directory=workspace_path) or []
        except Exception as e:  # noqa: BLE001 —— 轮询抖动继续重试，吃总预算
            log.warning('verifier poll error sid=%s: %s', session_id, e)
            msgs = []
        if isinstance(msgs, dict):
            # 兼容个别客户端实现直接回裸消息对象（非列表）——归一成单元素列表
            msgs = [msgs]
        snapshot = json.dumps(msgs, ensure_ascii=False, sort_keys=True)
        if snapshot != last_snapshot:
            last_snapshot, last_change = snapshot, now
        # 消息形状契约：{'info': {...}, 'parts': [...]}——role/finish/time 在
        # info、parts 在顶层。完成判定与 batch_engine.list_messages 同源
        # （_CONTINUATION_FINISH 口径）：time.completed 非空且 finish 不属于
        # continuation（'tool-calls'/'tool_use'）才视为回合终了。
        assistant = [m for m in msgs
                     if (m.get('info') or {}).get('role') == 'assistant']
        if assistant:
            info = assistant[-1].get('info') or {}
            finish = info.get('finish')
            completed = (info.get('time') or {}).get('completed')
            finished = bool(completed) and finish not in ('tool-calls', 'tool_use')
            if finished:
                parts = assistant[-1].get('parts') or []
                text = _parts_text(parts)
                # 判官契约（JSON）出现才视为终判：finished 但未吐契约的文本
                # （如中间回合「思考中…」）继续轮询——契约提取与 parse_verdicts
                # 同一定义；判官停滞由上方 stall 看门狗兜底 abort。
                if re.search(r'\{[\s\S]*\}', text or ''):
                    break
        time.sleep(POLL_INTERVAL_SEC)
    parsed = parse_verdicts(text or '', names)
    if parsed['error']:
        return {'error': parsed['error'],
                'results': parsed['results'] + _all_inconclusive(
                    parsed['unresolved'], parsed['error'], agent_tag)}
    if parsed['unresolved']:
        return {'error': 'verifier 未覆盖全部期望（缺名）',
                'results': parsed['results'] + _all_inconclusive(
                    parsed['unresolved'], 'verifier 未覆盖该期望（缺名）',
                    agent_tag)}
    for r in parsed['results']:
        r['agent'] = agent_tag
    return {'error': None, 'results': parsed['results']}


def run_verifier(session_id: str, workspace_path, model: str, *,
                 timeout_sec=None, stall_sec=None, client=None, get_db=None,
                 skip_names=None):
    """终态核对钩子的执行入口。无 verifier 期望 → None（调用方零改动）。
    按核对粒度分组逐组判定（每组一次判官会话、独立超时；组数通常 1~2）。
    `skip_names`：委派级门禁已提前判定的期望名（终态合并时跳过重复判官）。
    返回 {'status': 'completed'|'error', 'results': [...], 'error': str|None}；
    任一组 error 时整体 status='error'（该组条目已 inconclusive，fail-closed）。"""
    materials = collect_materials(session_id, get_db=get_db)
    if materials is None:
        return None
    if skip_names:
        skipped = set(skip_names)
        for g in materials['groups']:
            g['checks'] = [c for c in g['checks'] if c['name'] not in skipped]
        materials['groups'] = [g for g in materials['groups'] if g['checks']]
        if not materials['groups']:
            return None
    # client=None 走生产默认构造（惰性工厂）；注入桩时 or 短路不触发构造。
    # 判官会话创建/轮询/abort 全程用这同一个实例（_abort_quiet 亦然）。
    oc = client or _default_client()
    timeout = timeout_sec if timeout_sec is not None else DEFAULT_TIMEOUT_SEC
    stall = stall_sec if stall_sec is not None else DEFAULT_STALL_SEC
    try:
        # agent 可用性预检（一次；全组共用）——预检不过不建任何会话。
        err = _agent_registered(oc, workspace_path)
        if err:
            log.warning('verifier agent missing sid=%s: %s', session_id, err)
            names = [c['name'] for g in materials['groups'] for c in g['checks']]
            return {'status': 'error', 'error': err,
                    'results': _all_inconclusive(names, err)}
    except Exception as e:  # noqa: BLE001 —— 判官基础设施故障 → fail-closed
        log.warning('verifier precheck failed sid=%s: %s', session_id, e)
        names = [c['name'] for g in materials['groups'] for c in g['checks']]
        return {'status': 'error', 'error': f'verifier 会话失败: {e}',
                'results': _all_inconclusive(names, f'verifier 会话失败: {e}')}
    results, group_errors = [], []
    for group in materials['groups']:
        r = _run_group(oc, group, workspace_path, model, session_id,
                       timeout, stall)
        results.extend(r['results'])
        if r['error']:
            group_errors.append(r['error'])
    if group_errors:
        return {'status': 'error', 'error': '; '.join(group_errors)[:400],
                'results': results}
    return {'status': 'completed', 'error': None, 'results': results}


def merge_verifier_results(gate: dict, run: dict, session_id: str,
                           get_db=None) -> dict:
    """verdicts 合并进 gate：results 追加、overall 重算、期望行回写。
    任一 inconclusive → 整体 inconclusive（携带 error，引擎 fail-closed）；
    否则任一 failed → failed；否则 passed。"""
    db_ctx = get_db or _default_get_db
    gate['results'] = list(gate.get('results') or []) + list(run.get('results') or [])
    statuses = [r['status'] for r in gate['results']]
    if any(s == 'inconclusive' for s in statuses):
        gate['status'] = 'inconclusive'
        gate['expected'] = max(gate.get('expected') or 0, len(gate['results']))
        gate['error'] = run.get('error') or 'verifier 核对不可证实'
    elif any(s == 'failed' for s in statuses):
        gate['status'] = 'failed'
    else:
        gate['status'] = 'passed'
    with db_ctx() as conn:
        with conn.cursor() as cur:
            for r in run.get('results') or []:
                if r['status'] == 'inconclusive':
                    cur.execute(
                        "UPDATE action_expectations SET last_status='pending', "
                        "last_checked_at=NULL, last_evidence=NULL "
                        "WHERE scope_id=%s AND name=%s AND check_type='verifier'",
                        (session_id, r['name']))
                    continue
                cur.execute(
                    "UPDATE action_expectations SET last_status=%s, "
                    "last_checked_at=now(), last_evidence=%s "
                    "WHERE scope_id=%s AND name=%s AND check_type='verifier'",
                    (r['status'], 1 if r['status'] == 'passed' else 0,
                     session_id, r['name']))
    return gate
