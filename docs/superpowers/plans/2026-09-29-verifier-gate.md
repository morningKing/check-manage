# 动作门禁 verifier 判官核对 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为动作门禁新增第四类期望 `check_type: 'verifier'`——终态核对时在子会话工作区开隐藏 OpenCode 会话，由只读的 `baize-verifier` agent 按自然语言 rubric 逐条判定「动作是否做对了」，失败理由驱动既有 gate_retry 修复闭环。

**Architecture:** 登记侧扩展现有 `action_expectations`（tool='verifier'/args_pattern=''，沿袭 file/db_record 的 tool=check_type 惯例，无 DDL）；核对侧在 `_run_one` 收尾段证据核对之后插入 `utils/verifier.py::run_verifier`（材料组装 → 隐藏 OC 会话 → 轮询 → JSON 契约解析），verdicts 合并进 gate results；故障走既有 inconclusive→fail-closed 政策，失败走既有 failed→gate_retry 分支。

**Tech Stack:** Flask + psycopg2（PG 真库测试）、OpenCode REST 客户端（`utils/opencode_client.py`）、pytest（Windows 下 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`）、Vitest + Element Plus（创建对话框）、Playwright（e2e）。

**Spec:** `docs/design/ai/AI动作门禁verifier判官核对设计.md`（本计划逐节实现该 spec；执行者两份都要读）

## Global Constraints

- 无 DB DDL：`action_expectations.tool/args_pattern` 是 NOT NULL 列，verifier 行落库 `tool='verifier'`、`args_pattern=''`（沿袭 M3 的 `tool = tool or check_type` 惯例）。
- 故障政策不得发明新语义：verifier 基础设施故障 → gate `inconclusive` → fail-closed（子任务 failed，`gate_status='inconclusive'`）；判官正常判 failed → gate `failed` → gate_retry/落 failed。
- 判官调用每子会话恒定一次（一次会话判全部 verifier 期望）；verifier 会话不落 `ai_chat_messages`/`ai_chat_subtasks`/`agent_tool_calls`。
- verifier agent 只读：`read/grep/list` 开，`write/edit/bash/task/patch` 全关。
- 环境变量默认值：`AI_BATCH_VERIFIER_TIMEOUT_SEC=180`、`AI_BATCH_VERIFIER_STALL_SEC=60`。
- 后端测试命令统一 `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/<file> -v`；测试连真实 dev PG（`db_conn` fixture）；打桩一律 monkeypatch，种子数据在用例清理段删除。
- 代码注释/报错文案用中文，风格随 `agent_ledger.py` 现有 docstring。
- e2e 直连后端 3002（同 `e2e/ai-full/ai-retry-failed.spec.ts`），需后端 + OpenCode(4096) 在线。
- 范围说明：前端**没有**独立的门禁结果展示组件（`gate_status/last_status` 零引用）——verifier 判定与理由经 `error_message`（Task 2 的 `gate_failure_message` 分形渲染）在既有错误展示处呈现，spec §10「按 kind 渲染」以此形态满足，不新建组件。

## File Structure

| 文件 | 动作 | 职责 |
|---|---|---|
| `server/utils/agent_ledger.py` | 修改 | verifier 类型校验/落库归一、`check_session_gate` 跳过 verifier 行、`_expectation_desc`/`gate_failure_message` 分支 |
| `server/utils/verifier.py` | 新建 | 材料组装、verifier prompt、`run_verifier`（轮询/超时/abort/解析）、verdicts 合并、`ensure_verifier_agent` 部署 |
| `server/utils/batch_engine.py` | 修改 | `_check_action_gate` 接线（1331 行调用点传参） |
| `server/app.py` | 修改 | 启动时部署 verifier agent（~202 行插件部署旁） |
| `server/utils/action_check_extractor.py` | 修改 | system prompt 建议 verifier 型 + 字段白名单加 `rubric` |
| `server/tests/test_agent_action_gate.py` | 修改 | 校验矩阵 + 跳过/渲染用例 |
| `server/tests/test_verifier_gate.py` | 新建 | verifier 单元（组装/轮询/解析/合并/部署幂等） |
| `server/tests/test_batch_engine.py` | 修改 | `_check_action_gate` 接线用例 |
| `server/tests/test_action_check_extractor.py` | 新建 | 提炼器 verifier 建议用例 |
| `src/components/ai-chat/CreateBatchDialog.vue` | 修改 | 类型选择 + rubric 输入 + payload |
| `src/components/ai-chat/__tests__/CreateBatchDialog.test.ts` | 修改 | payload 形状守卫 |
| `e2e/ai-full/ai-verifier-gate.spec.ts` | 新建 | 真实链路三用例（达标/不达标/gate_retry 闭环） |
| `CLAUDE.md` | 修改 | 门禁段落补一行 |

---

### Task 1: validate_checks 支持 verifier 类型

**Files:**
- Modify: `server/utils/agent_ledger.py:237`（VALID_CHECK_TYPES）与 `validate_checks`（~278-338）
- Test: `server/tests/test_agent_action_gate.py`

**Interfaces:**
- Consumes: 现有 `validate_checks` / `_validate_effect_spec` / `validate_pg_regex`。
- Produces: `validate_checks` 对 `{'name', 'check_type':'verifier', 'rubric'}` 返回归一化条目 `{name, tool:'verifier', args_pattern:'', require_state:'completed', min_count:1, scope:'tree', check_type:'verifier', effect_spec:{'rubric':str}, subagents:None, apply_to:None}`；非法入参抛 `ValueError`（路由层转 400）。

- [ ] **Step 1: 写失败测试**（追加到 `test_agent_action_gate.py` 末尾）

```python
# ---------------------------------------------------------------------------
# verifier 类型（判官核对，设计 docs/design/ai/AI动作门禁verifier判官核对设计.md §5）
# ---------------------------------------------------------------------------

def test_validate_verifier_check_ok():
    from utils.agent_ledger import validate_checks
    checks = validate_checks([{'name': '结论含标记', 'check_type': 'verifier',
                               'rubric': '最终回复必须包含 DONE-MARK'}])
    c = checks[0]
    assert c['check_type'] == 'verifier'
    assert c['tool'] == 'verifier' and c['args_pattern'] == ''
    assert c['effect_spec'] == {'rubric': '最终回复必须包含 DONE-MARK'}
    assert c['min_count'] == 1 and c['scope'] == 'tree'


