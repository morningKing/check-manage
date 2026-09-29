"""verifier 判官——门禁第 4 类原语的执行体（设计 §6-§9）。

方案 B：终态核对时在子会话工作区开一个隐藏 OpenCode 会话，平台部署的
baize-verifier agent（只读工具）按材料逐条核对 rubric，以 JSON 契约返回
verdict。会话用完即弃：不落 ai_chat_messages/ai_chat_subtasks/账本，
平台只保留 verdict 与证据引用（经 merge_verifier_results 进 gate 结果）。
判官材料：最终 assistant 回复全文 + 账本轨迹摘要（tool/args/state/时序，
含子代理行）+ 全部 verifier 期望的 rubric 清单。
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
你是动作门禁判官。用户消息里给出：待核对的期望清单（按名编号）、子任务的
最终回复全文、工具调用轨迹摘要。你的职责：

1. 逐条核对每条期望；需要文件证据时用 read/grep/list 在当前工作区取证；
2. 每条给 verdict（"passed" 或 "failed"）、reasons（中文，逐条、具体）、
   evidence（引用你实际打开的文件:行或轨迹条目；禁止凭空断言）；
3. 只输出一个 JSON 对象（最后输出），形如：
   {"results": [{"name": "<期望名>", "verdict": "passed|failed",
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


def collect_materials(session_id: str, get_db=None):
    """组装判官材料。无 verifier 期望 → None（调用方零开销跳过）。"""
    db_ctx = get_db or _default_get_db
    with db_ctx() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT name, effect_spec FROM action_expectations "
                "WHERE scope_id = %s AND scope_type IN ('session','tree') "
                "  AND check_type = 'verifier' ORDER BY id", (session_id,))
            rows = cur.fetchall()
            if not rows:
                return None
            checks = [{'name': r[0], 'rubric': (r[1] or {}).get('rubric', '')}
                      for r in rows]
            cur.execute(
                "SELECT content FROM ai_chat_messages "
                "WHERE session_id = %s AND role = 'assistant' "
                "ORDER BY seq DESC LIMIT 1", (session_id,))
            row = cur.fetchone()
            reply = ''
            if row:
                parts = row[0] or []
                reply = '\n'.join(p.get('text', '') for p in parts
                                  if isinstance(p, dict) and p.get('type') == 'text')
            cur.execute(
                "SELECT oc_session_id, tool, args_text, state, occurred_at "
                "FROM agent_tool_calls WHERE root_session_id = %s "
                "ORDER BY occurred_at, id LIMIT %s",
                (session_id, _MAX_TRACE_ROWS))
            trace = [{'oc': r[0], 'tool': r[1],
                      'args': (r[2] or '')[:_MAX_ARG_CHARS],
                      'state': r[3], 'at': r[4].isoformat() if r[4] else None}
                     for r in cur.fetchall()]
    return {'reply': reply[:_MAX_REPLY_CHARS], 'trace': trace, 'checks': checks}


def build_verifier_prompt(materials: dict) -> str:
    lines = ['# 待核对期望（每条都必须出现在 results 里）']
    for i, c in enumerate(materials['checks'], 1):
        lines.append(f"{i}. name: {c['name']}\n   rubric: {c['rubric']}")
    lines += ['# 子任务最终回复全文', materials['reply'] or '(空)']
    lines.append('# 工具调用轨迹（按时序；oc=会话, tool, args, state）')
    for t in materials['trace']:
        lines.append(f"- oc={t['oc']} tool={t['tool']} args={t['args']} "
                     f"state={t['state']} at={t['at']}")
    if not materials['trace']:
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
                        and e.get('verdict') in ('passed', 'failed')):
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


def _all_inconclusive(names, reason):
    return [{'name': n, 'kind': 'verifier', 'check_type': 'verifier',
             'status': 'inconclusive', 'reasons': [reason], 'evidence': '',
             'min_count': 1, 'effect_spec': None} for n in names]


def _abort_quiet(oc, v_oc, session_id):
    try:
        oc.abort_session(v_oc)
    except Exception as e:  # noqa: BLE001
        log.warning('verifier abort failed sid=%s: %s', session_id, e)


def run_verifier(session_id: str, workspace_path, model: str, *,
                 timeout_sec=None, stall_sec=None, client=None, get_db=None):
    """终态核对钩子的执行入口。无 verifier 期望 → None（调用方零改动）。
    返回 {'status': 'completed'|'error', 'results': [...], 'error': str|None}；
    status='error' 时 results 里全部条目为 inconclusive（fail-closed）。"""
    materials = collect_materials(session_id, get_db=get_db)
    if materials is None:
        return None
    # client=None 走生产默认构造（惰性工厂）；注入桩时 or 短路不触发构造。
    # 判官会话创建/轮询/abort 全程用这同一个实例（_abort_quiet 亦然）。
    oc = client or _default_client()
    names = [c['name'] for c in materials['checks']]
    timeout = timeout_sec if timeout_sec is not None else DEFAULT_TIMEOUT_SEC
    stall = stall_sec if stall_sec is not None else DEFAULT_STALL_SEC
    try:
        # agent 可用性预检：agent md 部署后需重启 OC serve 才被加载——serve 未
        # 重启时 send_prompt_async 会以默认 primary agent（含 write/bash/task）
        # 在受判工作区里跑判官轮。预检不过不建会话，直接故障路径 fail-closed。
        agents = oc.list_agents(directory=workspace_path) or []
        agent_names = {a.get('name') if isinstance(a, dict) else str(a)
                       for a in agents}
        if VERIFIER_AGENT_NAME not in agent_names:
            err = 'verifier agent 未在 OpenCode 注册（部署后需重启 OC serve）'
            log.warning('verifier agent missing sid=%s: %s', session_id, err)
            return {'status': 'error', 'error': err,
                    'results': _all_inconclusive(names, err)}
        v_oc = oc.create_session(directory=workspace_path,
                                 title=f'verifier:{session_id[:8]}')
        oc.send_prompt_async(v_oc, build_verifier_prompt(materials),
                             model=model or '', directory=workspace_path,
                             agent=VERIFIER_AGENT_NAME)
    except Exception as e:  # noqa: BLE001 —— 判官基础设施故障 → fail-closed
        log.warning('verifier session failed sid=%s: %s', session_id, e)
        return {'status': 'error', 'error': f'verifier 会话失败: {e}',
                'results': _all_inconclusive(names, f'verifier 会话失败: {e}')}
    deadline = time.monotonic() + timeout
    last_snapshot, last_change = None, time.monotonic()
    while True:
        now = time.monotonic()
        if now >= deadline:
            _abort_quiet(oc, v_oc, session_id)
            return {'status': 'error', 'error': f'verifier 轮询超时({timeout}s)',
                    'results': _all_inconclusive(names, 'verifier 轮询超时')}
        if now - last_change >= stall:
            _abort_quiet(oc, v_oc, session_id)
            return {'status': 'error', 'error': f'verifier 输出停滞({stall}s)',
                    'results': _all_inconclusive(names, 'verifier 输出停滞')}
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
        assistant = []
        for m in msgs:
            info = m.get('info') or {}
            if info.get('role') != 'assistant':
                continue
            assistant.append(m)
        if assistant:
            info = assistant[-1].get('info') or {}
            finish = info.get('finish')
            completed = (info.get('time') or {}).get('completed')
            finished = bool(completed) and finish not in ('tool-calls', 'tool_use')
            if finished:
                parts = assistant[-1].get('parts') or []
                text = '\n'.join(p.get('text', '') for p in parts
                                 if isinstance(p, dict) and p.get('type') == 'text')
                # 判官契约（JSON）出现才视为终判：finished 但未吐契约的文本
                # （如中间回合「思考中…」）继续轮询——契约提取与 parse_verdicts
                # 同一定义；判官停滞由上方 stall 看门狗兜底 abort。
                if re.search(r'\{[\s\S]*\}', text or ''):
                    break
        time.sleep(POLL_INTERVAL_SEC)
    parsed = parse_verdicts(text or '', names)
    if parsed['error']:
        return {'status': 'error', 'error': parsed['error'],
                'results': parsed['results'] + _all_inconclusive(
                    parsed['unresolved'], parsed['error'])}
    if parsed['unresolved']:
        return {'status': 'error', 'error': 'verifier 未覆盖全部期望（缺名）',
                'results': parsed['results'] + _all_inconclusive(
                    parsed['unresolved'], 'verifier 未覆盖该期望（缺名）')}
    return {'status': 'completed', 'error': None, 'results': parsed['results']}


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
