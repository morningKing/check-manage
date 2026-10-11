# 动作门禁 · 过程检查（checkpoint）与门禁策略库 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给动作门禁补上第三种时机——过程检查（checkpoint）：命中监控动作在执行前/后注入检查提示词（预算可配），终态 assert 双保险；门禁规则解耦为可引用的策略库（gate_policies）。

**Architecture:** 复用 `action_expectations` 一张表（mode 增第三值 `'checkpoint'`）与既有 pre-check 链路（before 注入 = deny 抛错通道），新增 `/ai/gate/internal/post-check` 端点 + 插件 after 钩子做 after 注入；原子预算列 `fired_count` 防冗余；`assert_spec` 走既有 tool 型终态计数路径接 gate_retry；策略库新表 + 批引用合并（先策略后批侧，同名批侧覆盖）。

**Tech Stack:** Flask + PostgreSQL（psycopg2）/ OpenCode 插件（内嵌 JS，随启动部署）/ Vue3 + Element Plus + Pinia。

**Spec:** `docs/superpowers/specs/2026-10-11-gate-checkpoint-policy-design.md`（计划与 spec 一起读；行号以 spec 为准，本计划代码与 spec §3-§9 一一对应）。

## Global Constraints

- 迁移必须**双注册**：`migrations/` 目录文件 + `server/app.py` boot 显式块（生产重启路径只走 app.py 块；护栏测试 `tests/test_migration_boot_registration.py`）。
- fail-open 三层不动：插件 3s 超时/非 2xx 放行；服务端查询异常放行留痕；缺参放行。终态核对 fail-closed。
- 正则一律 PG `~` 口径把关（`validate_pg_regex`），匹配用 Python `re.search`（与既有 deny 一致）。
- `mode` 列 `VARCHAR(10)`：`'checkpoint'` 恰 10 字符，禁止改列宽。
- 红线：模型不能增删干预规则（MCP 入口 C 不扩；执行侧无增删期望路径）。
- 插件 `PLUGIN_JS` 内容变化 → 需重启 OC serve（`POST /ai/opencode/restart`）才生效。
- 后端测试跑真实测试库：命令前缀 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`，工作目录 `server/`；前端测试 `npx vitest run`；e2e 从仓库根 `npx playwright test`（dev 栈未起会自动拉起）。
- 提交信息风格：`feat(gate): …` / `fix(gate): …` 中文一句话；每任务收尾独立提交。

---

### Task 1: after 注入方式探针（spike，先行）

**Files:**
- Create: `server/backups/gate_after_probe.plugin.js`（探针产物，验证后删除——`server/backups/` 在 .gitignore 语义内，不入库）
- 产出: 探针结论（三选一 + 证据），写入本文件 Task 8 的「探针结论」小节

**Interfaces:**
- Produces: 探针结论决定 Task 8 用「抛错」还是「改写 output.result」还是「降级 before」（env `AI_GATE_AFTER_DEGRADE=1`）。

- [ ] **Step 1: 写探针插件**

在 `server/backups/gate_after_probe.plugin.js` 写入（内容即探针，不做门禁逻辑）：

```js
export const GateAfterProbe = async () => {
  const log = []
  return {
    async 'tool.execute.before'(input, output) {
      log.push({ hook: 'before', inputKeys: Object.keys(input || {}),
                 argKeys: Object.keys(output || {}) })
      try { console.error('[probe-before]', JSON.stringify({ callId: input?.callId, tool: input?.tool })) } catch {}
    },
    async 'tool.execute.after'(input, output) {
      // T0: 记录 after 钩子的入参形状
      try { console.error('[probe-after]', JSON.stringify({
        inputKeys: Object.keys(input || {}), outputKeys: Object.keys(output || {}),
        resultHead: String(output?.result ?? '').slice(0, 80) })) } catch {}
      // T2: 改写 result 追加标记（验证模型是否可见）
      if (output && typeof output === 'object') {
        output.result = `${output.result ?? ''}\n[probe-append] AFTER_INJECT_VISIBLE_MARKER`
      }
      // T1: 抛错验证（注释切换——先跑 T2 观察追加是否可见，再切换跑 T1）
      // throw new Error('[probe-throw] AFTER_THROW_VISIBLE_MARKER')
    },
  }
}
```

- [ ] **Step 2: 部署探针并重启 serve**

把插件复制进 OC 全局插件目录并重启（与 `ensure_runtime_plugin` 同目录）：

```bash
cp server/backups/gate_after_probe.plugin.js ~/.config/opencode/plugin/gate-after-probe.js
curl -s -X POST http://127.0.0.1:3002/ai/opencode/restart \
  -H "Authorization: Bearer $(python -c "