def test_validate_verifier_rejects_tool_fields():
    from utils.agent_ledger import validate_checks
    base = {'name': 'x', 'check_type': 'verifier', 'rubric': 'r'}
    with pytest.raises(ValueError, match='tool'):
        validate_checks([dict(base, tool='bash')])
    with pytest.raises(ValueError, match='args_pattern'):
        validate_checks([dict(base, args_pattern='git.*')])
    with pytest.raises(ValueError, match='min_count'):
        validate_checks([dict(base, min_count=2)])


def test_validate_verifier_requires_rubric():
    from utils.agent_ledger import validate_checks
    with pytest.raises(ValueError, match='rubric'):
        validate_checks([{'name': 'x', 'check_type': 'verifier'}])
    with pytest.raises(ValueError, match='rubric'):
        validate_checks([{'name': 'x', 'check_type': 'verifier', 'rubric': 'x' * 2001}])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_agent_action_gate.py -k verifier -v`
Expected: FAIL（`check_type 只支持 ('tool', 'file', 'db_record')`）

- [ ] **Step 3: 最小实现**（`agent_ledger.py`）

`VALID_CHECK_TYPES` 改为：

```python
VALID_CHECK_TYPES = ('tool', 'file', 'db_record', 'verifier')
```

`validate_checks` 循环内、现有 `if check_type == 'tool':` 之前插入 verifier 分支，并把原 `if check_type == 'tool':` 降级为 `elif`（其后的 `else: tool = tool or check_type` 兜底保持不变——file/db_record 行为不变）：

```python
        if check_type == 'verifier':
            if tool or pattern:
                raise ValueError(f'action_checks[{i}].verifier 类型不接受 '
                                 f'tool/args_pattern（判定要点写在 rubric 里）')
            if c.get('min_count') not in (None, 1):
                raise ValueError(f'action_checks[{i}].verifier 类型不接受 min_count')
            rubric = (c.get('rubric') or '').strip()
            if not rubric:
                raise ValueError(f'action_checks[{i}].rubric 必填(verifier 类型)')
            if len(rubric) > 2000:
                raise ValueError(f'action_checks[{i}].rubric 超长(>2000 字)')
            tool = 'verifier'   # NOT NULL 列：沿袭 file/db_record 的 tool=check_type 惯例
            pattern = ''
            effect_spec = {'rubric': rubric}
            min_count = 1
        elif check_type == 'tool':
```

- [ ] **Step 4: 跑测试确认通过 + 全文件回归**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_agent_action_gate.py -v`
Expected: 全 PASS（31 旧 + 3 新）

- [ ] **Step 5: Commit**

```bash
git add server/utils/agent_ledger.py server/tests/test_agent_action_gate.py
git commit -m "feat(gate): 动作门禁期望新增 verifier 类型——rubric 登记与校验"
```

---

### Task 2: 证据核对跳过 verifier 行 + 失败文案分支

**Files:**
- Modify: `server/utils/agent_ledger.py`（`check_session_gate` 的 exps 循环 ~618 行、`_expectation_desc` ~701、`gate_failure_message` ~712）
- Test: `server/tests/test_agent_action_gate.py`

**Interfaces:**
- Consumes: Task 1 的归一化形状（verifier 行 tool='verifier'/args_pattern=''）。
- Produces: `check_session_gate` 对 verifier 行**不**做证据匹配（无 result 行、last_status 保持 'pending'——由 Task 4 钩子负责核对与回写）；`gate_failure_message` 对 `kind='verifier'` 的 result 行渲染 `name(判官未通过: reasons)`。

- [ ] **Step 1: 写失败测试**（追加；自带种子，不依赖文件内既有 helper）

```python
def _seed_gate_session(db_conn, user_id, *, oc_sid):
    """最小种子：批 + running 子会话（自清理由本文件的 user fixture 负责的
    用例直接调用；本 helper 供 verifier/gate 相关用例复用）。返回 sid。"""
    import uuid as _uuid
    from db import get_db
    bid, sid = str(_uuid.uuid4()), str(_uuid.uuid4())
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ai_chat_batches (id, user_id, name, prompt, total) "
                "VALUES (%s, %s, 'gate', 'p', 1)", (bid, user_id))
            cur.execute(
                "INSERT INTO ai_chat_sessions (id, user_id, status, batch_id, "
                "  batch_seq, opencode_session_id, session_token) "
                "VALUES (%s, %s, 'running', %s, 0, %s, %s)",
                (sid, user_id, bid, oc_sid, f'tok-{sid[:12]}'))
    return sid


def test_gate_check_skips_verifier_expectations(db_conn, user_id):
    """verifier 期望不参与证据匹配——否则 args_text ~ '' 恒真、verifier 行
    被误判成 tool 核对。它的核对由引擎钩子（utils/verifier）负责。"""
    from utils.agent_ledger import register_session_expectations, check_session_gate
    from db import get_db
    sid = _seed_gate_session(db_conn, user_id, oc_sid='oc-vskip')
    n = register_session_expectations(
        sid, [{'name': '语义达标', 'check_type': 'verifier', 'rubric': '回复包含 MARK'}],
        get_db=get_db)
    assert n == 1
    gate = check_session_gate(sid, ledger_healthy=True, get_db=get_db)
    assert gate['results'] == []                      # 跳过：不出 result、不报错
    with get_db().cursor() as cur:
        cur.execute("SELECT last_status FROM action_expectations WHERE scope_id=%s", (sid,))
        assert cur.fetchone()[0] == 'pending'          # 核对权在引擎钩子


def test_expectation_desc_and_failure_message_for_verifier():
    from utils.agent_ledger import _expectation_desc, gate_failure_message
    row = {'name': '结论含标记', 'kind': 'verifier', 'check_type': 'verifier',
           'status': 'failed', 'reasons': ['缺少 DONE-MARK', '证据不足'],
           'evidence': '', 'min_count': 1}
    assert '判官核对' in _expectation_desc(row)
    msg = gate_failure_message({'results': [row]})
    assert msg.startswith('action_gate: ')
    assert '结论含标记(判官未通过: 缺少 DONE-MARK; 证据不足)' in msg
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_agent_action_gate.py -k "skips_verifier or failure_message_for_verifier" -v`
Expected: 2 FAIL（现循环把 verifier 行当 tool 匹配 / desc 无分支）

- [ ] **Step 3: 最小实现**

`check_session_gate` 的 exps 解包循环体第一行后加：

```python
                    if check_type == 'verifier':
                        # 判官核对由引擎钩子（utils/verifier）负责：证据循环
                        # 跳过——verifier 行 args_pattern='' 会让 `~ ''` 恒真
                        continue
```

`_expectation_desc` 在 `if ct == 'file':` 之前加分支：

```python
    if ct == 'verifier':
        rubric = (r.get('effect_spec') or {}).get('rubric', '?')
        return f'判官核对: {rubric[:60]}'
```

`gate_failure_message` 整体替换为：

```python
def gate_failure_message(gate_result: dict) -> str:
    """把 failed 的核对结果压成子任务 error_message(action_gate: 前缀 +
    逐条缺失项),沿用对账器"带准确原因失败"的风格。verifier 条目渲染
    判官理由(经 _maybe_gate_retry 进定向修复提示)。"""
    missed = [r for r in gate_result.get('results', [])
              if r.get('status') == 'failed']
    parts = []
    for r in missed:
        if r.get('kind') == 'verifier':
            reasons = '; '.join(r.get('reasons') or [])[:200]
            parts.append(f"{r['name']}(判官未通过: {reasons})")
        else:
            parts.append(f"{r['name']}(需 {_expectation_desc(r)},"
                         f"实际命中 {r['evidence']}/{r['min_count']})")
    return 'action_gate: ' + '; '.join(parts)[:400]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_agent_action_gate.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add server/utils/agent_ledger.py server/tests/test_agent_action_gate.py
git commit -m "feat(gate): 证据核对跳过 verifier 行——判官理由进入失败文案"
```

---

### Task 3: utils/verifier.py——材料组装、run_verifier、结果合并、agent 部署

**Files:**
- Create: `server/utils/verifier.py`
- Test: `server/tests/test_verifier_gate.py`（新建）

**Interfaces:**
- Consumes: `utils.opencode_client`（`create_session/send_prompt_async/get_messages/abort_session`）、`utils.agent_ledger.register_session_expectations`、`db.get_db`。
- Produces:
  - `VERIFIER_AGENT_NAME = 'baize-verifier'`
  - `collect_materials(session_id: str, get_db=None) -> dict | None`：`{'reply': str, 'trace': list[dict], 'checks': [{'name','rubric'}]}`；无 verifier 期望 → `None`。
  - `build_verifier_prompt(materials: dict) -> str`
  - `parse_verdicts(text: str, expected_names: list) -> dict`：`{'results': [row...], 'unresolved': [name...], 'error': str|None}`；row=`{'name','kind':'verifier','status':'passed'|'failed','reasons':[str],'evidence':str,'min_count':1,'check_type':'verifier','effect_spec':None}`。
  - `run_verifier(session_id: str, workspace_path, model: str, *, timeout_sec=None, stall_sec=None, client=None, get_db=None) -> dict | None`：无 verifier 期望 → `None`；否则 `{'status':'completed'|'error','results':[row...]（inconclusive 条目含于其中）,'error':str|None}`。
  - `merge_verifier_results(gate: dict, run: dict, session_id: str, get_db=None) -> dict`：追加 results + overall 重算 + 回写 `action_expectations.last_status/last_evidence`（passed→1/failed→0，inconclusive→'pending' 重置）。
  - `ensure_verifier_agent(global_dir: str) -> str | None`：幂等写 `<global>/agent/baize-verifier.md`。

- [ ] **Step 1: 写失败测试**（新建 `server/tests/test_verifier_gate.py`）