import json,urllib.request
req=urllib.request.Request('http://127.0.0.1:3002/auth/login',data=json.dumps({'username':'admin','password':'admin123'}).encode(),headers={'Content-Type':'application/json'})
print(json.load(urllib.request.urlopen(req))['token'])")" -H 'Content-Type: application/json' -d '{}'
```

（dev 栈未起先 `npm run dev:all` 后台拉起；409 ACTIVE_WORKLOAD 加 `{"force": true}`。）

- [ ] **Step 3: 跑一次真会话采证**

经 API 建交互会话、发「运行 bash: echo probe-after-test」（真 LLM），然后导出该会话消息（`GET /ai/chat/sessions/<sid>/messages`），检查 bash 工具结果里是否出现 `[probe-append] AFTER_INJECT_VISIBLE_MARKER`；切注释跑 T1（抛错）重跑一轮，看结果是否变成错误文本 `[probe-throw] AFTER_THROW_VISIBLE_MARKER`；OC serve 日志（`server/proxy-opencode.log`）里找 `[probe-after]` 行记录 inputKeys/outputKeys 形状（确认 after 是否有 args、callId）。

- [ ] **Step 4: 记录结论并清理**

把结论写进本文件 Task 8 的「探针结论」行（三选一：`throw` / `append_result` / `degrade_before`，附 T0 inputKeys 证据）。删除探针插件文件与 OC 全局目录里的副本，重启 serve 恢复。

---

### Task 2: 迁移——checkpoint 列 + gate_policies 表 + 批引用列

**Files:**
- Create: `server/migrations/2026_10_11_gate_checkpoint_policy.py`
- Modify: `server/app.py`（迁移 boot 块，紧跟 `_gate_mode_boot` 块之后）
- Test: `server/tests/test_agent_action_gate.py`（文件末尾追加 schema 用例）

**Interfaces:**
- Produces: `action_expectations` 新列 `prompt/timing/max_checks/fired_count/assert_spec`；表 `gate_policies`；`ai_chat_batches.gate_policy_id`。后续所有任务依赖这些列名。

- [ ] **Step 1: 写迁移文件**

```python
# -*- coding: utf-8 -*-
"""幂等迁移：动作门禁过程检查（checkpoint）与门禁策略库。

- action_expectations 加 checkpoint 承载列：prompt（检查提示词）/timing
  （before|after）/max_checks（注入预算）/fired_count（已注入计数）/
  assert_spec（终态兜底断言，账本型 tool 断言）。mode 列 VARCHAR(10)
  无 CHECK 约束，'checkpoint' 恰 10 字符直接可用。
- gate_policies：命名的门禁规则集（deny/post/checkpoint 混装），批引用即生效。
- ai_chat_batches.gate_policy_id：批引用策略（应用层校验，不加 FK——终态批
  的历史引用允许随策略删除，期望行快照已生效）。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db

def run():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("ALTER TABLE action_expectations "
                    "ADD COLUMN IF NOT EXISTS prompt TEXT")
        cur.execute("ALTER TABLE action_expectations "
                    "ADD COLUMN IF NOT EXISTS timing VARCHAR(8) NOT NULL DEFAULT 'before'")
        cur.execute("ALTER TABLE action_expectations "
                    "ADD COLUMN IF NOT EXISTS max_checks INT NOT NULL DEFAULT 1")
        cur.execute("ALTER TABLE action_expectations "
                    "ADD COLUMN IF NOT EXISTS fired_count INT NOT NULL DEFAULT 0")
        cur.execute("ALTER TABLE action_expectations "
                    "ADD COLUMN IF NOT EXISTS assert_spec JSONB")
        cur.execute(
            "CREATE TABLE IF NOT EXISTS gate_policies ("
            " id TEXT PRIMARY KEY,"
            " name TEXT NOT NULL UNIQUE,"
            " description TEXT,"
            " checks JSONB NOT NULL DEFAULT '[]'::jsonb,"
            " enabled BOOLEAN NOT NULL DEFAULT TRUE,"
            " created_by TEXT,"
            " created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),"
            " updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())")
        cur.execute("ALTER TABLE ai_chat_batches "
                    "ADD COLUMN IF NOT EXISTS gate_policy_id TEXT")
        conn.commit()
    print("gate checkpoint/policy migration ready.")

if __name__ == "__main__":
    run()
```

- [ ] **Step 2: app.py 双注册**

在 `server/app.py` 的 `_gate_mode_boot` 块（搜 `'2026_09_26_action_gate_mode.py'`）之后插入：

```python
# 动作门禁过程检查 + 策略库（2026-10-11）：checkpoint 承载列 / gate_policies /
# 批引用列。随启动幂等执行；漏注册会让 pre/post-check 查询报列不存在 → 全部
# fail-open 放行（过程检查静默失效）。
try:
    _mp19 = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'migrations', '2026_10_11_gate_checkpoint_policy.py')
    _spec19 = _ilu.spec_from_file_location('_gate_checkpoint_boot', _mp19)
    _m19 = _ilu.module_from_spec(_spec19)
    _spec19.loader.exec_module(_m19)
    _m19.run()
except Exception as _e:
    logging.warning('gate checkpoint/policy migration on boot failed: %s', _e)
```

- [ ] **Step 3: 写失败测试（schema 断言）**

`server/tests/test_agent_action_gate.py` 末尾追加：

```python
def test_gate_checkpoint_policy_schema(db_conn):
    """checkpoint 承载列 / gate_policies 表 / 批引用列就绪（迁移双注册护栏）。"""
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'action_expectations' "
            "  AND column_name IN ('prompt','timing','max_checks',"
            "                      'fired_count','assert_spec')")
        assert {r[0] for r in cur.fetchall()} == {
            'prompt', 'timing', 'max_checks', 'fired_count', 'assert_spec'}
        cur.execute("SELECT to_regclass('gate_policies')")
        assert cur.fetchone()[0] is not None
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'ai_chat_batches' "
            "  AND column_name = 'gate_policy_id'")
        assert cur.fetchone() is not None
```

- [ ] **Step 4: 跑测试（先对现状失败——迁移未跑时红）**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_agent_action_gate.py -q -k checkpoint_policy_schema`
Expected: 迁移跑过后 PASS（fixture autouse 加载旧迁移不会建新列——若首跑即绿，说明 conftest 的库已有列，确认 `git stash` 干净基线上为红再继续）。

- [ ] **Step 5: Commit**

```bash
git add server/migrations/2026_10_11_gate_checkpoint_policy.py server/app.py server/tests/test_agent_action_gate.py
git commit -m "feat(gate): checkpoint 承载列/gate_policies 表/批引用列迁移（双注册）"
```

---

### Task 3: `validate_checks` 扩展——checkpoint 规则规范化与校验

**Files:**
- Modify: `server/utils/agent_ledger.py:308`（VALID_MODES）、`:414-441`（mode 处理段与 normalized.append）
- Test: `server/tests/test_agent_action_gate.py`（纯函数段追加）

**Interfaces:**
- Produces: `validate_checks` 输出条目新增键 `prompt: str|None`、`timing: 'before'|'after'`、`max_checks: int`、`assert_spec: dict|None`；`VALID_MODES = ('post', 'pre', 'checkpoint')`。Task 4/6/7 依赖这些键。

- [ ] **Step 1: 写失败测试**

```python
def test_validate_checkpoint_rule_normalized():
    out = agent_ledger.validate_checks([{
        'name': '写报告前读知识库', 'mode': 'checkpoint', 'check_type': 'tool',
        'tool': 'write', 'args_pattern': 'outputs/report',
        'timing': 'after', 'prompt': '检查写入内容是否含结论段。',
        'max_checks': 3,
        'assert_spec': {'tool': 'read', 'args_pattern': 'docs/kb/', 'min_count': 1},
    }])
    c = out[0]
    assert c['mode'] == 'checkpoint' and c['timing'] == 'after'
    assert c['max_checks'] == 3 and c['prompt'].startswith('检查')
    assert c['assert_spec'] == {'tool': 'read', 'args_pattern': 'docs/kb/',
                                'min_count': 1}


def test_validate_checkpoint_defaults_and_errors():
    # 缺省回填：timing=before、max_checks=1
    c = agent_ledger.validate_checks([{
        'name': 'cp1', 'mode': 'checkpoint', 'tool': 'write',
        'args_pattern': 'x', 'prompt': 'p'}])[0]
    assert c['timing'] == 'before' and c['max_checks'] == 1
    assert c['assert_spec'] is None
    with pytest.raises(ValueError, match='prompt'):
        agent_ledger.validate_checks([{
            'name': 'cp2', 'mode': 'checkpoint', 'tool': 'w', 'args_pattern': 'x'}])
    with pytest.raises(ValueError, match='超长'):
        agent_ledger.validate_checks([{
            'name': 'cp3', 'mode': 'checkpoint', 'tool': 'w', 'args_pattern': 'x',
            'prompt': '长' * 2001}])
    with pytest.raises(ValueError, match='timing'):
        agent_ledger.validate_checks([{
            'name': 'cp4', 'mode': 'checkpoint', 'tool': 'w', 'args_pattern': 'x',
            'prompt': 'p', 'timing': 'mid'}])
    with pytest.raises(ValueError, match='max_checks'):
        agent_ledger.validate_checks([{
            'name': 'cp5', 'mode': 'checkpoint', 'tool': 'w', 'args_pattern': 'x',
            'prompt': 'p', 'max_checks': 0}])
    with pytest.raises(ValueError, match='assert_spec.args_pattern'):
        agent_ledger.validate_checks([{
            'name': 'cp6', 'mode': 'checkpoint', 'tool': 'w', 'args_pattern': 'x',
            'prompt': 'p',
            'assert_spec': {'tool': 'read', 'args_pattern': '[bad'}]})
    with pytest.raises(ValueError, match='只支持 tool'):
        agent_ledger.validate_checks([{
            'name': 'cp7', 'mode': 'checkpoint', 'check_type': 'file',
            'tool': 'file', 'prompt': 'p',
            'effect_spec': {'path': 'a.txt'}}])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_agent_action_gate.py -q -k "validate_checkpoint"`
Expected: FAIL（`mode 只支持 ('post', 'pre')`）

- [ ] **Step 3: 实现**

`agent_ledger.py`：

```python
VALID_MODES = ('post', 'pre', 'checkpoint')
```

mode 校验段（`:414-420`）后追加：

```python
        prompt = None
        timing = 'before'
        max_checks = 1
        assert_spec = None
        if mode == 'checkpoint':
            if check_type != 'tool':
                raise ValueError(f'action_checks[{i}].mode=checkpoint 只支持 '
                                 'tool 型检查（触发对象是工具调用）')
            prompt = (c.get('prompt') or '').strip()
            if not prompt:
                raise ValueError(f'action_checks[{i}].prompt 必填（checkpoint 规则）')
            if len(prompt) > 2000:
                raise ValueError(f'action_checks[{i}].prompt 超长(>2000 字)')
            timing = (c.get('timing') or 'before').strip()
            if timing not in ('before', 'after'):
                raise ValueError(f'action_checks[{i}].timing 只支持 before/after')
            try:
                max_checks = int(c.get('max_checks', 1) or 1)
            except (TypeError, ValueError):
                raise ValueError(f'action_checks[{i}].max_checks 必须是整数')
            if not 1 <= max_checks <= 20:
                raise ValueError(f'action_checks[{i}].max_checks 必须在 1..20')
            aspec = c.get('assert_spec')
            if aspec is not None:
                if not isinstance(aspec, dict):
                    raise ValueError(f'action_checks[{i}].assert_spec 必须是对象')
                a_tool = (aspec.get('tool') or '').strip()
                a_pat = (aspec.get('args_pattern') or '').strip()
                if not a_tool or len(a_tool) > 50:
                    raise ValueError(f'action_checks[{i}].assert_spec.tool '
                                     '必填且不超过 50 字')
                if not a_pat:
                    raise ValueError(f'action_checks[{i}].assert_spec.args_pattern 必填')
                try:
                    re.compile(a_pat)
                except re.error as e:
                    raise ValueError(f'action_checks[{i}].assert_spec.args_pattern '
                                     f'不是合法正则: {e}')
                try:
                    validate_pg_regex(a_pat)
                except ValueError as e:
                    raise ValueError(f'action_checks[{i}].assert_spec.args_pattern {e}')
                try:
                    a_min = int(aspec.get('min_count', 1) or 1)
                except (TypeError, ValueError):
                    raise ValueError(f'action_checks[{i}].assert_spec.min_count '
                                     '必须是整数')
                if a_min < 1:
                    raise ValueError(f'action_checks[{i}].assert_spec.min_count 至少为 1')
                assert_spec = {'tool': a_tool, 'args_pattern': a_pat,
                               'min_count': a_min}
```

`normalized.append({...})` 字典追加四个键：

```python
            'prompt': prompt, 'timing': timing, 'max_checks': max_checks,
            'assert_spec': assert_spec,
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_agent_action_gate.py -q -k "validate"`
Expected: PASS（含既有 validate 用例）

- [ ] **Step 5: Commit**

```bash
git add server/utils/agent_ledger.py server/tests/test_agent_action_gate.py
git commit -m "feat(gate): validate_checks 支持 checkpoint 规则（prompt/timing/max_checks/assert_spec）"
```

---

### Task 4: `pre_check_tool` 扩展——deny 优先 + checkpoint 预算 + agent 定向

**Files:**
- Modify: `server/utils/agent_ledger.py:589-620`（pre_check_tool 重写）
- Modify: `server/routes/ai_gate_internal.py:36-91`（`_resolve_scope_id` → `_resolve_session`，pre-check 路由传 agent）
- Test: `server/tests/test_agent_action_gate.py`

**Interfaces:**
- Consumes: Task 3 的 normalized 键、Task 2 的列。
- Produces: `pre_check_tool(session_id, tool, args_text, agent_name=None, get_db=None) -> dict`（返回 `{'allow': bool, 'reason'?: str, 'rules': int}`；reason=检查提示词或 deny 理由）；`_resolve_session(oc_or_platform_id) -> tuple[str, str | None]`（路由层，agent_name=子代理名/根会话 None——**agent 名由路由层解析传入，账本函数信任参数**，`_resolve_session` 的子代理查询本来就返回 agent，零额外查询）。Task 5 的 post_check_tool 同签名风格。

- [ ] **Step 1: 写失败测试**

测试前置：fixture 登记期望（真实库）。追加：

```python
@pytest.fixture
def cp_scope(db_conn):
    """登记一条 checkpoint(before) 规则到临时 scope，收尾删除。"""
    scope = f'sess_cp_{uuid.uuid4().hex[:8]}'
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO action_expectations (scope_type, scope_id, name, tool,"
            " args_pattern, require_state, min_count, source, check_type, mode,"
            " timing, max_checks, fired_count, prompt)"
            " VALUES ('tree', %s, '写前读知识库', 'write', 'outputs/report',"
            " 'completed', 1, 'batch', 'tool', 'checkpoint', 'before', 2, 0,"
            " '请先读取知识库 docs/kb/ 再写入')", (scope,))
    db_conn.commit()
    yield scope
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM action_expectations WHERE scope_id = %s", (scope,))
    db_conn.commit()


class TestPreCheckCheckpoint:
    def test_budget_consume_then_exhaust_then_allow(self, db_conn, cp_scope):
        r1 = agent_ledger.pre_check_tool(cp_scope, 'write', 'outputs/report.md')
        assert r1 == {'allow': False, 'reason': '请先读取知识库 docs/kb/ 再写入',
                      'rules': 1}
        r2 = agent_ledger.pre_check_tool(cp_scope, 'write', 'outputs/report.md')
        assert r2['allow'] is False          # 预算 2：第二次仍注入
        r3 = agent_ledger.pre_check_tool(cp_scope, 'write', 'outputs/report.md')
        assert r3 == {'allow': True, 'rules': 1}   # 预算耗尽 → 放行

    def test_non_matching_tool_untouched(self, db_conn, cp_scope):
        r = agent_ledger.pre_check_tool(cp_scope, 'bash', 'outputs/report')
        assert r == {'allow': True, 'rules': 0}
        with db_conn.cursor() as cur:
            cur.execute("SELECT fired_count FROM action_expectations "
                        "WHERE scope_id=%s", (cp_scope,))
            assert cur.fetchone()[0] == 0     # 未命中不耗预算

    def test_deny_wins_over_checkpoint(self, db_conn, cp_scope):
        with db_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO action_expectations (scope_type, scope_id, name,"
                " tool, args_pattern, require_state, min_count, source,"
                " check_type, mode)"
                " VALUES ('tree', %s, '禁删', 'write', 'secrets', 'completed',"
                " 1, 'batch', 'tool', 'pre')", (cp_scope,))
        db_conn.commit()
        r = agent_ledger.pre_check_tool(cp_scope, 'write', 'secrets.txt')
        assert r['allow'] is False and '拦截' in r['reason']

    def test_subagents_directed_injection(self, db_conn):
        """名单内子代理动作注入；名单外子代理/根会话动作放行且不耗预算。"""
        scope = f'sess_cp2_{uuid.uuid4().hex[:8]}'
        with db_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO action_expectations (scope_type, scope_id, name,"
                " tool, args_pattern, require_state, min_count, source,"
                " check_type, mode, timing, max_checks, fired_count, prompt,"
                " subagents)"
                " VALUES ('tree', %s, '定向检查', 'write', 'r', 'completed', 1,"
                " 'batch', 'tool', 'checkpoint', 'before', 5, 0, '先读知识库',"
                " %s)", (scope, ['分析师']))
        db_conn.commit()
        try:
            assert agent_ledger.pre_check_tool(
                scope, 'write', 'r', agent_name='执行器')['allow'] is True
            assert agent_ledger.pre_check_tool(
                scope, 'write', 'r', agent_name=None)['allow'] is True
            hit = agent_ledger.pre_check_tool(
                scope, 'write', 'r', agent_name='分析师')
            assert hit['allow'] is False and hit['reason'] == '先读知识库'
        finally:
            with db_conn.cursor() as cur:
                cur.execute("DELETE FROM action_expectations WHERE scope_id=%s",
                            (scope,))
            db_conn.commit()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_agent_action_gate.py -q -k "PreCheckCheckpoint"`
Expected: FAIL（现实现只查 mode='pre'，checkpoint 行被无视 → allow）

- [ ] **Step 3: 实现**

`agent_ledger.py` 替换 `pre_check_tool`（删除旧函数体；**不引入** `_acting_agent_name`
——agent 名由路由层 `_resolve_session` 解析传入，该函数的子代理查询本来就带出
agent 列，零额外查询）：

```python
def pre_check_tool(session_id: str, tool: str, args_text: str,
                   agent_name: str | None = None, get_db=None) -> dict:
    """P3-C3 pre 阻断 + checkpoint 过程检查（before 时机）。

    判定顺序：① mode='pre' deny 命中即拦（优先级最高，不计预算）；
    ② mode='checkpoint' AND timing='before' 按 (created_at, id) 序取第一条
    匹配且有预算的行，原子消耗 fired_count 后注入 prompt——复用 deny 的抛错
    通道（reason 即注入内容）。`agent_name` 是动作归属子代理名（路由层由 OC
    会话映射解析；根会话自身动作=None）：带 subagents 名单的行只对名单内
    子代理注入，名单外（含根会话）不注入不耗预算；无名单行树级全量。查询
    异常向上抛——由路由统一 fail-open。"""
    import re as _re
    db_ctx = get_db or _default_get_db
    with db_ctx() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT name, args_pattern FROM action_expectations "
                "WHERE scope_id = %s AND mode = 'pre' AND tool = %s",
                (session_id, tool),
            )
            deny_rows = cur.fetchall()
            for name, pattern in deny_rows:
                if pattern and _re.search(pattern, args_text or ''):
                    return {'allow': False,
                            'reason': f'门禁规则「{name}」拦截了工具 {tool} 的调用',
                            'rules': len(deny_rows)}
            cur.execute(
                "SELECT id, prompt, args_pattern, subagents FROM action_expectations "
                "WHERE scope_id = %s AND mode = 'checkpoint' "
                "  AND timing = 'before' AND tool = %s "
                "ORDER BY created_at, id",
                (session_id, tool),
            )
            check_rows = cur.fetchall()
            rules_n = len(deny_rows) + len(check_rows)
            for rid, prompt, pattern, subagents in check_rows:
                if not pattern or not _re.search(pattern, args_text or ''):
                    continue
                if subagents and (not agent_name or agent_name not in subagents):
                    continue
                cur.execute(
                    "UPDATE action_expectations SET fired_count = fired_count + 1 "
                    "WHERE id = %s AND fired_count < max_checks RETURNING id",
                    (rid,),
                )
                if cur.fetchone():
                    return {'allow': False, 'reason': prompt, 'rules': rules_n}
    return {'allow': True, 'rules': rules_n}
```

注意：`rules` 语义从「pre 行数」扩为「pre 行 + checkpoint(before) 行数」——
插件 `rules=0` 短路缓存语义保持正确（只有 checkpoint 规则时不被缓存跳过）。

`ai_gate_internal.py`：`_resolve_scope_id` 改造为 `_resolve_session`：

```python
def _resolve_session(oc_or_platform_id: str) -> tuple[str, str | None]:
    """工具调用所在会话 id → (期望登记的 scope_id, 动作归属 agent 名)。

    平台 id（sess_ 前缀）原样返回（根会话自身动作 agent=None）；OC 内部 id
    先查根/批子会话表（agent=None），再查子代理表（agent=子代理名，归属根
    会话——期望登记在根作用域、约束整棵树）。都查不到时原样返回（匹配不上
    → allow，与无期望同义）。"""
    sid = oc_or_platform_id or ''
    if not sid or sid.startswith('sess_'):
        return sid, None
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM ai_chat_sessions "
                    "WHERE opencode_session_id = %s "
                    "ORDER BY last_active_at DESC NULLS LAST LIMIT 1", (sid,))
                row = cur.fetchone()
                if row:
                    return row[0], None
                cur.execute(
                    "SELECT root_session_id, agent FROM ai_chat_subtasks "
                    "WHERE id = %s", (sid,))
                row = cur.fetchone()
                if row and row[0]:
                    return row[0], row[1]
    except Exception as e:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning(
            'gate session resolve failed %s: %s', sid, e)
    return sid, None
```

pre-check 路由改用：

```python
    scope_id, agent_name = _resolve_session(str(session_id))
    try:
        result = pre_check_tool(scope_id, str(tool), str(args_text or ''),
                                agent_name=agent_name)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_agent_action_gate.py tests/test_sse_liveness.py tests/test_routes_ai_chat.py -q`
Expected: PASS（含既有 pre-check 用例——注意既有断言 `{'allow': True, 'rules': n}` 形状的用例需同步核对 rules 语义）

- [ ] **Step 5: Commit**

```bash
git add server/utils/agent_ledger.py server/routes/ai_gate_internal.py server/tests/test_agent_action_gate.py
git commit -m "feat(gate): pre_check 支持 checkpoint 预算注入（deny 优先/agent 定向/原子计数）"
```

---

### Task 5: `post_check_tool` + `/post-check` 端点

**Files:**
- Modify: `server/utils/agent_ledger.py`（`post_check_tool` 新增，紧跟 `pre_check_tool`）
- Modify: `server/routes/ai_gate_internal.py`（新端点）
- Test: `server/tests/test_agent_action_gate.py`

**Interfaces:**
- Produces: `post_check_tool(session_id, tool, args_text, agent_name=None, get_db=None) -> dict`（`{'inject': bool, 'prompt'?: str, 'rules': int}`）；`POST /ai/gate/internal/post-check`（body `{sessionId, tool, argsText}` → `{inject, prompt?, rules}`）。

- [ ] **Step 1: 写失败测试**

```python
@pytest.fixture
def cp_after_scope(db_conn):
    scope = f'sess_cpa_{uuid.uuid4().hex[:8]}'
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO action_expectations (scope_type, scope_id, name, tool,"
            " args_pattern, require_state, min_count, source, check_type, mode,"
            " timing, max_checks, fired_count, prompt)"
            " VALUES ('tree', %s, '写后查结论', 'write', 'outputs/report',"
            " 'completed', 1, 'batch', 'tool', 'checkpoint', 'after', 1, 0,"
            " '请确认文件包含结论段，缺失请立即补写')", (scope,))
    db_conn.commit()
    yield scope
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM action_expectations WHERE scope_id = %s", (scope,))
    db_conn.commit()


class TestPostCheck:
    def test_after_inject_budget_and_exhaust(self, db_conn, cp_after_scope):
        r = agent_ledger.post_check_tool(cp_after_scope, 'write', 'outputs/report.md')
        assert r == {'inject': True, 'prompt': '请确认文件包含结论段，缺失请立即补写',
                     'rules': 1}
        r2 = agent_ledger.post_check_tool(cp_after_scope, 'write', 'outputs/report.md')
        assert r2 == {'inject': False, 'rules': 1}   # 预算耗尽
        with db_conn.cursor() as cur:
            cur.execute("SELECT fired_count FROM action_expectations "
                        "WHERE scope_id=%s", (cp_after_scope,))
            assert cur.fetchone()[0] == 1

    def test_before_rows_not_matched_by_after(self, db_conn, cp_scope):
        assert agent_ledger.post_check_tool(cp_scope, 'write', 'outputs/report') \
            == {'inject': False, 'rules': 0}

    def test_post_check_endpoint_auth_and_flow(self, db_conn, monkeypatch):
        """端点：token 鉴权 403 / 缺参 inject=false / 正常注入（scope 映射）。"""
        import db as db_module
        db_module.pool = None
        for mod_name, mod in list(sys.modules.items()):
            if mod is None:
                continue
            if getattr(mod, 'get_db', None) is not None and (
                    mod_name.startswith('routes.') or mod_name.startswith('utils.')
                    or mod_name == 'auth'):
                try:
                    mod.get_db = db_module.get_db
                except (AttributeError, TypeError):
                    pass
        from app import app
        app.config['TESTING'] = True
        client = app.test_client()
        monkeypatch.setattr('routes.ai_gate_internal.MCP_INTERNAL_TOKEN',
                            'test-token')
        tok = 'test-token'
        assert client.post('/ai/gate/internal/post-check',
                           json={'sessionId': cp_after_scope}).status_code == 403
        r = client.post('/ai/gate/internal/post-check',
                        headers={'X-Internal-Token': tok},
                        json={'sessionId': cp_after_scope})
        assert r.status_code == 200
        assert r.get_json()['inject'] is False        # 缺 tool
        r2 = client.post('/ai/gate/internal/post-check',
                         headers={'X-Internal-Token': tok},
                         json={'sessionId': cp_after_scope,
                               'tool': 'write', 'argsText': 'outputs/r.md'})
        body = r2.get_json()
        assert body['inject'] is True and '结论段' in body['prompt']
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_agent_action_gate.py -q -k "PostCheck"`
Expected: FAIL（`post_check_tool` 不存在）

- [ ] **Step 3: 实现**

`agent_ledger.py`（`pre_check_tool` 之后）：

```python
def post_check_tool(session_id: str, tool: str, args_text: str,
                    agent_name: str | None = None, get_db=None) -> dict:
    """checkpoint 过程检查（after 时机）——工具调用完成后由 OC 插件经
    /ai/gate/internal/post-check 调用。语义与 pre_check_tool 的 checkpoint
    分支对称：timing='after' 行匹配 + 原子预算 + agent 定向（agent_name 由
    路由层解析传入，口径同 §4.1）；预算耗尽/无规则 → inject=False（动作照常
    完成，终态 assert 兜底）。查询异常向上抛——路由统一 fail-open（不注入）。"""
    import re as _re
    db_ctx = get_db or _default_get_db
    with db_ctx() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, prompt, args_pattern, subagents FROM action_expectations "
                "WHERE scope_id = %s AND mode = 'checkpoint' "
                "  AND timing = 'after' AND tool = %s "
                "ORDER BY created_at, id",
                (session_id, tool),
            )
            check_rows = cur.fetchall()
            for rid, prompt, pattern, subagents in check_rows:
                if not pattern or not _re.search(pattern, args_text or ''):
                    continue
                if subagents and (not agent_name or agent_name not in subagents):
                    continue
                cur.execute(
                    "UPDATE action_expectations SET fired_count = fired_count + 1 "
                    "WHERE id = %s AND fired_count < max_checks RETURNING id",
                    (rid,),
                )
                if cur.fetchone():
                    return {'inject': True, 'prompt': prompt,
                            'rules': len(check_rows)}
    return {'inject': False, 'rules': len(check_rows)}
```

`ai_gate_internal.py` 新端点（文件末尾）：

```python
@ai_gate_internal_bp.route('/post-check', methods=['POST'])
def post_check():
    """工具调用后过程检查：session_id + tool + args → inject/prompt。

    inject=true 时插件向模型注入 prompt（注入方式由探针结论定夺，见实现
    计划 Task 1/8）。fail-open 口径与 pre-check 相同：本端点不可达/非 2xx
    时插件侧不注入；查询异常服务端同样 fail-open 并留痕。"""
    if not _authorized():
        return jsonify({'error': 'forbidden'}), 403
    body = request.get_json(silent=True) or {}
    session_id = body.get('sessionId')
    tool = body.get('tool')
    args_text = body.get('argsText') or ''
    if not session_id or not tool:
        return jsonify({'inject': False, 'rules': 0}), 200
    scope_id, agent_name = _resolve_session(str(session_id))
    try:
        result = post_check_tool(scope_id, str(tool), str(args_text or ''),
                                 agent_name=agent_name)
    except Exception as e:  # noqa: BLE001 —— 校验故障不注入（fail-open）
        import logging
        logging.getLogger(__name__).warning(
            'post-check failed sid=%s tool=%s: %s', scope_id, tool, e)
        result = {'inject': False, 'rules': 0, 'error': str(e)[:200]}
    return jsonify(result), 200
```

顶部 import 行加 `post_check_tool`（`from utils.agent_ledger import pre_check_tool, post_check_tool`）。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_agent_action_gate.py -q -k "PostCheck or PreCheck"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/utils/agent_ledger.py server/routes/ai_gate_internal.py server/tests/test_agent_action_gate.py
git commit -m "feat(gate): post-check 端点——checkpoint after 时机预算注入"
```

---

### Task 6: 终态兜底——`check_session_gate` 核对 assert_spec + 失败明细渲染

**Files:**
- Modify: `server/utils/agent_ledger.py:736-744`（两处 expected 计数）、`:746-757`（主 SELECT）、`:763-826`（核对循环）、`_expectation_desc`
- Test: `server/tests/test_agent_action_gate.py`

**Interfaces:**
- Consumes: Task 2 的 `assert_spec` 列。
- Produces: checkpoint+assert 行参与终态核对（复用 tool 计数路径）；`results` 条目对 checkpoint 行带 `'mode': 'checkpoint'`；`gate_failure_message` 渲染 `过程检查·名(需 read ~ docs/kb/, 实际命中 x/y)`。

- [ ] **Step 1: 写失败测试**

```python
class TestCheckpointTerminalAssert:
    def test_assert_spec_counts_ledger_hits(self, db_conn):
        """assert_spec：账本命中 ≥ min_count → passed；不满足 → failed。"""
        scope = f'sess_ta_{uuid.uuid4().hex[:8]}'
        with db_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ai_chat_sessions (id, user_id, status,"
                " opencode_session_id, workspace_path)"
                " VALUES (%s, 'gate-test', 'completed', 'oc-ta-x', '/tmp/ta')")
            cur.execute(
                "INSERT INTO action_expectations (scope_type, scope_id, name,"
                " tool, args_pattern, require_state, min_count, source,"
                " check_type, mode, timing, max_checks, fired_count, prompt,"
                " assert_spec)"
                " VALUES ('tree', %s, '写前读知识库', 'write', 'r', 'completed',"
                " 1, 'batch', 'tool', 'checkpoint', 'before', 3, 1, '先读',"
                " %s)", (scope, '{"tool":"read","args_pattern":"docs/kb/",'
                                '"min_count":1}'))
            cur.execute(
                "INSERT INTO agent_tool_calls (oc_session_id, root_session_id,"
                " part_id, tool, args_text, state, occurred_at)"
                " VALUES ('oc-ta-x', %s, 'p1', 'read', 'docs/kb/a.md',"
                " 'completed', NOW())", (scope,))
        db_conn.commit()
        try:
            gate = agent_ledger.check_session_gate(scope)
            assert gate['status'] == 'passed'
            assert gate['results'][0]['mode'] == 'checkpoint'
            # 撤掉证据 → failed；gate_failure_message 渲染过程检查明细
            with db_conn.cursor() as cur:
                cur.execute("DELETE FROM agent_tool_calls WHERE part_id='p1'")
            db_conn.commit()
            gate2 = agent_ledger.check_session_gate(scope)
            assert gate2['status'] == 'failed'
            msg = agent_ledger.gate_failure_message(gate2)
            assert '写前读知识库' in msg and 'read' in msg
            # 无 assert 的纯提醒行不参与终态
            with db_conn.cursor() as cur:
                cur.execute("UPDATE action_expectations SET assert_spec = NULL "
                            "WHERE scope_id=%s", (scope,))
            db_conn.commit()
            assert agent_ledger.check_session_gate(scope)['status'] == 'passed'
        finally:
            with db_conn.cursor() as cur:
                cur.execute("DELETE FROM agent_tool_calls WHERE root_session_id=%s",
                            (scope,))
                cur.execute("DELETE FROM ai_chat_sessions WHERE id=%s", (scope,))
                cur.execute("DELETE FROM action_expectations WHERE scope_id=%s",
                            (scope,))
            db_conn.commit()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_agent_action_gate.py -q -k "CheckpointTerminalAssert"`
Expected: FAIL（checkpoint 行不进核对 → passed，但 results 空 → `results[0]` IndexError 或 mode 断言失败）

- [ ] **Step 3: 实现**

`check_session_gate`：

1. 两处 expected 计数（`:736-744` 与异常兜底 `:838-844`）的 WHERE 改为
   `mode IN ('post', 'checkpoint')`（checkpoint+assert 是「已登记且必须可核对」
   的期望——inconclusive fail-closed 语义覆盖它；纯提醒行无 assert 不算，见下）。
   注意：为让「纯提醒行不推高 expected」，两处计数 WHERE 再加
   `AND (mode = 'post' OR assert_spec IS NOT NULL)`。
2. 主 SELECT（`:746-757`）：

```sql
SELECT id, name, tool, args_pattern, require_state, min_count, scope_type,
       check_type, effect_spec, subagents, mode, assert_spec
FROM action_expectations
WHERE scope_id = %s AND scope_type IN ('session','tree')
  AND (mode = 'post' OR (mode = 'checkpoint' AND assert_spec IS NOT NULL))
ORDER BY id
```

3. 循环解包加 `mode, assert_spec`，checkpoint 行归一化为 tool 计数：

```python
                for (eid, name, tool, pattern, req_state, min_count,
                     scope, check_type, effect_spec, subagents,
                     exp_mode, assert_spec) in exps:
                    if check_type == 'verifier':
                        continue
                    if exp_mode == 'checkpoint':
                        # 过程检查的终态兜底断言：assert_spec 的账本字段参与
                        # 核对（行上的 tool/args_pattern 是触发匹配，不是断言）
                        aspec = assert_spec or {}
                        tool = aspec.get('tool') or tool
                        pattern = aspec.get('args_pattern') or ''
                        min_count = int(aspec.get('min_count') or 1)
                        req_state = 'completed'
```

（`results.append` 字典加 `'mode': exp_mode or 'post'`；tool/pattern 已是
assert 字段，`_expectation_desc` 的 tool 分支渲染 `read ~ docs/kb/`。）

4. `_expectation_desc` 的 tool 分支前加：

```python
    if r.get('mode') == 'checkpoint':
        return f"过程检查·{r.get('tool')} ~ {r.get('args_pattern')}"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_agent_action_gate.py tests/test_agent_ledger.py -q`
Expected: PASS（含既有终态核对用例）

- [ ] **Step 5: Commit**

```bash
git add server/utils/agent_ledger.py server/tests/test_agent_action_gate.py
git commit -m "feat(gate): checkpoint assert_spec 终态兜底核对（复用 tool 计数路径）"
```

---

### Task 7: 登记重置 `fired_count`

**Files:**
- Modify: `server/utils/agent_ledger.py:540-582`（register_session_expectations）
- Test: `server/tests/test_agent_action_gate.py`

**Interfaces:**
- Produces: 登记/重登记（含 sync_batch_expectations 的删旧插新路径）把 checkpoint 行 `fired_count` 归零。

- [ ] **Step 1: 写失败测试**

```python
def test_register_resets_fired_count(db_conn, cp_scope):
    """重新登记（重试/修正轮）预算重置：fired_count 归零。"""
    with db_conn.cursor() as cur:
        cur.execute("UPDATE action_expectations SET fired_count = 2 "
                    "WHERE scope_id=%s", (cp_scope,))
    db_conn.commit()
    agent_ledger.register_session_expectations(cp_scope, [{
        'name': '写前读知识库', 'mode': 'checkpoint', 'tool': 'write',
        'args_pattern': 'outputs/report', 'prompt': '请先读取知识库 docs/kb/ 再写入',
        'max_checks': 2, 'timing': 'before', 'scope': 'tree'}])
    with db_conn.cursor() as cur:
        cur.execute("SELECT fired_count FROM action_expectations "
                    "WHERE scope_id=%s", (cp_scope,))
        assert cur.fetchone()[0] == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_agent_action_gate.py -q -k "resets_fired_count"`
Expected: FAIL（fired_count 仍为 2——INSERT 列不含 fired_count，ON CONFLICT 不触碰）

- [ ] **Step 3: 实现**

`register_session_expectations` 的 INSERT 列与 VALUES 追加
`prompt, timing, max_checks, fired_count, assert_spec`，ON CONFLICT DO UPDATE
SET 追加：

```sql
                        prompt = EXCLUDED.prompt,
                        timing = EXCLUDED.timing,
                        max_checks = EXCLUDED.max_checks,
                        assert_spec = EXCLUDED.assert_spec,
                        fired_count = 0,
```

参数元组对应追加：

```python
                     c.get('prompt'), c.get('timing') or 'before',
                     c.get('max_checks') or 1,
                     psycopg2.extras.Json(c.get('assert_spec'))
                     if c.get('assert_spec') else None,
```

（INSERT 列表的 `fired_count` 传 `0`；`sync_session_expectations` 走删旧插新，
天然重置，无需改。）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_agent_action_gate.py -q -k "register or fired"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/utils/agent_ledger.py server/tests/test_agent_action_gate.py
git commit -m "feat(gate): 重新登记重置 checkpoint 注入预算 fired_count"
```

---

### Task 8: 插件 after 钩子（按 Task 1 探针结论）

**Files:**
- Modify: `server/utils/skillopt.py`（PLUGIN_JS + `ensure_runtime_plugin` 签名）
- Modify: `server/app.py:186-190`（部署调用传 post-check 端点）
- Test: `server/tests/test_agent_action_gate.py`（PLUGIN_JS 内容断言）

**Interfaces:**
- Consumes: Task 5 的 `/post-check` 端点（`{inject, prompt, rules}`）。
- Produces: 插件 after 钩子按探针结论注入。部署后需重启 OC serve。

**探针结论**：（Task 1 完成后填写——`throw` / `append_result` / `degrade_before`；下述代码按 `throw` 写，探针结论为 `append_result` 时把抛错行换成 `output.result = ...` 追加，`degrade_before` 时跳过本任务、改为在 `pre_check_tool` 的查询条件放开 `timing='before'` 到含 after 并设 env `AI_GATE_AFTER_DEGRADE=1`，同时本任务只加 post-check 客户端调用的注释说明。）

- [ ] **Step 1: 写失败测试**

```python
def test_plugin_js_has_after_hook_and_postcheck():
    """插件含 after 钩子与 post-check 调用（探针结论=throw 形态）。"""
    from utils.skillopt import PLUGIN_JS
    assert "tool.execute.after" in PLUGIN_JS
    assert "gate/internal/post-check" in PLUGIN_JS
    assert "BAIZE_GATE_POSTCHECK_URL" in PLUGIN_JS
    assert "_lastArgsByCall" in PLUGIN_JS          # before 暂存 argsText 供 after 复用
    assert "inject" in PLUGIN_JS and "prompt" in PLUGIN_JS
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_agent_action_gate.py -q -k "plugin_js_has_after"`
Expected: FAIL

- [ ] **Step 3: 实现（PLUGIN_JS 三处改动）**

①常量区（`GATE_URL` 行后）：

```js
const GATE_POST_URL = process.env.BAIZE_GATE_POSTCHECK_URL || '__GATE_POSTCHECK__'
// before 钩子暂存 argsText（after 钩子可能拿不到 args；keyed by callId），
// after 取走即删——Map 上限 500 防泄漏。
const _lastArgsByCall = new Map()
const _lastArgsMax = 500
// post-check 的 rules=0 短路缓存（与 pre-check 同款，独立 Map）
const _gatePostEmpty = new Map()
```

②`gatePreCheck` 函数后新增：

```js
async function gatePostCheck(sessionID, tool, argsText) {
  const emptyAt = _gatePostEmpty.get(sessionID)
  if (emptyAt) {
    if (Date.now() - emptyAt <= GATE_EMPTY_TTL_MS) return { inject: false }
    _gatePostEmpty.delete(sessionID)
  }
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), GATE_TIMEOUT_MS)
  try {
    const res = await fetch(GATE_POST_URL, {
      method: 'POST', headers: HEADERS, signal: ctrl.signal,
      body: JSON.stringify({ sessionId: sessionID, tool, argsText }),
    })
    if (!res.ok) return { inject: false }
    const body = await res.json()
    if (body && body.rules === 0) _gatePostEmpty.set(sessionID, Date.now())
    return body || { inject: false }
  } finally {
    clearTimeout(timer)
  }
}
```

③插件返回对象加两个钩子（`'tool.execute.before'` 钩子体开头加 args 暂存、
返回对象尾部加 after）：

```js
    async 'tool.execute.before'(input, output) {
      if (!GATE_URL || !input || !input.tool || !input.sessionID) return
      const argsText = gateArgsText(output && output.args)
      if (input.callId) {
        if (_lastArgsByCall.size >= _lastArgsMax) {
          const first = _lastArgsByCall.keys().next().value
          _lastArgsByCall.delete(first)
        }
        _lastArgsByCall.set(input.callId, argsText)
      }
      // ……既有 gatePreCheck 调用保持不变……
    },
    async 'tool.execute.after'(input, output) {
      if (!GATE_POST_URL || !input || !input.tool || !input.sessionID) return
      const argsText = (input.callId && _lastArgsByCall.get(input.callId)) || ''
      if (input.callId) _lastArgsByCall.delete(input.callId)
      let body = null
      try {
        body = await gatePostCheck(input.sessionID, input.tool, argsText)
      } catch (e) {
        return   // fail-open：post-check 不可达不注入
      }
      if (body && body.inject && body.prompt) {
        // 探针结论=throw：OC 把抛错作为工具结果回给模型——提示词即纠偏指令
        throw new Error(body.prompt)
      }
    },
```

④模板占位符替换：`ensure_runtime_plugin` 的 `js = PLUGIN_JS.replace(...)` 链加
`.replace('__GATE_POSTCHECK__', gate_post_endpoint or '')`；签名加
`gate_post_endpoint: str = ''`；`app.py:186-190` 调用处：

```python
    _gate_post_ep = f'http://127.0.0.1:{_FPORT}/ai/gate/internal/post-check'
    _sk.ensure_runtime_plugin(_OGD, _ep,
                              _os.getenv('MCP_INTERNAL_TOKEN', ''),
                              gate_endpoint=_gate_ep,
                              gate_post_endpoint=_gate_post_ep)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_agent_action_gate.py -q -k "plugin_js"`
Expected: PASS

- [ ] **Step 5: 真链路冒烟 + Commit**

起 dev 栈 → 重启 OC serve（插件重写生效）→ 交互会话登记 after 规则（管理员 attach 或临时脚本直插期望行）→ 发消息让模型写文件 → 观察写完后模型收到检查提示词并（按提示）补正。冒烟通过后：

```bash
git add server/utils/skillopt.py server/app.py server/tests/test_agent_action_gate.py
git commit -m "feat(gate): baize-trace 插件 after 钩子——动作完成后注入检查提示词"
```

---

### Task 9: `gate_policy_repo` + CRUD 端点

**Files:**
- Create: `server/utils/gate_policy_repo.py`
- Create: `server/routes/ai_gate_policies.py`
- Modify: `server/app.py`（蓝图注册，`ai_gate_internal_bp` 注册行旁）
- Test: `server/tests/test_gate_policies.py`（新）

**Interfaces:**
- Produces: `gate_policy_repo.create_policy/list_policies/get_policy/update_policy/delete_policy/count_active_refs`；路由 `GET|POST /ai/chat/gate-policies`、`GET|PATCH|DELETE /ai/chat/gate-policies/<id>`。Task 10 的批引用与 Task 11/12 前端消费这些形状。

- [ ] **Step 1: 写失败测试**

```python
# -*- coding: utf-8 -*-
"""门禁策略库：CRUD + 引用防护 + 端点鉴权。"""
import json
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from db import get_db
from utils import gate_policy_repo as repo

@pytest.fixture
def admin_h():
    from auth import create_token
    tok = create_token({'id': 'user-admin', 'username': 'admin', 'role': 'admin'})
    return {'Authorization': f'Bearer {tok}'}

@pytest.fixture
def client():
    """真实 DB 的 test client（shadow 掉 conftest 的 mock-DB `app` fixture，
    重绑定模式与 test_skill_fit_routes.client 一致）。"""
    import db as db_module
    db_module.pool = None
    for mod_name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        if getattr(mod, 'get_db', None) is not None and (
                mod_name.startswith('routes.') or mod_name.startswith('utils.')
                or mod_name == 'auth'):
            try:
                mod.get_db = db_module.get_db
            except (AttributeError, TypeError):
                pass
    from app import app
    app.config['TESTING'] = True
    return app.test_client()

CHECKS = [{
    'name': '写前读知识库', 'mode': 'checkpoint', 'check_type': 'tool',
    'tool': 'write', 'args_pattern': 'outputs/report', 'timing': 'before',
    'prompt': '请先读取知识库 docs/kb/ 再写入', 'max_checks': 2,
    'assert_spec': {'tool': 'read', 'args_pattern': 'docs/kb/', 'min_count': 1},
}]

def _cleanup(pid):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM gate_policies WHERE id=%s", (pid,))
    conn.commit()

def test_policy_crud_roundtrip(db_conn):
    pid = repo.create_policy('user-admin', f'pol-{uuid.uuid4().hex[:6]}',
                             'd', CHECKS, enabled=True)['id']
    try:
        got = repo.get_policy(pid)
        assert got['checks'][0]['mode'] == 'checkpoint'
        assert got['checks'][0]['assert_spec']['tool'] == 'read'
        assert any(p['id'] == pid for p in repo.list_policies(enabled=True))
        repo.update_policy(pid, checks=None, enabled=False,
                           name=None, description=None)   # 只翻 enabled
        assert repo.get_policy(pid)['enabled'] is False
        assert all(p['id'] != pid for p in repo.list_policies(enabled=True))
    finally:
        _cleanup(pid)

def test_delete_blocked_by_active_batch_ref(db_conn):
    pid = repo.create_policy('user-admin', f'pol-{uuid.uuid4().hex[:6]}',
                             None, CHECKS)['id']
    bid = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_chat_batches (id, user_id, name, prompt, total,"
            " status, gate_policy_id) VALUES (%s,'user-admin','p','p',1,"
            " 'running', %s)", (bid, pid))
    db_conn.commit()
    try:
        with pytest.raises(repo.PolicyInUseError):
            repo.delete_policy(pid)
        with db_conn.cursor() as cur:
            cur.execute("UPDATE ai_chat_batches SET status='completed' "
                        "WHERE id=%s", (bid,))
        db_conn.commit()
        repo.delete_policy(pid)          # 仅终态引用 → 允许删
        assert repo.get_policy(pid) is None
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_chat_batches WHERE id=%s", (bid,))
            cur.execute("DELETE FROM gate_policies WHERE id=%s", (pid,))
        db_conn.commit()

def test_policy_endpoints_auth_and_validation(client, admin_h, db_conn):
    # 写权限：非 admin 403
    from auth import create_token
    dev = create_token({'id': 'u-dev', 'username': 'd', 'role': 'developer'})
    r = client.post('/ai/chat/gate-policies',
                    headers={'Authorization': f'Bearer {dev}'},
                    json={'name': 'x', 'checks': CHECKS})
    assert r.status_code == 403
    # 非法 checks（坏 prompt 缺失）→ 400 不带病入库
    r = client.post('/ai/chat/gate-policies', headers=admin_h,
                    json={'name': f'bad-{uuid.uuid4().hex[:6]}', 'checks': [
                        {'name': 'cp', 'mode': 'checkpoint', 'tool': 'w',
                         'args_pattern': 'x'}]})
    assert r.status_code == 400
    # 正常创建 + PATCH 显式传 checks 才更新
    name = f'pol-{uuid.uuid4().hex[:6]}'
    r = client.post('/ai/chat/gate-policies', headers=admin_h,
                    json={'name': name, 'description': 'd', 'checks': CHECKS})
    assert r.status_code == 201
    pid = r.get_json()['id']
    try:
        r2 = client.patch(f'/ai/chat/gate-policies/{pid}', headers=admin_h,
                          json={'enabled': False})
        assert r2.status_code == 200
        with db_conn.cursor() as cur:
            cur.execute("SELECT enabled, checks FROM gate_policies WHERE id=%s",
                        (pid,))
            enabled, checks = cur.fetchone()
        assert enabled is False and len(checks) == 1
    finally:
        _cleanup(pid)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_gate_policies.py -q`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 repo**

`server/utils/gate_policy_repo.py`：

```python
"""门禁策略库（2026-10-11 spec §8）：命名的规则集，批引用即生效。