```python
# -*- coding: utf-8 -*-
"""verifier 判官单元（设计 docs/design/ai/AI动作门禁verifier判官核对设计.md §6-§9）。
打桩 opencode_client；会话/期望行用真库种子并在 user fixture 里清理。"""
import json
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


@pytest.fixture
def user_id(db_conn):
    uid = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, username, password_hash, display_name, role) "
            "VALUES (%s, %s, 'x', %s, 'developer')",
            (uid, f'ver_{uid[:8]}', f'VER {uid[:8]}'),
        )
    db_conn.commit()
    yield uid
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_chat_sessions WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM ai_chat_batches WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM users WHERE id = %s", (uid,))
    db_conn.commit()


def _seed_child(db_conn, user_id, *, oc_sid, with_reply=True, with_trace=True):
    """种子批 + running 子会话 + （可选）assistant 回复与账本轨迹。返回 sid。"""
    from db import get_db
    bid, sid = str(uuid.uuid4()), str(uuid.uuid4())
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ai_chat_batches (id, user_id, name, prompt, total) "
                "VALUES (%s, %s, 'ver', 'p', 1)", (bid, user_id))
            cur.execute(
                "INSERT INTO ai_chat_sessions (id, user_id, status, batch_id, "
                "  batch_seq, opencode_session_id, workspace_path, session_token) "
                "VALUES (%s, %s, 'running', %s, 0, %s, %s, %s)",
                (sid, user_id, bid, oc_sid, f'C:\\ver-e2e\\{sid}', f'tok-{sid[:12]}'))
            if with_reply:
                cur.execute(
                    "INSERT INTO ai_chat_messages (id, session_id, role, content) "
                    "VALUES (%s, %s, 'assistant', %s::jsonb)",
                    (f'm-{sid[:8]}', sid, json.dumps(
                        [{'type': 'text', 'text': '结果已写入,包含 DONE-MARK'}])))
            if with_trace:
                cur.execute(
                    "INSERT INTO agent_tool_calls (oc_session_id, root_session_id, "
                    "  subtask_id, agent, part_id, tool, args_text, state) "
                    "VALUES (%s, %s, NULL, NULL, 'p1', 'write', 'filePath=out.md', 'completed')",
                    (oc_sid, sid))
    db_conn.commit()
    return sid


def _register(sid, name='结论含标记', rubric='回复包含 DONE-MARK'):
    from utils.agent_ledger import register_session_expectations
    from db import get_db
    return register_session_expectations(
        sid, [{'name': name, 'check_type': 'verifier', 'rubric': rubric}], get_db=get_db)


class _FakeClient:
    """按脚本回放的 OpenCode 客户端桩：script 逐次弹出，弹尽后回放 _last。"""
    def __init__(self, script, last=None):
        self.script = list(script)
        self._last = last or []
        self.created, self.sent, self.aborted = [], [], []

    def create_session(self, *, directory, title=''):
        self.created.append(directory)
        return 'oc-verifier-1'

    def send_prompt_async(self, oc, content, model='', directory='', agent='', agent_parts=None):
        self.sent.append((oc, agent, model, directory))

    def get_messages(self, oc, directory=''):
        return self.script.pop(0) if self.script else self._last

    def abort_session(self, oc, directory=''):
        self.aborted.append(oc)


def _finished(text):
    return [{'role': 'user', 'finished': True, 'parts': [{'type': 'text', 'text': 'judge'}]},
            {'role': 'assistant', 'finished': True, 'parts': [{'type': 'text', 'text': text}]}]


def test_collect_materials_and_noop(db_conn, user_id):
    from utils import verifier
    from db import get_db
    sid = _seed_child(db_conn, user_id, oc_sid='oc-m1')
    assert verifier.collect_materials(sid, get_db=get_db) is None  # 未登记期望
    _register(sid)
    m = verifier.collect_materials(sid, get_db=get_db)
    assert 'DONE-MARK' in m['reply'] and m['trace'][0]['tool'] == 'write'
    assert m['checks'] == [{'name': '结论含标记', 'rubric': '回复包含 DONE-MARK'}]


def test_run_verifier_completed(db_conn, user_id):
    from utils import verifier
    from db import get_db
    sid = _seed_child(db_conn, user_id, oc_sid='oc-m2')
    _register(sid)
    verdict = json.dumps({'results': [{'name': '结论含标记', 'verdict': 'passed',
                                       'reasons': ['原文包含'], 'evidence': '回复第 1 段'}]},
                         ensure_ascii=False)
    fake = _FakeClient([_finished('思考中…'), _finished(verdict)])
    run = verifier.run_verifier(sid, workspace_path='C:\\ver-e2e\\x', model='p/m',
                                client=fake, get_db=get_db)
    assert run['status'] == 'completed' and run['results'][0]['status'] == 'passed'
    assert fake.created == ['C:\\ver-e2e\\x']
    assert fake.sent[0][1] == 'baize-verifier' and fake.sent[0][2] == 'p/m'
    assert fake.aborted == []


def test_run_verifier_noop_without_expectations(db_conn, user_id):
    from utils import verifier
    from db import get_db
    sid = _seed_child(db_conn, user_id, oc_sid='oc-m3', with_reply=False, with_trace=False)
    fake = _FakeClient([])
    assert verifier.run_verifier(sid, workspace_path='x', model='p/m',
                                 client=fake, get_db=get_db) is None
    assert fake.created == []  # 未开 OC 会话（零开销路径）


def test_run_verifier_timeout_aborts(db_conn, user_id, monkeypatch):
    from utils import verifier
    from db import get_db
    sid = _seed_child(db_conn, user_id, oc_sid='oc-m4')
    _register(sid)
    fake = _FakeClient([{'role': 'assistant', 'finished': False, 'parts': []}])
    monkeypatch.setattr(verifier, 'POLL_INTERVAL_SEC', 0.01)
    run = verifier.run_verifier(sid, workspace_path='x', model='p/m',
                                timeout_sec=0.05, client=fake, get_db=get_db)
    assert run['status'] == 'error'
    assert fake.aborted == ['oc-verifier-1']
    assert all(r['status'] == 'inconclusive' for r in run['results'])


def test_parse_verdicts_contract():
    from utils.verifier import parse_verdicts
    ok = parse_verdicts(
        '前置说明\n{"results": [{"name": "a", "verdict": "failed", "reasons": ["缺"], "evidence": "-"}]}',
        ['a', 'b'])
    assert ok['results'][0]['status'] == 'failed'
    assert ok['unresolved'] == ['b']                       # 缺名 → inconclusive
    bad = parse_verdicts('完全不是 JSON', ['a'])
    assert bad['error'] and all(r['status'] == 'inconclusive' for r in bad['results'])


def test_merge_verifier_results_status_and_persistence(db_conn, user_id):
    from utils import verifier
    from db import get_db
    sid = _seed_child(db_conn, user_id, oc_sid='oc-m5')
    _register(sid)
    gate = {'status': 'passed', 'results': []}
    run = {'status': 'completed', 'error': None, 'results': [
        {'name': '结论含标记', 'kind': 'verifier', 'status': 'failed',
         'reasons': ['缺少 DONE-MARK'], 'evidence': '全篇未见', 'min_count': 1,
         'check_type': 'verifier', 'effect_spec': {'rubric': '回复包含 DONE-MARK'}}]}
    merged = verifier.merge_verifier_results(gate, run, sid, get_db=get_db)
    assert merged['status'] == 'failed' and merged['results'][0]['kind'] == 'verifier'
    with get_db().cursor() as cur:
        cur.execute("SELECT last_status, last_evidence FROM action_expectations WHERE scope_id=%s", (sid,))
        assert cur.fetchone() == ('failed', 0)


def test_ensure_verifier_agent_idempotent(tmp_path):
    from utils.verifier import ensure_verifier_agent, VERIFIER_AGENT_NAME
    p1 = ensure_verifier_agent(str(tmp_path))
    assert p1 and os.path.isfile(p1) and VERIFIER_AGENT_NAME in p1
    first = open(p1, encoding='utf-8').read()
    mtime1 = os.path.getmtime(p1)
    assert ensure_verifier_agent(str(tmp_path)) == p1
    assert os.path.getmtime(p1) == mtime1 and open(p1, encoding='utf-8').read() == first
    assert 'mode: primary' in first and 'task: false' in first
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_verifier_gate.py -v`
Expected: FAIL（`No module named 'utils.verifier'`）

- [ ] **Step 3: 实现 `server/utils/verifier.py`**

```python
"""verifier 判官——门禁第 4 类原语的执行体（设计 §6-§9）。

方案 B：终态核对时在子会话工作区开一个隐藏 OpenCode 会话，平台部署的
baize-verifier agent（只读工具）按材料逐条核对 rubric，以 JSON 契约返回
verdict。会话用完即弃：不落 ai_chat_messages/ai_chat_subtasks/账本，
平台只保留 verdict 与证据引用（经 merge_verifier_results 进 gate 结果）。
判官材料：最终 assistant 回复全文 + 账本轨迹摘要（tool/args/state/时序，
含子代理行）+ 全部 verifier 期望的 rubric 清单。
"""
import json
import logging
import os
import re
import time

from utils import opencode_client

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
    oc = client or opencode_client
    names = [c['name'] for c in materials['checks']]
    timeout = timeout_sec if timeout_sec is not None else DEFAULT_TIMEOUT_SEC
    stall = stall_sec if stall_sec is not None else DEFAULT_STALL_SEC
    try:
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
        snapshot = json.dumps(msgs, ensure_ascii=False, sort_keys=True)
        if snapshot != last_snapshot:
            last_snapshot, last_change = snapshot, now
        assistant = [m for m in msgs if m.get('role') == 'assistant']
        if assistant and assistant[-1].get('finished'):
            parts = assistant[-1].get('parts') or []
            text = '\n'.join(p.get('text', '') for p in parts
                             if isinstance(p, dict) and p.get('type') == 'text')
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_verifier_gate.py -v`
Expected: 7 PASS

- [ ] **Step 5: Commit**

```bash
git add server/utils/verifier.py server/tests/test_verifier_gate.py
git commit -m "feat(gate): verifier 判官执行体——材料组装/隐藏会话轮询/契约解析/结果合并"
```

---

### Task 4: 引擎接线——_check_action_gate 扩展

**Files:**
- Modify: `server/utils/batch_engine.py:1331`（调用点）与 `_check_action_gate`（~1570）
- Test: `server/tests/test_batch_engine.py`

**Interfaces:**
- Consumes: Task 3 的 `verifier.run_verifier` / `verifier.merge_verifier_results` / `verifier.collect_materials` / `verifier._all_inconclusive`。
- Produces: `_check_action_gate(self, sid, *, workspace_path=None, model=None) -> dict`——返回的 gate 已含 verifier 条目；新参带默认值，向后兼容。

- [ ] **Step 1: 写失败测试**（追加到 `test_batch_engine.py`；顶部补 `from utils import agent_ledger, verifier`）

```python
def _seed_gate_child(db_conn, user_id, *, oc_sid, with_reply=False):
    """最小种子：批 + running 子会话（+可选 assistant 回复）。返回 sid。"""
    import uuid as _uuid
    import json as _json
    from db import get_db
    bid, sid = str(_uuid.uuid4()), str(_uuid.uuid4())
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ai_chat_batches (id, user_id, name, prompt, total) "
                "VALUES (%s, %s, 'vgt', 'p', 1)", (bid, user_id))
            cur.execute(
                "INSERT INTO ai_chat_sessions (id, user_id, status, batch_id, "
                "  batch_seq, opencode_session_id, workspace_path, session_token) "
                "VALUES (%s, %s, 'running', %s, 0, %s, %s, %s)",
                (sid, user_id, bid, oc_sid, f'C:\\vgt\\{sid}', f'tok-{sid[:12]}'))
            if with_reply:
                cur.execute(
                    "INSERT INTO ai_chat_messages (id, session_id, role, content) "
                    "VALUES (%s, %s, 'assistant', %s::jsonb)",
                    (f'm-{sid[:8]}', sid, _json.dumps([{'type': 'text', 'text': 'r'}])))
    db_conn.commit()
    return sid


def test_check_action_gate_merges_verifier(db_conn, user_id, monkeypatch):
    from utils.batch_engine import BatchWorker
    import utils.verifier as verifier_mod
    from utils.agent_ledger import register_session_expectations, gate_failure_message
    from db import get_db
    w = BatchWorker()
    # 零开销路径：无 verifier 期望 → run_verifier 不被调用
    called = []
    monkeypatch.setattr(verifier_mod, 'run_verifier',
                        lambda *a, **kw: called.append(a) or None)
    sid0 = _seed_gate_child(db_conn, user_id, oc_sid='oc-vg0', with_reply=True)
    w._check_action_gate(sid0, workspace_path='C:\\vgt', model='p/m')
    assert called == []
    # 有 verifier 期望且判官判 failed → gate failed，理由进失败文案
    sid = _seed_gate_child(db_conn, user_id, oc_sid='oc-vg1', with_reply=True)
    register_session_expectations(
        sid, [{'name': '结论含标记', 'check_type': 'verifier', 'rubric': '回复包含 MARK'}],
        get_db=get_db)
    run = {'status': 'completed', 'error': None, 'results': [
        {'name': '结论含标记', 'kind': 'verifier', 'status': 'failed',
         'reasons': ['缺少 DONE-MARK'], 'evidence': '-', 'min_count': 1,
         'check_type': 'verifier', 'effect_spec': {'rubric': '回复包含 MARK'}}]}
    monkeypatch.setattr(verifier_mod, 'run_verifier', lambda *a, **kw: run)
    gate = w._check_action_gate(sid, workspace_path='C:\\vgt', model='p/m')
    assert gate['status'] == 'failed'
    assert any(r.get('kind') == 'verifier' for r in gate['results'])
    assert '判官未通过' in gate_failure_message(gate)


def test_check_action_gate_verifier_error_fail_closed(db_conn, user_id, monkeypatch):
    """verifier 基础设施故障 → gate inconclusive → 引擎既有分支落 failed。"""
    from utils.batch_engine import BatchWorker
    import utils.verifier as verifier_mod
    from utils.agent_ledger import register_session_expectations
    from db import get_db
    w = BatchWorker()
    sid = _seed_gate_child(db_conn, user_id, oc_sid='oc-vg2', with_reply=True)
    register_session_expectations(
        sid, [{'name': '结论含标记', 'check_type': 'verifier', 'rubric': '回复包含 MARK'}],
        get_db=get_db)
    monkeypatch.setattr(verifier_mod, 'run_verifier', lambda *a, **kw: {
        'status': 'error', 'error': 'verifier 轮询超时(180s)',
        'results': [{'name': '结论含标记', 'kind': 'verifier', 'status': 'inconclusive',
                     'reasons': ['verifier 轮询超时(180s)'], 'evidence': '',
                     'min_count': 1, 'check_type': 'verifier', 'effect_spec': None}]})
    gate = w._check_action_gate(sid, workspace_path='C:\\vgt', model='p/m')
    assert gate['status'] == 'inconclusive' and gate.get('error')
```