策略只是模板——生效快照是登记时的 action_expectations 行，策略后改不重写
已登记任务；DELETE 只防护非终态批引用。checks 全量过 validate_checks
（deny/post/checkpoint 混装），不带病入库。
"""
import json
import logging
import secrets

from db import get_db as _default

log = logging.getLogger(__name__)


class PolicyInUseError(RuntimeError):
    """策略仍被非终态批引用，删除被拒绝。"""


def create_policy(user_id: str, name: str, description, checks,
                  enabled: bool = True, get_db=None) -> dict:
    from utils.agent_ledger import validate_checks
    normalized = validate_checks(checks or [])
    name = (name or '').strip()
    if not name or len(name) > 100:
        raise ValueError('策略名必填且不超过 100 字')
    pid = 'gpol_' + secrets.token_hex(6)
    db_ctx = get_db or _default
    with db_ctx() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO gate_policies (id, name, description, checks, "
                " enabled, created_by) VALUES (%s,%s,%s,%s,%s,%s) RETURNING *",
                (pid, name, description,
                 json.dumps(normalized, ensure_ascii=False)
                 if normalized else '[]',
                 bool(enabled), user_id))
            row = cur.fetchone()
            cols = [d[0] for d in cur.description]
        conn.commit()
    return dict(zip(cols, row))


def list_policies(enabled=None, get_db=None) -> list[dict]:
    db_ctx = get_db or _default
    sql = "SELECT * FROM gate_policies"
    params: list = []
    if enabled is not None:
        sql += " WHERE enabled = %s"
        params.append(bool(enabled))
    sql += " ORDER BY updated_at DESC"
    with db_ctx() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]


def get_policy(pid: str, get_db=None) -> dict | None:
    db_ctx = get_db or _default
    with db_ctx() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM gate_policies WHERE id = %s", (pid,))
            row = cur.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))


def update_policy(pid: str, *, name=None, description=None, checks=None,
                  enabled=None, get_db=None) -> dict | None:
    """哨兵语义：None=不更新该字段（checks/name 用 None 判定；enabled 用
    is None 判定——False 是合法值）。checks 显式传入才过校验。"""
    from utils.agent_ledger import validate_checks
    sets, params = [], []
    if name is not None:
        name = (name or '').strip()
        if not name or len(name) > 100:
            raise ValueError('策略名必填且不超过 100 字')
        sets.append('name=%s'); params.append(name)
    if description is not None:
        sets.append('description=%s'); params.append(description)
    if checks is not None:
        normalized = validate_checks(checks)
        sets.append('checks=%s')
        params.append(json.dumps(normalized, ensure_ascii=False))
    if enabled is not None:
        sets.append('enabled=%s'); params.append(bool(enabled))
    if not sets:
        return get_policy(pid, get_db=get_db)
    sets.append('updated_at=NOW()')
    params.append(pid)
    db_ctx = get_db or _default
    with db_ctx() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE gate_policies SET {', '.join(sets)} "
                        "WHERE id=%s RETURNING *", tuple(params))
            row = cur.fetchone()
            cols = [d[0] for d in cur.description] if row else []
        conn.commit()
    return dict(zip(cols, row)) if row else None


def count_active_refs(pid: str, get_db=None) -> int:
    """引用该策略且存在非终态子任务的批数（DELETE 防护依据）。"""
    db_ctx = get_db or _default
    with db_ctx() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM ai_chat_batches b WHERE "
                " b.gate_policy_id = %s AND EXISTS ("
                "   SELECT 1 FROM ai_chat_sessions s WHERE s.batch_id = b.id "
                "   AND s.status IN ('pending','running','paused'))",
                (pid,))
            return cur.fetchone()[0]


def delete_policy(pid: str, get_db=None) -> bool:
    if count_active_refs(pid, get_db=get_db) > 0:
        raise PolicyInUseError('策略仍被运行中/待运行的批任务引用，'
                               '请先解绑或等待其终态')
    db_ctx = get_db or _default
    with db_ctx() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM gate_policies WHERE id=%s", (pid,))
            deleted = cur.rowcount > 0
        conn.commit()
    return deleted
```

（repo 内 `get_db` 参数与模块级导入同名遮蔽——模块级用
`from db import get_db as _default`，函数体内 `db_ctx = get_db or _default`，
与 agent_ledger 同款；上面代码已按此形态。）

- [ ] **Step 4: 实现路由**

`server/routes/ai_gate_policies.py`：

```python
"""门禁策略库 CRUD（2026-10-11 spec §8.1）。