（若 `test_batch_engine.py` 已有同形 helper 则复用；种子行的 `session_token` 唯一约束用 sid 片段保证。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_batch_engine.py -k "check_action_gate" -v`
Expected: FAIL（`_check_action_gate` 不接受 workspace_path/model 关键字）

- [ ] **Step 3: 实现**

模块顶部 import 区（`from utils import agent_ledger` 旁）加：

```python
from utils import verifier
```

`_check_action_gate` 整体替换为：

```python
    def _check_action_gate(self, sid: str, *, workspace_path=None, model=None) -> dict:
        """终态核对。inconclusive(账本不健康/查询异常/判官故障)不算新语义:
        统一走既有 fail-closed 分支,任务落 failed、期望行与日志留痕。
        verifier 期望由判官核对(utils/verifier,方案 B)——无 verifier 期望时
        run_verifier 返回 None,零开销跳过。"""
        healthy = self._ledger_health.pop(sid, True)
        result = agent_ledger.check_session_gate(sid, ledger_healthy=healthy,
                                                 get_db=get_db)
        if healthy:
            try:
                run = verifier.run_verifier(sid, workspace_path=workspace_path,
                                            model=model, get_db=get_db)
            except Exception as e:  # noqa: BLE001 —— 钩子自身异常双保险
                log.warning('verifier hook crashed sid=%s: %s', sid, e)
                mats = verifier.collect_materials(sid, get_db=get_db) or {}
                run = {'status': 'error', 'error': str(e)[:300],
                       'results': verifier._all_inconclusive(
                           [c['name'] for c in mats.get('checks', [])],
                           f'verifier 异常: {str(e)[:200]}')}
            if run is not None:
                result = verifier.merge_verifier_results(result, run, sid, get_db=get_db)
        if result['status'] == 'inconclusive':
            logger.warning('action gate inconclusive sid=%s: %s',
                           sid, result.get('error') or healthy is False)
        return result
```

1331 行调用点改为（`ws`/`model` 是 `_run_one` 既有局部变量）：

```python
            gate = self._check_action_gate(sid, workspace_path=ws, model=model)
```

- [ ] **Step 4: 跑测试确认通过 + 引擎回归**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_batch_engine.py tests/test_batch_execution_safety_p0.py -v`
Expected: 全 PASS（gate 分支对无 verifier 期望的既有用例零影响）

- [ ] **Step 5: Commit**

```bash
git add server/utils/batch_engine.py server/tests/test_batch_engine.py
git commit -m "feat(gate): 终态核对接入 verifier 判官——故障沿 fail-closed 政策"
```

---

### Task 5: 启动时部署 baize-verifier agent

**Files:**
- Modify: `server/app.py`（~202 行 `ensure_subagent_reuse_plugin` 调用旁）
- Test: `server/tests/test_verifier_gate.py::test_ensure_verifier_agent_idempotent`（Task 3 已覆盖；本任务只接线）

**Interfaces:**
- Consumes: `verifier.ensure_verifier_agent(global_dir)`。

- [ ] **Step 1:** 读 `app.py` 190-210 行，确认 `_OGD` 与 `ensure_subagent_reuse_plugin` 的调用形态与变量名。
- [ ] **Step 2:** 在该调用后追加：

```python
        try:
            from utils.verifier import ensure_verifier_agent
            ensure_verifier_agent(_OGD)
        except Exception:
            pass  # 判官 agent 部署失败不阻断启动（与插件同口径）；核对期走 fail-closed
```

（变量名以 Step 1 读到的实际代码为准——若全局目录变量不叫 `_OGD`，用实际名字。）

- [ ] **Step 3: 验证**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_verifier_gate.py::test_ensure_verifier_agent_idempotent -v && python -c "import app; print('app import ok')"`
Expected: PASS + `app import ok`

- [ ] **Step 4: Commit**

```bash
git add server/app.py
git commit -m "feat(gate): 启动时部署 baize-verifier 判官 agent"
```

---

### Task 6: extract 预填器建议 verifier 期望

**Files:**
- Modify: `server/utils/action_check_extractor.py`（`_SYSTEM_PROMPT` ~24-38、`cleaned` 白名单 ~138）
- Test: `server/tests/test_action_check_extractor.py`（新建）

**Interfaces:**
- Consumes: Task 1 的 `validate_checks`（verifier 分支）。
- Produces: `extract_action_checks` 可返回 `{'name','check_type':'verifier','rubric'}` 建议（经 `validate_checks` 归一化）。

- [ ] **Step 1: 写失败测试**（新建 `server/tests/test_action_check_extractor.py`）

```python
# -*- coding: utf-8 -*-
"""提炼器 verifier 建议（设计 §10）。打桩 AI 设置与 HTTP 会话。"""
import json as _json
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _patch_ai(monkeypatch, content):
    import utils.action_check_extractor as ex
    monkeypatch.setattr(ex, 'get_ai_settings',
                        lambda: {'enabled': True, 'apiKey': 'k', 'endpoint': 'http://x',
                                 'model': 'm', 'timeout': 5, 'maxTokens': 1024})
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {'choices': [{'message': {'content': content}}]}
    monkeypatch.setattr(ex, 'get_http_session', lambda: MagicMock(post=lambda *a, **kw: resp))


def test_extract_suggests_verifier_check(monkeypatch):
    _patch_ai(monkeypatch, _json.dumps([
        {'name': '结论含标记', 'check_type': 'verifier', 'rubric': '回复必须包含 DONE-MARK'},
        {'name': '克隆仓库', 'check_type': 'tool', 'tool': 'bash',
         'args_pattern': 'git clone', 'min_count': 1},
    ], ensure_ascii=False))
    from utils.action_check_extractor import extract_action_checks
    checks = extract_action_checks('完成任务并汇报')
    v = [c for c in checks if c['check_type'] == 'verifier']
    assert len(v) == 1 and v[0]['effect_spec']['rubric'] == '回复必须包含 DONE-MARK'


def test_extract_verifier_without_rubric_dropped(monkeypatch):
    _patch_ai(monkeypatch, _json.dumps([{'name': '坏建议', 'check_type': 'verifier'}],
                                       ensure_ascii=False))
    from utils.action_check_extractor import extract_action_checks
    assert extract_action_checks('x') == []  # validate_checks 拒绝 → Runtime/ValueError 路径
```

（第二例：坏建议会让 `validate_checks` 抛 ValueError → `extract_action_checks` 转 RuntimeError——测试改用 `pytest.raises(RuntimeError)` 断言，二选一以实现为准：提炼器现有行为是整体 502。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_action_check_extractor.py -v`
Expected: FAIL（白名单剥掉 rubric → 校验失败）

- [ ] **Step 3: 实现**

`_SYSTEM_PROMPT` 规则 3 之后插入（原规则 4 顺延为 5）：

```
4. 语义/过程类要求(无法穷举为正则——"结论覆盖要点""必须先读后写")可建议 \
check_type="verifier"(rubric: ≤2000 字中文判定要点,写给判官看),最多 2 条,宁缺勿滥;
```

`cleaned` 循环的字段白名单元组加入 `'rubric'`：

```python
            for k in list(c.keys()):
                if k not in ('name', 'tool', 'args_pattern', 'min_count',
                             'scope', 'check_type', 'effect_spec', 'rubric'):
                    c.pop(k, None)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_action_check_extractor.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/utils/action_check_extractor.py server/tests/test_action_check_extractor.py
git commit -m "feat(gate): 动作期望建议支持 verifier 类型——语义要点提炼"
```

---

### Task 7: 创建对话框支持 verifier 期望编辑 + dry-run 端点守卫

**Files:**
- Modify: `src/components/ai-chat/CreateBatchDialog.vue`（gateChecks 定义 ~189、`_blank_check` ~198、编辑行模板 ~80-95、payload 组装 ~318-329）
- Modify: `server/routes/ai_chat_batches.py`（`gate_dry_run` ~219，见下方「dry-run 端点守卫」）
- Test: `src/components/ai-chat/__tests__/CreateBatchDialog.test.ts`、`server/tests/test_batch_routes.py`

**Interfaces:**
- Produces: 创建请求 `action_checks` 数组元素为 `{name, check_type:'tool', tool, args_pattern, min_count}` 或 `{name, check_type:'verifier', rubric}`；verifier 行不显示 tool/args_pattern/min_count/dry-run 控件。

- [ ] **Step 1: 写失败测试**（追加到 `CreateBatchDialog.test.ts`；mount 与交互写法先读该文件现有用例）

```ts
it('verifier 行提交 {name, check_type, rubric} 且不带 tool/args_pattern/min_count', async () => {
  // 1) mount（沿用本文件现有的创建流程挂载方式）
  // 2) 开启动作门禁 → 新增一行 → 类型选 verifier → name 填「结论含标记」、rubric 填「回复包含 MARK」
  // 3) 提交创建
  // 断言 create payload 的 action_checks 深等于：
  //   [{ name: '结论含标记', check_type: 'verifier', rubric: '回复包含 MARK' }]
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run src/components/ai-chat/__tests__/CreateBatchDialog.test.ts`
Expected: 新增用例 FAIL

- [ ] **Step 3: 实现**

- `gateChecks` 行形状：`{ name: string; check_type: 'tool' | 'verifier'; tool: string; args_pattern: string; min_count: number; subagents: string; rubric: string }`；`_blank_check()` 返回缺省 `check_type: 'tool', rubric: ''`。
- 编辑行模板：行首加类型 `ElSelect`（`tool`=工具调用断言 / `verifier`=判官核对）；`check_type==='verifier'` 时渲染 `<ElInput type="textarea" v-model="c.rubric" placeholder="判定要点（≤2000 字，写给判官核对）" />` 并隐藏 tool/args_pattern/min_count/dry-run 控件；tool 行渲染现状不变。
- payload 组装（~318-329）：verifier 行产出 `{ name, check_type: 'verifier', rubric: c.rubric.trim() }`，过滤条件 `c.name.trim() && c.rubric.trim()`；tool 行走现有形状并补 `check_type: 'tool'`，过滤条件不变。
- 提交前长度守卫：`c.rubric.length > 2000` 时 `ElMessage.error('判官核对要点超长（>2000 字）')` 并阻断提交。

**dry-run 端点守卫**（spec §10：verifier 条目「无法预演」要显式标注，不假装跑过）：

- Modify: `server/routes/ai_chat_batches.py`（`gate_dry_run` ~219）与 `server/tests/test_batch_routes.py`
- `gate_dry_run` 在 body 解析后加分支：

```python
    if (body.get('check_type') or '').strip() == 'verifier':
        return jsonify({'supported': False,
                        'note': 'verifier 类型需真实会话由判官核对,无法预演'})
```

- 对应测试（追加到 `test_batch_routes.py`）：

```python
def test_gate_dry_run_verifier_not_supported(setup_app, db_conn, tmp_path, monkeypatch):
    client, admin_headers = setup_app
    monkeypatch.setenv('AI_CHAT_WORKSPACE_ROOT', str(tmp_path))
    f = _stage_one(client, admin_headers, name='dr.txt', upload_session_id='u-dr')
    detail = client.post('/ai/chat/batches', json={'name': 'b', 'prompt': 'p', 'files': [f]},
                         headers=admin_headers).get_json()
    bid = detail['batch']['id']; sid = detail['sessions'][0]['id']
    r = client.post(f'/ai/chat/batches/{bid}/children/{sid}/gate/dry-run',
                    json={'check_type': 'verifier'}, headers=admin_headers)
    assert r.status_code == 200 and r.get_json()['supported'] is False
    client.delete(f'/ai/chat/batches/{bid}', headers=admin_headers)
```

- 验证：`cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_batch_routes.py -k verifier -v` → PASS（并入 Step 4 一并跑）。

- [ ] **Step 4: 跑测试确认通过 + 类型检查**

Run: `npx vitest run src/components/ai-chat/__tests__/CreateBatchDialog.test.ts && npx vue-tsc --noEmit`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/components/ai-chat/CreateBatchDialog.vue src/components/ai-chat/__tests__/CreateBatchDialog.test.ts server/routes/ai_chat_batches.py server/tests/test_batch_routes.py
git commit -m "feat(ui): 批任务创建对话框支持判官核对期望——类型选择与 rubric 编辑"
```

---

### Task 8: e2e 真实链路三用例

**Files:**
- Create: `e2e/ai-full/ai-verifier-gate.spec.ts`

**Interfaces:**
- Consumes: Task 1-5、7 全部落地；后端带新代码运行且重启过（verifier agent 已部署、OC 已加载）。

- [ ] **Step 1: 写 spec**（登录/staging/建批/轮询/清理骨架与 `ai-retry-failed.spec.ts` 完全同款：`adminToken2()`、`API='http://127.0.0.1:3002'`、`test.setTimeout(1_200_000)`、finally `DELETE ?stop=1`）

```ts
/**
 * 动作门禁 verifier 判官 端到端（真实链路，2026-09-29）。
 * 覆盖三个方向：
 *  1) 达标：prompt「把 uploads/in.txt 的原文完整复述一遍」，verifier rubric
 *     要求「最终回复必须包含输入文件的原文内容」→ completed 且无 action_gate 失败；
 *  2) 不达标：同 prompt，rubric 要求「最终回复必须包含字符串 VERIFIER-NO-MARK-XYZ」
 *     （正常任务不可能满足）→ failed，error_message 含「action_gate」与「判官未通过」；
 *  3) gate_retry 闭环：prompt「只复述 uploads/in.txt 的原文，不要写任何文件」，
 *     rubric 要求「工作区 outputs/report.md 必须存在且内容包含输入原文」（判官需
 *     主动取证——read/list 工作区）→ 第一轮 gate failed → 批级 gate_retry 开启时
 *     带理由续跑，第二轮模型补写文件 → 判官取证通过 → completed，retry_count ≥ 1。
 * 断言直连后端 3002（同 ai-retry-failed.spec.ts 的理由）。
 */