读：登录用户（批创建下拉需要）；写：admin（门禁是治理语义，与
log_operation 留痕一致）。checks 全量过 validate_checks，非法 400 不带病
入库；PATCH 的 checks 显式传入才更新（哨兵语义，与批编辑一致）。
"""
from flask import Blueprint, g, jsonify, request

from auth import login_required
from routes.deps import require_permission   # 与 ai_chat_batches 同款鉴权
from utils import gate_policy_repo
from utils.operation_log import log_operation

ai_gate_policies_bp = Blueprint('ai_gate_policies', __name__,
                                url_prefix='/ai/chat/gate-policies')


@ai_gate_policies_bp.get('')
@login_required
def list_policies():
    enabled = request.args.get('enabled')
    items = gate_policy_repo.list_policies(
        enabled=None if enabled is None else enabled.lower() == 'true')
    return jsonify({'policies': items})


@ai_gate_policies_bp.post('')
@require_permission('admin.ai_chat_admin')
def create_policy():
    body = request.get_json(silent=True) or {}
    try:
        pol = gate_policy_repo.create_policy(
            g.current_user['userId'], body.get('name'),
            body.get('description'), body.get('checks'),
            enabled=bool(body.get('enabled', True)))
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    log_operation('create', 'gate_policy', pol['id'], pol['name'],
                  '创建门禁策略')
    return jsonify(pol), 201


@ai_gate_policies_bp.get('/<pid>')
@login_required
def get_policy(pid):
    pol = gate_policy_repo.get_policy(pid)
    if not pol:
        return jsonify({'error': 'not found'}), 404
    return jsonify(pol)


@ai_gate_policies_bp.patch('/<pid>')
@require_permission('admin.ai_chat_admin')
def update_policy(pid):
    body = request.get_json(silent=True) or {}
    try:
        pol = gate_policy_repo.update_policy(
            pid,
            name=body.get('name') if 'name' in body else None,
            description=body.get('description') if 'description' in body else None,
            checks=body.get('checks') if 'checks' in body else None,
            enabled=body.get('enabled') if 'enabled' in body else None)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    if not pol:
        return jsonify({'error': 'not found'}), 404
    log_operation('update', 'gate_policy', pid, pol.get('name'),
                  '更新门禁策略')
    return jsonify(pol)


@ai_gate_policies_bp.delete('/<pid>')
@require_permission('admin.ai_chat_admin')
def delete_policy(pid):
    try:
        deleted = gate_policy_repo.delete_policy(pid)
    except gate_policy_repo.PolicyInUseError as e:
        return jsonify({'error': str(e), 'code': 'POLICY_IN_USE'}), 409
    if not deleted:
        return jsonify({'error': 'not found'}), 404
    log_operation('delete', 'gate_policy', pid, None, '删除门禁策略')
    return jsonify({'deleted': True})
```

（`require_permission`/`log_operation` 的实际导入路径以
`server/routes/ai_chat_batches.py` 顶部既有写法为准——实现前先读该文件头部，
保持同一导入源。）

`app.py` 蓝图注册（`ai_gate_internal_bp` 注册行旁）：

```python
from routes.ai_gate_policies import ai_gate_policies_bp
app.register_blueprint(ai_gate_policies_bp)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_gate_policies.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add server/utils/gate_policy_repo.py server/routes/ai_gate_policies.py server/app.py server/tests/test_gate_policies.py
git commit -m "feat(gate): 门禁策略库 CRUD（引用防护/validate 把关/审计留痕）"
```

---

### Task 10: 批引用 `gate_policy_id`——创建/编辑 + 派发合并

**Files:**
- Modify: `server/utils/batch_repo.py:245-283`（create_batch）、`:915+`（update_batch_config）
- Modify: `server/routes/ai_chat_batches.py:92-143`（create）、`:303-353`（update_config）
- Modify: `server/utils/batch_engine.py:1740-1756`（`_fetch_action_checks` 合并）
- Test: `server/tests/test_gate_policies.py` 追加

**Interfaces:**
- Consumes: Task 9 的 repo。
- Produces: `create_batch(..., gate_policy_id=None)`；`update_batch_config(gate_policy_id=_UNSET)`；`_fetch_action_checks` 返回「策略 ∪ 批（模板兜底）」合并数组（先策略后批侧，同名批侧覆盖）。

- [ ] **Step 1: 写失败测试**

（`_fetch_action_checks` 是 `BatchWorker` 的方法：测试用 `BatchWorker.__new__(BatchWorker)`
绕过 `__init__` 取未绑定调用即可，不起 worker 线程。追加到 `test_gate_policies.py`：）

```python
    def test_fetch_action_checks_merge(self, db_conn):
        """合并语义（spec §8.2）：先策略后批侧、同名批侧覆盖、模板兜底保留。"""
        from utils.batch_engine import BatchWorker
        pid = repo.create_policy('user-admin', f'pol-{uuid.uuid4().hex[:6]}',
                                 None, CHECKS)['id']
        bid = str(uuid.uuid4())
        own = [{'name': '写前读知识库', 'mode': 'checkpoint', 'check_type': 'tool',
                'tool': 'write', 'args_pattern': 'outputs/own', 'timing': 'before',
                'prompt': '批侧覆盖版提示词', 'max_checks': 1}]
        with db_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ai_chat_batches (id, user_id, name, prompt, total,"
                " status, action_checks, gate_policy_id) "
                "VALUES (%s,'user-admin','p','p',1,'pending',%s,%s)",
                (bid, json.dumps(own), pid))
        db_conn.commit()
        try:
            worker = BatchWorker.__new__(BatchWorker)
            merged = worker._fetch_action_checks(bid)
            names = [c['name'] for c in merged]
            assert names.count('写前读知识库') == 1          # 同名去重
            own_hit = [c for c in merged if c['name'] == '写前读知识库']
            assert own_hit[0]['args_pattern'] == 'outputs/own'   # 批侧覆盖
            assert any(c['mode'] == 'checkpoint' and
                       c.get('prompt', '').startswith('请先读取') for c in merged)
        finally:
            with db_conn.cursor() as cur:
                cur.execute("DELETE FROM ai_chat_batches WHERE id=%s", (bid,))
                cur.execute("DELETE FROM gate_policies WHERE id=%s", (pid,))
        db_conn.commit()

    def test_create_batch_rejects_missing_policy(self, db_conn):
        from utils.batch_repo import create_batch
        with pytest.raises(ValueError, match='策略'):
            create_batch('user-admin', name='p', prompt='p', template_id=None,
                         files=[{'name': 'a.txt', 'path': 'batch-staging/u/a.txt'}],
                         gate_policy_id='gpol_nope')
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_gate_policies.py -q -k "fetch_action_checks or rejects_missing"`
Expected: FAIL（签名无 gate_policy_id / 合并未实现）

- [ ] **Step 3: 实现**

`batch_repo.create_batch`：签名加 `gate_policy_id: str | None = None`；函数体开头加校验：

```python
    if gate_policy_id:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT enabled FROM gate_policies WHERE id = %s",
                            (gate_policy_id,))
                row = cur.fetchone()
        if row is None:
            raise ValueError('引用的门禁策略不存在')
        if not row[0]:
            raise ValueError('引用的门禁策略已停用')
```

INSERT 列与 VALUES 各加 `gate_policy_id`（参数 `gate_policy_id`）。

`update_batch_config`：签名加 `gate_policy_id=_UNSET`；显式传入时先做同款校验
（`None` 允许 = 解绑）再 UPDATE 该列。

`routes/ai_chat_batches.py` create：

```python
    gate_policy_id = body.get('gatePolicyId') or None
    if gate_policy_id:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT enabled FROM gate_policies WHERE id=%s",
                            (gate_policy_id,))
                prow = cur.fetchone()
        if prow is None:
            return jsonify({'error': '引用的门禁策略不存在'}), 400
        if not prow[0]:
            return jsonify({'error': '引用的门禁策略已停用'}), 400
```

`create_batch(..., gate_policy_id=gate_policy_id)`。update_config 的哨兵段：

```python
    if 'gatePolicyId' in body:
        gp = body.get('gatePolicyId') or None
        if gp:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT enabled FROM gate_policies WHERE id=%s",
                                (gp,))
                    prow = cur.fetchone()
            if prow is None:
                return jsonify({'error': '引用的门禁策略不存在'}), 400
            if not prow[0]:
                return jsonify({'error': '引用的门禁策略已停用'}), 400
        patch_kwargs['gate_policy_id'] = gp
```

并在 `'action_checks' in body` 的同步分支后补：换策略/解绑也要同步期望——
把同步条件改为 `if 'action_checks' in body or 'gatePolicyId' in body:`，
`sync_batch_expectations(batch_id, checks or None)` 的 checks 参数改为
**合并集**：同步前现查合并（抽 `_merged_action_checks(cur, batch_id)` 小函数
供路由与 worker 共用，避免两处合并逻辑漂移——见下）。

`batch_engine._fetch_action_checks` 改造（合并唯一事实源在 repo 层更稳——
把合并抽到 `gate_policy_repo.merged_action_checks(batch_id, get_db=None) -> list|None`，
worker 与路由都调它）：

```python
def merged_action_checks(batch_id: str, get_db=None) -> list | None:
    """批生效规则 = 策略 checks ∪ 批自身 action_checks（批缺省回退模板）。

    登记顺序=数组顺序：先策略后批侧——register_session_expectations 按 name
    幂等覆盖，后登记的批侧同名规则覆盖策略侧（"更具体者赢"）。策略停用/
    不存在 → 跳过并留日志（已登记期望不受影响）。"""
    db_ctx = get_db or _default
    with db_ctx() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT action_checks, template_id, gate_policy_id "
                        "FROM ai_chat_batches WHERE id = %s", (batch_id,))
            row = cur.fetchone()
            if not row:
                return None
            checks, template_id, policy_id = row[0], row[1], row[2]
            policy_checks: list = []
            if policy_id:
                cur.execute("SELECT checks, enabled FROM gate_policies "
                            "WHERE id = %s", (policy_id,))
                prow = cur.fetchone()
                if prow is None:
                    log.warning('batch %s 引用的门禁策略 %s 已删除，跳过',
                                batch_id, policy_id)
                elif not prow[1]:
                    log.warning('batch %s 引用的门禁策略 %s 已停用，跳过',
                                batch_id, policy_id)
                else:
                    policy_checks = prow[0] or []
            if not checks and template_id:
                cur.execute("SELECT action_checks FROM ai_chat_prompt_templates "
                            "WHERE id = %s", (template_id,))
                trow = cur.fetchone()
                checks = trow[0] if trow else None
            merged = [*(policy_checks or []), *(checks or [])]
            return merged or None
```

`batch_engine._fetch_action_checks` 改为一行委托：

```python
    def _fetch_action_checks(self, batch_id: str):
        """入口 A/B/策略（spec §8.2）：合并事实源在 gate_policy_repo。"""
        from utils.gate_policy_repo import merged_action_checks
        return merged_action_checks(batch_id)
```

路由 update_config 的同步分支用同一函数取合并集再做
`sync_batch_expectations`。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_gate_policies.py tests/test_batch_engine.py tests/test_batch_routes.py -q`
Expected: PASS（批既有用例全绿——create_batch 新参数有默认值，旧调用不破）

- [ ] **Step 5: Commit**

```bash
git add server/utils/batch_repo.py server/routes/ai_chat_batches.py server/utils/batch_engine.py server/utils/gate_policy_repo.py server/tests/test_gate_policies.py
git commit -m "feat(gate): 批引用门禁策略——创建/编辑校验与派发合并（先策略后批侧）"
```

---

### Task 11: 前端——API client + 批对话框（策略下拉 + checkpoint 规则行）

**Files:**
- Create: `src/api/gatePolicies.ts`
- Modify: `src/components/ai-chat/CreateBatchDialog.vue`（gateChecks 区 + 提交 payload）
- Modify: `src/components/ai-chat/EditBatchConfigDialog.vue`（同款两处）
- Test: `npx vue-tsc --noEmit`（类型门禁）+ 手动冒烟（e2e 在 Task 13）

**Interfaces:**
- Consumes: Task 9 的 `/ai/chat/gate-policies`、Task 10 的 `gatePolicyId` 批字段。
- Produces: 批创建/编辑请求带 `gatePolicyId` 与 checkpoint 型 `action_checks` 条目。

- [ ] **Step 1: API client**

`src/api/gatePolicies.ts`（对齐 `src/api/aiChat.ts` 的 get/post/patch/del 惯例）：

```ts
/**
 * 门禁策略库（2026-10-11 spec §8）：命名的规则集，批引用即生效。
 */
import { get, post, patch, del } from '@/utils/request'

export type GateCheckMode = 'post' | 'pre' | 'checkpoint'

export interface GateCheckRow {
  name: string
  check_type?: 'tool' | 'file' | 'db_record' | 'verifier'
  tool?: string
  args_pattern?: string
  min_count?: number
  require_state?: string
  scope?: 'session' | 'tree'
  mode?: GateCheckMode
  timing?: 'before' | 'after'
  prompt?: string
  max_checks?: number
  assert_spec?: { tool: string; args_pattern: string; min_count: number } | null
  subagents?: string[] | null
  apply_to?: { batch_seq?: number[]; input_file_glob?: string } | null
  effect_spec?: Record<string, unknown> | null
  rubric?: string
}

export interface GatePolicy {
  id: string
  name: string
  description: string | null
  checks: GateCheckRow[]
  enabled: boolean
  createdBy?: string | null
  updatedAt?: string | null
}

export function listGatePolicies(enabled?: boolean) {
  return get<{ policies: GatePolicy[] }>(
    '/ai/chat/gate-policies', enabled === undefined ? undefined : { enabled: String(enabled) })
}
export function createGatePolicy(body: { name: string; description?: string; checks: GateCheckRow[]; enabled?: boolean }) {
  return post<GatePolicy>('/ai/chat/gate-policies', body)
}
export function updateGatePolicy(id: string, body: Partial<{ name: string; description: string; checks: GateCheckRow[]; enabled: boolean }>) {
  return patch<GatePolicy>(`/ai/chat/gate-policies/${encodeURIComponent(id)}`, body)
}
export function deleteGatePolicy(id: string) {
  return del<{ deleted: boolean }>(`/ai/chat/gate-policies/${encodeURIComponent(id)}`)
}
```

- [ ] **Step 2: 批对话框改造（先读组件全文再动手）**

两个对话框同款改法（以 `CreateBatchDialog.vue` 为主，Edit 同步）：

a. 门禁区顶部（`gateEnabled` checkbox 旁）加策略下拉：

```vue
        <ElSelect v-model="gatePolicyId" data-test="gate-policy-select"
                  clearable placeholder="引用门禁策略（可选）"
                  :disabled="!gateEnabled" style="width: 260px">
          <ElOption v-for="p in policies" :key="p.id" :value="p.id"
                    :label="`${p.name}（${p.checks.length} 条规则）`" />
        </ElSelect>
```

```ts
import { listGatePolicies, type GatePolicy } from '@/api/gatePolicies'
const policies = ref<GatePolicy[]>([])
const gatePolicyId = ref<string>('')
watch(() => props.visible ?? true, () => { listGatePolicies().then(r => { policies.value = r.policies }).catch(() => {}) }, { immediate: true })
```

b. 规则行加「类型」选择与 checkpoint 条件字段（`gate-card` 内、既有
name/tool/args_pattern 行上方加一行模式选择；checkpoint 时显示
timing/prompt/max_checks/assert 三个输入）：

```vue
            <ElRadioGroup v-model="c.mode" size="small" data-test="gate-row-mode">
              <ElRadioButton value="post">终态期望</ElRadioButton>
              <ElRadioButton value="checkpoint">过程检查</ElRadioButton>
            </ElRadioGroup>
            <template v-if="c.mode === 'checkpoint'">
              <ElRadioGroup v-model="c.timing" size="small">
                <ElRadioButton value="before">执行前</ElRadioButton>
                <ElRadioButton value="after">执行后</ElRadioButton>
              </ElRadioGroup>
              <ElInputNumber v-model="c.max_checks" :min="1" :max="20"
                             data-test="gate-row-max-checks" />
              <ElInput v-model="c.prompt" type="textarea" :rows="2"
                       maxlength="2000" show-word-limit
                       placeholder="检查提示词（命中后注入给模型）"
                       data-test="gate-row-prompt" />
              <ElCheckbox v-model="c.hasAssert">终态兜底断言</ElCheckbox>
              <template v-if="c.hasAssert">
                <ElInput v-model="c.assert_tool" placeholder="断言工具（如 read）" />
                <ElInput v-model="c.assert_pattern" placeholder="断言 args 正则（如 docs/kb/）" />
                <ElInputNumber v-model="c.assert_min_count" :min="1" />
              </template>
            </template>
```

c. 行类型与提交归一（`emptyCheck()` 返回对象加
`mode:'post', timing:'before', max_checks:1, hasAssert:false, assert_tool:'', assert_pattern:'', assert_min_count:1`；
`gateChecks` 行类型 `GateCheckRow` 扩展同字段；提交时归一——

```ts
function serializeChecks(rows: GateCheckRow[]) {
  return rows.map((c) => {
    if (c.mode !== 'checkpoint') {
      const { mode, timing, prompt, max_checks, hasAssert, assert_tool,
              assert_pattern, assert_min_count, ...post } = c
      return post
    }
    return {
      name: c.name, mode: 'checkpoint', check_type: 'tool',
      tool: c.tool, args_pattern: c.args_pattern, timing: c.timing,
      prompt: c.prompt, max_checks: c.max_checks,
      assert_spec: c.hasAssert
        ? { tool: c.assert_tool, args_pattern: c.assert_pattern,
            min_count: c.assert_min_count }
        : undefined,
      subagents: c.subagents ?? undefined,
    }
  })
}
```

创建 payload 加 `gatePolicyId: gatePolicyId.value || undefined`；编辑对话框
PATCH body 加 `gatePolicyId`（显式键——清空时传 `null` 解绑，未动不传）；
回填时按 mode 分流到行对象（`hasAssert: !!c.assert_spec` 展开）。

- [ ] **Step 3: 类型门禁 + 冒烟**

Run: `npx vue-tsc --noEmit`
Expected: 0 error。起 dev 栈手动冒烟：创建对话框显示策略下拉、加一条过程检查行能提交（浏览器 DevTools 看请求体）。

- [ ] **Step 4: Commit**

```bash
git add src/api/gatePolicies.ts src/components/ai-chat/CreateBatchDialog.vue src/components/ai-chat/EditBatchConfigDialog.vue
git commit -m "feat(gate): 批对话框引用门禁策略 + 过程检查规则行"
```

---

### Task 12: 管理页「门禁策略」

**Files:**
- Create: `src/views/admin/GatePolicies.vue`
- Modify: `src/views/admin/hub/settingsCatalog.ts`（AI 能力组 items 加条目）
- Test: `npx vue-tsc --noEmit` + 手动冒烟

**Interfaces:**
- Consumes: Task 9 端点、Task 11 的 `GateCheckRow` 类型与行编辑形态。

- [ ] **Step 1: 页面组件**

`src/views/admin/GatePolicies.vue`（对齐 `AiSkillOpt.vue` 的 admin 页骨架惯例：
外层卡片 + 工具栏 + 表格 + 对话框编辑器）：

```vue
<template>
  <div class="gate-policies">
    <ElCard>
      <template #header>
        <div class="toolbar">
          <span>门禁策略（{{ policies.length }}）</span>
          <ElButton type="primary" data-test="policy-new" @click="openEditor()">新建策略</ElButton>
        </div>
      </template>
      <ElTable :data="policies" data-test="policy-table">
        <ElTableColumn prop="name" label="名称" min-width="160" />
        <ElTableColumn label="规则" width="180">
          <template #default="{ row }">
            {{ (row.checks || []).length }} 条
            （deny {{ cnt(row, 'pre') }} / 终态 {{ cnt(row, 'post') }} /
             过程 {{ cnt(row, 'checkpoint') }}）
          </template>
        </ElTableColumn>
        <ElTableColumn label="启用" width="80">
          <template #default="{ row }">
            <ElSwitch :model-value="row.enabled" @change="(v: boolean) => toggle(row, v)" />
          </template>
        </ElTableColumn>
        <ElTableColumn prop="updatedAt" label="更新时间" width="180" />
        <ElTableColumn label="操作" width="140">
          <template #default="{ row }">
            <ElButton link @click="openEditor(row)">编辑</ElButton>
            <ElButton link type="danger" @click="remove(row)">删除</ElButton>
          </template>
        </ElTableColumn>
      </ElTable>
    </ElCard>

    <ElDialog v-model="editorVisible" :title="editingId ? '编辑策略' : '新建策略'" width="720px">
      <ElForm label-width="90px">
        <ElFormItem label="名称"><ElInput v-model="form.name" maxlength="100" /></ElFormItem>
        <ElFormItem label="描述"><ElInput v-model="form.description" /></ElFormItem>
        <ElFormItem label="启用"><ElSwitch v-model="form.enabled" /></ElFormItem>
        <ElFormItem label="规则">
          <!-- 规则行编辑：与 CreateBatchDialog 的 gate-card 同一套字段
               （类型/触发工具/args 正则/时机/提示词/预算/断言）——抽为共享
               子组件 GateCheckRows.vue 放 src/components/admin/ 下，两处复用 -->
          <GateCheckRows v-model="form.checks" />
        </ElFormItem>
      </ElForm>
      <template #footer>
        <ElButton @click="editorVisible = false">取消</ElButton>
        <ElButton type="primary" data-test="policy-save" @click="save">保存</ElButton>
      </template>
    </ElDialog>
  </div>
</template>
```

脚本段：`listGatePolicies/createGatePolicy/updateGatePolicy/deleteGatePolicy`
增删改查 + `ElMessageBox.confirm` 删除确认（409 POLICY_IN_USE 时提示先解绑）。
规则行编辑器抽 `src/components/admin/GateCheckRows.vue` 共享组件（Task 11 的
对话框行编辑同步迁移到该组件——避免两份规则行实现漂移）。

- [ ] **Step 2: settingsCatalog 注册**

AI 能力组 items 中 `ai-skills` 条目后加：

```ts
    { id: 'gate-policies', label: '门禁策略', perm: 'admin.ai_chat_admin', icon: 'Lock',
      component: () => import('@/views/admin/GatePolicies.vue') },
```

- [ ] **Step 3: 类型门禁 + 冒烟**

Run: `npx vue-tsc --noEmit`
Expected: 0 error。手动冒烟：管理页新建策略（含一条过程检查规则）→ 列表出现 → 批创建对话框下拉可见。

- [ ] **Step 4: Commit**

```bash
git add src/views/admin/GatePolicies.vue src/components/admin/GateCheckRows.vue src/views/admin/hub/settingsCatalog.ts src/components/ai-chat/CreateBatchDialog.vue src/components/ai-chat/EditBatchConfigDialog.vue
git commit -m "feat(gate): 管理页门禁策略（列表/编辑器/规则行共享组件）"
```

---

### Task 13: E2E——真 LLM 场景扩展

**Files:**
- Modify: `e2e/ai-full/agent-action-gate.spec.ts`（先读全文，沿用其 harness/helpers）

**Interfaces:**
- Consumes: 全链路（迁移/判定/插件/策略/批）。

- [ ] **Step 1: 通读既有 spec**，确认建批 helper（`createBatch` 的 `action_checks`）、
  等终态 helper、门禁徽标断言选择器（`.bg-child` 行内「门禁 ✓/×n」）。

- [ ] **Step 2: 追加场景（真 LLM，逐条可独立运行）**

场景体骨架（按既有 spec 的 helper 风格填充）：

1. **before 闭环**：`createBatch({ action_checks: [checkpoint(before) write outputs/report + prompt '请先读取知识库 docs/kb/ 再写入', assert_spec read docs/kb/], prompt: '阅读 uploads/in.txt 后把要点写入 outputs/report.md' })` → 等终态 → 断言子任务行「门禁 ✓」且账本里出现 read docs/kb（`GET /ai/chat/batches/<bid>/children/<sid>/tool-calls`）；
2. **预算防冗余**：同上规则 `max_checks=1`，模型第二次写不再被拦（断言期望行 `fired_count=1`——经 admin 查询或账本里只有一次拦截痕迹）；
3. **双保险 gate_retry**：checkpoint 规则 prompt 写成不可能自查满足的指令（如 assert 要求 read 一个不存在目录），开 `gateRetry: true` → 任务终态 fail → 修正轮注入 → 断言 retry_count=1 且终态 completed（若模型最终补不齐则断言 failed + `action_gate:` 前缀错误，两种终态都算场景有效——断言「门禁参与判定」本身）；
4. **after 场景**：按探针结论形态——配 after 规则，断言模型写完后收到检查提示词（消息导出里 write 结果为错误/含提示词）并重写；
5. **策略复用**：API 建策略 → 两个批分别引用 → 各自子任务 `action_expectations` 有对应行（DB 断言，`dbExec` 模式参照 skillopt-perf.spec.ts）；解绑后重新派发不再登记。

- [ ] **Step 3: 跑 E2E**

Run: `npx playwright test e2e/ai-full/agent-action-gate.spec.ts`
Expected: 既有 4 场景 + 新场景全绿（真 LLM，单场景预算 2-4 分钟）。

- [ ] **Step 4: Commit**

```bash
git add e2e/ai-full/agent-action-gate.spec.ts
git commit -m "test(gate): 过程检查/策略复用真 LLM E2E 场景"
```

---

### Task 14: 全量回归与收尾

**Files:**
- 无新文件；产出回归证据。

- [ ] **Step 1: 后端全量**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q --ignore=tests/stress`
Expected: 0 failed。

- [ ] **Step 2: 前端全量**

Run: `npx vitest run`
Expected: 0 failed。

- [ ] **Step 3: 收尾检查**

- 迁移双注册护栏：`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_migration_boot_registration.py -q` 绿；
- spec §2 非目标未被越界（MCP 入口 C 未扩、无全局默认策略、无文件内容断言）；
- 部署说明三条写入提交信息或 PR 描述：迁移随启动生效 / 插件需重启 OC serve / after 注入方式与探针结论。

- [ ] **Step 4: Commit（如有零散修正）+ 推送**

```bash
git push origin main
```