import { test, expect } from '@playwright/test'

const API = 'http://127.0.0.1:3002'

async function adminToken2(): Promise<string> {
  const r = await fetch(`${API}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: 'admin', password: 'admin123' }),
  })
  if (!r.ok) throw new Error(`login failed: ${r.status}`)
  return (await r.json()).token
}

test.setTimeout(1_200_000)

interface RunOpts {
  name: string
  prompt: string
  rubric: string
  gateRetry?: boolean
}

async function runBatch(token: string, opts: RunOpts): Promise<{ status: string; errMsg: string; retryCount: number }> {
  const HDRS = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
  const form = new FormData()
  form.append('file', new Blob([Buffer.from('VERIFIER-E2E-INPUT-42')], { type: 'text/plain' }), 'in.txt')
  form.append('upload_session_id', `vf-${Date.now()}`)
  const up = await fetch(`${API}/ai/chat/batches/staging/upload`, {
    method: 'POST', headers: { Authorization: `Bearer ${token}` }, body: form,
  })
  expect(up.status).toBe(201)
  const create = await fetch(`${API}/ai/chat/batches`, {
    method: 'POST', headers: HDRS,
    body: JSON.stringify({
      name: `AITEST-vf-e2e-${Date.now()}`,
      prompt: opts.prompt,
      gate_retry: opts.gateRetry ?? false,
      action_checks: [{ name: '语义核对', check_type: 'verifier', rubric: opts.rubric }],
      files: [await up.json()],
    }),
  })
  expect(create.status).toBe(201)
  const d = await create.json()
  const bid = d.batch.id as string
  const sid = d.sessions[0].id as string
  try {
    const deadline = Date.now() + 900_000
    while (Date.now() < deadline) {
      const dd = await (await fetch(`${API}/ai/chat/batches/${bid}`, { headers: HDRS })).json()
      if (['completed', 'partial', 'failed'].includes(dd.batch.status)) {
        const s = dd.sessions[0]
        return { status: s.status, errMsg: String(s.error_message || ''), retryCount: Number(s.retry_count ?? 0) }
      }
      await new Promise(rr => setTimeout(rr, 3000))
    }
    throw new Error('子任务未在时限内达到终态')
  } finally {
    await fetch(`${API}/ai/chat/batches/${bid}?stop=1`, {
      method: 'DELETE', headers: { Authorization: `Bearer ${token}` },
    })
  }
}

test('verifier 门禁：达标 → completed 无失败', async () => {
  const token = await adminToken2()
  const r = await runBatch(token, {
    name: 'ok',
    prompt: '把 uploads/in.txt 的原文完整复述一遍,不要做其他事。',
    rubric: '最终回复必须包含输入文件 uploads/in.txt 的原文内容(VERIFIER-E2E-INPUT-42)。',
  })
  expect(r.status).toBe('completed')
  expect(r.errMsg).not.toContain('action_gate')
})

test('verifier 门禁：不达标 → failed 且 error_message 带判官理由', async () => {
  const token = await adminToken2()
  const r = await runBatch(token, {
    name: 'bad',
    prompt: '把 uploads/in.txt 的原文完整复述一遍,不要做其他事。',
    rubric: '最终回复必须包含字符串 VERIFIER-NO-MARK-XYZ(正常任务不可能满足,用于验证判官拦截)。',
  })
  expect(r.status).toBe('failed')
  expect(r.errMsg).toContain('action_gate')
  expect(r.errMsg).toContain('判官未通过')
})

test('verifier 门禁：gate_retry 闭环——判官理由驱动修复续跑后通过', async () => {
  const token = await adminToken2()
  const r = await runBatch(token, {
    name: 'retry',
    prompt: '只复述 uploads/in.txt 的原文,不要写任何文件。',
    rubric: '工作区 outputs/report.md 必须存在,且其内容包含输入原文(VERIFIER-E2E-INPUT-42)。请打开工作区核对文件。',
    gateRetry: true,
  })
  expect(r.retryCount).toBeGreaterThanOrEqual(1)   // 第一轮判官拦截 → 定向修复续跑
  expect(r.status).toBe('completed')               // 第二轮补写文件 → 判官取证通过
})
```

（`detail` 响应的 `sessions[].retry_count` 若未暴露，改用 attempts 端点 `GET /<batch_id>/attempts` 或事件流断言 GATE_RETRY；执行者按 detail 实际字段调整断言来源，断言语义不变：发生过判官拦截后的修复续跑且最终 completed。）

- [ ] **Step 2: 起环境跑通**

Run: `npx playwright test e2e/ai-full/ai-verifier-gate.spec.ts`（需 3002 + 4096 在线；后端先重启加载新代码，OC serve 若未重启需重启一次以加载 baize-verifier agent）
Expected: 3 passed

- [ ] **Step 3: Commit**

```bash
git add e2e/ai-full/ai-verifier-gate.spec.ts
git commit -m "test(e2e): verifier 门禁真实链路——达标/不达标/gate_retry 闭环"
```

---

### Task 9: 文档同步与全量回归

**Files:**
- Modify: `CLAUDE.md`（门禁描述段）、`docs/design/ai/AI动作门禁verifier判官核对设计.md`（状态行改「已实现」）

- [ ] **Step 1:** CLAUDE.md 中动作门禁/`action_checks` 描述处补一句：`check_type: 'verifier'`（判官核对——终态时由平台部署的只读 baize-verifier agent 在子会话工作区按 rubric 逐条判定，理由驱动 gate_retry；故障走 inconclusive→fail-closed）。
- [ ] **Step 2:** 设计文档头部状态行改 `状态：已实现（2026-09-29）`。
- [ ] **Step 3: 全量回归**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_agent_action_gate.py tests/test_verifier_gate.py tests/test_batch_engine.py tests/test_batch_execution_safety_p0.py tests/test_action_check_extractor.py tests/test_batch_routes.py -q`
Expected: 全 PASS

Run: `npx vitest run src/components/ai-chat/__tests__/CreateBatchDialog.test.ts`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md docs/design/ai/AI动作门禁verifier判官核对设计.md
git commit -m "docs: verifier 门禁落地同步——能力说明与设计状态"
```
