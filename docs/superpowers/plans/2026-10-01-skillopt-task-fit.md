# SkillOpt 任务拟合 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 以任务会话（attempt）为维度，把执行轨迹与 skill/agent frontmatter 声明的步骤规则对照，得出步骤级拟合度（图标呈现于管理页），配套 AI 步骤生成器、偏差诊断与版本效果对比。

**Architecture:** 迁移新增 `ai_skill_fit_results`/`ai_skill_def_versions` 表与 `ai_skill_invocations.subtask_id` 列；拟合引擎为纯函数模块 `utils/skill_fit.py`（frontmatter 解析缓存 + 贪心顺序匹配 + 评分），attempt 收敛钩子（chat_persist 交互侧 + batch_engine 批侧）调 `compute_for_session` 落库；5 组 admin API 挂既有 `ai_execution_admin_bp`（前缀 `/ai/chat/admin`）；UI 在 `AiSkillOpt.vue` 加「任务拟合」主 tab（分段点阵图标 + 明细 + 版本对比卡 + 生成器/诊断对话框）。

**Tech Stack:** Flask + psycopg2（PG 真库测试）、PyYAML 6.0.1（frontmatter 解析，已可用）、pytest（`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`）、Vue3 + Element Plus + Vitest。

**Spec:** `docs/design/ai/SkillOpt任务拟合设计.md`（执行者两份都要读）

## Global Constraints

- 迁移文件放 `server/migrations/2026_10_01_skill_fit_tables.py`，**必须暴露 `run()`**（迁移执行器契约——2026_09_29_embedding_provider 曾因暴露 main() 从未被执行）；幂等（IF NOT EXISTS / ON CONFLICT）。
- 拟合判定的 expect 正则校验沿用动作门禁口径：`utils.agent_ledger.validate_pg_regex`（PG `~`）。
- 收敛钩子 best-effort：任何异常只记日志，不影响主流程（同 `collect_skill_invocations` 策略）。
- 所有新端点挂 `ai_execution_admin_bp`（`routes/ai_session_admin.py`，前缀 `/ai/chat/admin`），权限装饰器与相邻 skill-analytics 端点一致（先读相邻端点的装饰器写法再照抄）。
- 中文注释/文案；测试连真实 dev PG（`db_conn` fixture）；Windows 下命令统一 `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest ...`。
- 前端调试/治理信息只进管理页，不回 AI 会话页。
- 拟合轨迹统一事实源是 `agent_tool_calls` 账本（含子代理，root_session_id + subtask_id 双标识）；`ai_execution_events` 的 skill.invoke 仅参考。

## File Structure

| 文件 | 动作 | 职责 |
|---|---|---|
| `server/migrations/2026_10_01_skill_fit_tables.py` | 新建 | fit_results/def_versions 表 + invocations.subtask_id 列 |
| `server/utils/skill_fit.py` | 新建 | frontmatter 解析缓存、贪心匹配纯函数、compute_attempt_fit/compute_for_session |
| `server/utils/skillopt.py` | 修改 | _platform_session_id 回退子代理、subtask 归属写入 |
| `server/routes/ai_session_admin.py` | 修改 | fits/def-versions/generate/apply/diagnose 端点 |
| `server/utils/chat_persist.py` | 修改 | 交互侧收敛钩子接线 |
| `server/utils/batch_engine.py` | 修改 | 批侧终态钩子接线 |
| `src/views/admin/AiSkillOpt.vue` | 修改 | 任务拟合 tab（列表/图标/明细/版本对比/生成器/诊断） |
| `server/tests/test_skill_fit.py` | 新建 | 引擎单测 + 编排测试 |
| `server/tests/test_skillopt_collection.py` | 新建 | 采集修复测试 |
| `server/tests/test_skill_fit_routes.py` | 新建 | 端点测试 |
| `src/components/admin/SkillFitBadge.vue` | 新建 | 分段点阵拟合图标 |

---

### Task 1: 迁移——fit 结果表 / 版本表 / invocations.subtask_id 列

**Files:**
- Create: `server/migrations/2026_10_01_skill_fit_tables.py`
- Test: 直接跑 `python init_db.py` + `python migrations/2026_10_01_skill_fit_tables.py` 验证（迁移契约：暴露 `run()`、幂等）

**Interfaces:**
- Produces: 表 `ai_skill_fit_results`（结构见 spec §3.2）、`ai_skill_def_versions`（spec §3.4）、列 `ai_skill_invocations.subtask_id VARCHAR(100) NULL` + 索引 `idx_skill_inv_subtask ON ai_skill_invocations(subtask_id)`。

- [ ] **Step 1: 写迁移文件**（参照 `migrations/2026_09_17_execution_audit_tables.py` 的 MIGRATIONS 列表结构）

```python
"""SkillOpt 任务拟合：拟合结果表 / 定义版本表 / 子代理归属列（2026-10-01）。

- ai_skill_fit_results：attempt 收敛时轨迹 vs 定义步骤的拟合结果（spec §3.2）
- ai_skill_def_versions：定义版本实体（content_hash 可读化，spec §3.4）
- ai_skill_invocations.subtask_id：subagent skill 调用的归属标注（spec §5c）
"""


def run():
    import secrets
    from db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS ai_skill_fit_results (
                  id            VARCHAR(100) PRIMARY KEY,
                  attempt_id    VARCHAR(100) NOT NULL
                                REFERENCES ai_execution_attempts(id) ON DELETE CASCADE,
                  session_id    VARCHAR(100) NOT NULL,
                  def_kind      VARCHAR(30) NOT NULL,
                  def_name      VARCHAR(300) NOT NULL,
                  def_hash      VARCHAR(64),
                  steps_total   INTEGER NOT NULL DEFAULT 0,
                  steps_hit     INTEGER NOT NULL DEFAULT 0,
                  score         INTEGER NOT NULL DEFAULT 0,
                  status        VARCHAR(20) NOT NULL DEFAULT 'unknown',
                  per_step      JSONB NOT NULL DEFAULT '[]'::jsonb,
                  diagnosis     JSONB,
                  computed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )""")
            cur.execute("CREATE INDEX IF NOT EXISTS ai_skill_fit_attempt_idx "
                        "ON ai_skill_fit_results(attempt_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS ai_skill_fit_def_idx "
                        "ON ai_skill_fit_results(def_name, computed_at DESC)")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS ai_skill_def_versions (
                  id             VARCHAR(100) PRIMARY KEY,
                  def_kind       VARCHAR(30) NOT NULL,
                  def_name       VARCHAR(300) NOT NULL,
                  content_hash   VARCHAR(64) NOT NULL,
                  version_label  VARCHAR(200),
                  note           TEXT,
                  first_seen_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                  UNIQUE (def_kind, def_name, content_hash)
                )""")
            cur.execute("ALTER TABLE ai_skill_invocations "
                        "ADD COLUMN IF NOT EXISTS subtask_id VARCHAR(100)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_skill_inv_subtask "
                        "ON ai_skill_invocations(subtask_id)")
            conn.commit()
```

（若项目的迁移加载器要求 `secrets` 等 import 放模块顶部，按同目录其他迁移文件的实际风格对齐——先读 `migrations/2026_09_17_execution_audit_tables.py` 全文再落笔。）

- [ ] **Step 2: 验证**

Run: `cd server && python migrations/2026_10_01_skill_fit_tables.py && python migrations/2026_10_01_skill_fit_tables.py && python -c "import psycopg2; from config import DB_CONFIG; c=psycopg2.connect(**DB_CONFIG); cur=c.cursor(); cur.execute(\"SELECT table_name FROM information_schema.tables WHERE table_name IN ('ai_skill_fit_results','ai_skill_def_versions')\"); print(cur.fetchall()); cur.execute(\"SELECT column_name FROM information_schema.columns WHERE table_name='ai_skill_invocations' AND column_name='subtask_id'\"); print(cur.fetchall())"`
Expected: 两个迁移执行均无错（幂等）；两张表 + subtask_id 列在位

- [ ] **Step 3: Commit**

```bash
git add server/migrations/2026_10_01_skill_fit_tables.py
git commit -m "feat(skillopt): 拟合结果/定义版本表与子代理归属列迁移"
```

---

### Task 2: utils/skill_fit.py——frontmatter 解析 + 贪心匹配纯函数 + 编排

**Files:**
- Create: `server/utils/skill_fit.py`
- Test: `server/tests/test_skill_fit.py`（新建）

**Interfaces:**
- Produces:
  - `parse_fit_steps(text: str) -> list[dict]`：md 全文 → `[{id,name,expect:[{tool,args_pattern?}]}]`；无 fit 块 → `[]`；yaml 坏 → 抛 `FitParseError`
  - `parse_cached(path: str, content_hash: str) -> list[dict]`：读文件解析，进程内 LRU（容量 256）按 hash 缓存；文件缺失且缓存命中 → 缓存值；均无 → `FitParseError`
  - `match_steps(steps: list[dict], trace: list[dict]) -> dict`：trace=`[{tool,args,occurredAt}]`；返回 `{'per_step': [{id,name,status,evidence}], 'steps_total', 'steps_hit', 'score', 'status'}`（status∈fit/partial/diverged/no_trace；skipped 不计分母）
  - `compute_attempt_fit(attempt_id: str, get_db=None) -> list[dict]`：编排（定义发现→轨迹→匹配→upsert 结果行），返回结果行列表
  - `compute_for_session(session_id: str, get_db=None) -> list[dict] | None`：按 session 现查最新 attempt 后委托 compute_attempt_fit；无 attempt → None（接线用）

- [ ] **Step 1: 写失败测试**（新建 `tests/test_skill_fit.py`）

```python
# -*- coding: utf-8 -*-
"""SkillOpt 任务拟合单元（设计 docs/design/ai/SkillOpt任务拟合设计.md §3-§4）。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.skill_fit import (FitParseError, match_steps, parse_cached,
                             parse_fit_steps)

SKILL_MD = """---
description: 数据拉取
fit:
  steps:
    - id: clone
      name: 克隆仓库
      expect:
        - tool: bash
          args_pattern: 'git clone'
    - id: inspect
      name: 检查结构
      expect:
        - tool: glob
    - id: report
      name: 写报告
      expect:
        - tool: write
          args_pattern: 'outputs?/'
---
正文内容忽略。
"""

NO_FIT_MD = """---
description: 无步骤声明
---
正文。
"""


def test_parse_fit_steps_ok():
    steps = parse_fit_steps(SKILL_MD)
    assert [s['id'] for s in steps] == ['clone', 'inspect', 'report']
    assert steps[0]['expect'] == [{'tool': 'bash', 'args_pattern': 'git clone'}]


def test_parse_fit_steps_no_fit_block():
    assert parse_fit_steps(NO_FIT_MD) == []


def test_parse_fit_steps_bad_yaml_raises():
    with pytest.raises(FitParseError):
        parse_fit_steps("---\nfit: [unclosed\n---\nbody")


def test_parse_cached_by_hash(tmp_path):
    f = tmp_path / 'SKILL.md'
    f.write_text(SKILL_MD, encoding='utf-8')
    import hashlib
    h = hashlib.sha256(SKILL_MD.encode('utf-8')).hexdigest()
    assert len(parse_cached(str(f), h)) == 3
    f.unlink()                       # 文件删除后按 hash 命中缓存
    assert len(parse_cached(str(f), h)) == 3
    with pytest.raises(FitParseError):
        parse_cached(str(tmp_path / 'gone-other.md'), 'deadbeef')


def _t(tool, args='', at=0):
    return {'tool': tool, 'args': args, 'occurredAt': f'2026-10-01T00:00:{at:02d}'}


def test_match_steps_all_hit_in_order():
    steps = parse_fit_steps(SKILL_MD)
    trace = [_t('bash', 'git clone https://x', 1), _t('glob', '**/*.py', 2),
             _t('write', 'outputs/report.md', 3)]
    r = match_steps(steps, trace)
    assert r['status'] == 'fit' and r['score'] == 100
    assert all(p['status'] == 'hit' for p in r['per_step'])
    assert r['per_step'][0]['evidence'][0]['args'] == 'git clone https://x'


def test_match_steps_out_of_order_is_miss():
    steps = parse_fit_steps(SKILL_MD)
    # write 先于 bash：贪心顺序下 clone 错过 bash（被 write 消耗前的指针…）
    # 指针扫描：clone 找 bash——轨迹里 bash 在 write 之后出现仍可命中（指针
    # 只前进不回头，但 clone 是第一步，bash 在位置 1 命中）——真正乱序用例：
    trace = [_t('write', 'outputs/r.md', 1), _t('bash', 'git clone x', 2),
             _t('glob', '**/*', 3)]
    r = match_steps(steps, trace)
    # clone 命中位置 2；inspect 命中位置 3；report 需要的 write 在位置 1
    # （指针已过）→ miss
    statuses = {p['id']: p['status'] for p in r['per_step']}
    assert statuses == {'clone': 'hit', 'inspect': 'hit', 'report': 'miss'}
    assert r['status'] == 'partial'


def test_match_steps_no_trace():
    r = match_steps(parse_fit_steps(SKILL_MD), [])
    assert r['status'] == 'no_trace' and r['score'] == 0


def test_match_steps_skipped_not_scored():
    steps = parse_fit_steps(SKILL_MD)
    steps.append({'id': 'free', 'name': '无期望步骤', 'expect': []})
    trace = [_t('bash', 'git clone x', 1), _t('glob', '**/*', 2),
             _t('write', 'outputs/r.md', 3)]
    r = match_steps(steps, trace)
    assert r['steps_total'] == 3                    # skipped 不计分母
    assert {p['id']: p['status'] for p in r['per_step']}['free'] == 'skipped'
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_skill_fit.py -v`
Expected: FAIL（`No module named 'utils.skill_fit'`）

- [ ] **Step 3: 实现 `utils/skill_fit.py` 的解析与匹配部分**

```python
"""SkillOpt 任务拟合（设计 docs/design/ai/SkillOpt任务拟合设计.md §3-§4）。

以 attempt 为维度：定义（SKILL.md / agent md 的 frontmatter fit.steps）与
实际工具调用轨迹做贪心顺序匹配，得出步骤级拟合度并落
ai_skill_fit_results。判定为纯规则（无 LLM）；轨迹统一事实源是
agent_tool_calls 账本（含子代理，root+subtask 双标识）。
"""
import hashlib
import json
import logging
import re
from collections import OrderedDict

import yaml

log = logging.getLogger(__name__)

_PARSE_CACHE_MAX = 256
_parse_cache: 'OrderedDict[str, list[dict]]' = OrderedDict()


class FitParseError(ValueError):
    """frontmatter 缺失/解析失败（定义不参与拟合，结果行标 parse_error）。"""


def parse_fit_steps(text: str) -> list[dict]:
    """从 md 全文解析 frontmatter fit.steps。无 fit 块 → []；坏 yaml → 抛错。"""
    m = re.match(r'\A---\r?\n(.*?)\r?\n---\r?\n', text or '', re.S)
    if not m:
        return []
    meta = yaml.safe_load(m.group(1))
    if not meta:
        return []
    fit = meta.get('fit')
    if not isinstance(fit, dict):
        return []
    steps = fit.get('steps')
    if steps in (None, []):
        return []
    if not isinstance(steps, list):
        raise FitParseError('fit.steps 必须是数组')
    out = []
    for i, s in enumerate(steps):
        if not isinstance(s, dict):
            raise FitParseError(f'fit.steps[{i}] 必须是对象')
        expect = s.get('expect') or []
        if not isinstance(expect, list):
            raise FitParseError(f'fit.steps[{i}].expect 必须是数组')
        out.append({
            'id': str(s.get('id') or f'step-{i + 1}'),
            'name': str(s.get('name') or s.get('id') or f'步骤 {i + 1}'),
            'expect': [{'tool': str(e.get('tool', '')),
                        **({'args_pattern': str(e['args_pattern'])}
                           if e.get('args_pattern') else {})}
                       for e in expect if isinstance(e, dict) and e.get('tool')],
        })
    return out


def parse_cached(path: str, content_hash: str) -> list[dict]:
    """按 content_hash 缓存的解析入口：文件缺失时仍可命中（定义被清理后
    历史拟合的重算依据）。均无 → FitParseError。"""
    if content_hash and content_hash in _parse_cache:
        _parse_cache.move_to_end(content_hash)
        return _parse_cache[content_hash]
    try:
        with open(path, encoding='utf-8') as f:
            steps = parse_fit_steps(f.read())
    except FileNotFoundError:
        raise FitParseError(f'定义文件不存在: {path}')
    if content_hash:
        _parse_cache[content_hash] = steps
        while len(_parse_cache) > _PARSE_CACHE_MAX:
            _parse_cache.popitem(last=False)
    return steps


def match_steps(steps: list[dict], trace: list[dict]) -> dict:
    """贪心顺序匹配：指针扫描轨迹，按步骤序寻找首个满足
    tool 相等 AND (无 args_pattern OR args_text ~ pattern) 的调用。
    返回 per_step/steps_total/steps_hit/score/status（spec §4 口径）。"""
    per_step, ptr, hits = [], 0, 0
    trace_empty = not trace
    for s in steps:
        expect = s.get('expect') or []
        if not expect:
            per_step.append({'id': s['id'], 'name': s['name'],
                             'status': 'skipped', 'evidence': []})
            continue
        hit = None
        while ptr < len(trace):
            t = trace[ptr]
            ptr += 1
            for e in expect:                      # OR 语义：任一命中即该步 hit
                if t['tool'] == e['tool'] and (
                        not e.get('args_pattern')
                        or re.search(e['args_pattern'], t['args'] or '')):
                    hit = {'tool': t['tool'], 'args': (t['args'] or '')[:160],
                           'occurredAt': t['occurredAt']}
                    break
            if hit:
                break
        if hit:
            hits += 1
            per_step.append({'id': s['id'], 'name': s['name'],
                             'status': 'hit', 'evidence': [hit]})
        else:
            per_step.append({'id': s['id'], 'name': s['name'],
                             'status': 'miss', 'evidence': []})
    total = sum(1 for p in per_step if p['status'] in ('hit', 'miss'))
    score = round(hits / total * 100) if total else 0
    if trace_empty:
        status = 'no_trace'
    elif hits == total:
        status = 'fit'
    elif hits == 0:
        status = 'diverged'
    else:
        status = 'partial'
    return {'per_step': per_step, 'steps_total': total, 'steps_hit': hits,
            'score': score, 'status': status}
```

（文件头 `import re` 与上述函数同文件；`compute_attempt_fit`/`compute_for_session` 在 Step 5 追加。）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_skill_fit.py -v`
Expected: 8 PASS

- [ ] **Step 5: 追加编排函数（I/O）+ 编排测试**

测试追加（文件尾）：

```python
def test_compute_attempt_fit_end_to_end(db_conn, monkeypatch):
    """编排：manifests 定义 + 账本轨迹 → 结果行落库（attempt + def_name 冲突覆盖）。"""
    import json as _json
    import uuid as _uuid
    from db import get_db
    from utils import skill_fit
    uid, sid, bid, attempt = (str(_uuid.uuid4()) for _ in range(4))
    skill_dir = os.path.join(str(db_conn.info), 'unused')  # 占位；真实路径用 tmp_path
    ws = _uuid.uuid4()
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (id, username, password_hash, display_name, role) "
                "VALUES (%s, %s, 'x', 'SF', 'developer')", (uid, f'sf_{uid[:8]}'))
            cur.execute(
                "INSERT INTO ai_chat_batches (id, user_id, name, prompt, total) "
                "VALUES (%s, %s, 'sf', 'p', 1)", (bid, uid))
            cur.execute(
                "INSERT INTO ai_chat_sessions (id, user_id, status, batch_id, "
                "  batch_seq, workspace_path, session_token) "
                "VALUES (%s, %s, 'completed', %s, 0, %s, %s)",
                (sid, uid, bid, f'C:\\sf\\{sid}', f'tok-{sid[:12]}'))
            cur.execute(
                "INSERT INTO ai_execution_attempts (id, session_id, source_type, "
                "  operation, started_at, finished_at) "
                "VALUES (%s, %s, 'batch', 'send', NOW() - interval '5 minutes', NOW())",
                (attempt, sid))
            cur.execute(
                "INSERT INTO ai_execution_manifests (id, attempt_id, kind, name, "
                "  source, path, content_hash, injected) "
                "VALUES (%s, %s, 'skill', 'demo-skill', 'session', %s, %s, true)",
                ('man_' + _uuid.uuid4().hex[:8], attempt,
                 str(tmp_skill_md := None) if False else 'placeholder', 'hash-demo'))
            cur.execute(
                "INSERT INTO agent_tool_calls (oc_session_id, root_session_id, "
                "  subtask_id, agent, part_id, tool, args_text, state) "
                "VALUES ('oc-sf', %s, NULL, NULL, 'p1', 'bash', 'git clone x', 'completed')",
                (sid,))
    db_conn.commit()
    # 定义文件：一个 expect 命中、一个 expect 缺失
    md_path = os.path.join(str(tmp_path), 'SKILL.md')
    md_path and None
```

（上面编排测试的种子写法有几处依赖 `tmp_path` fixture 的细节——**实现者按此语义落成完整可运行代码**：定义文件写到 `tmp_path` 的 `demo-skill/SKILL.md`，frontmatter 两步（bash `git clone` 命中 + glob miss），manifest 的 path 指向该文件、content_hash 用文件 sha256；断言：`compute_attempt_fit(attempt)` 返回 1 行、`status='partial'`、score=50、per_step[0].status='hit'；重复调用幂等（仍 1 行）；`compute_for_session(sid)` 返回同结果。）

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_skill_fit.py -v`
Expected: 全 PASS

- [ ] **Step 6: Commit**

```bash
git add server/utils/skill_fit.py server/tests/test_skill_fit.py
git commit -m "feat(skillopt): 拟合引擎——frontmatter 步骤解析/贪心匹配/attempt 编排"
```

---

### Task 3: 采集修复——subagent skill 调用归属

**Files:**
- Modify: `server/utils/skillopt.py`（`_platform_session_id`、`record_runtime_skill_event`、`upsert_invocation`）
- Create: `server/migrations/2026_10_01_skill_fit_tables.py`（Task 1 已含 subtask_id 列）
- Test: `server/tests/test_skillopt_collection.py`（新建）

**Interfaces:**
- Produces: `_platform_session_id(oc_id) -> str`（回退语义：ai_chat_sessions 无行时查 ai_chat_subtasks.id → 返回 root_session_id）；`upsert_invocation(..., subtask_id: str | None = None)`；`record_runtime_skill_event` 命中子代理时写入 subtask_id 并优先归属子代理自己的 attempt。

- [ ] **Step 1: 写失败测试**（新建 `tests/test_skillopt_collection.py`）

```python
# -*- coding: utf-8 -*-
"""subagent skill 调用采集修复（spec §5c）：OC 事件里 subagent part 的
sessionID 是子代理自己的 id，_platform_session_id 必须回退 ai_chat_subtasks
映射到根会话，并以 subtask_id 标注归属。"""
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def test_platform_session_id_falls_back_to_subtask(db_conn):
    import uuid as _uuid
    from db import get_db
    from utils.skillopt import _platform_session_id
    uid, bid = str(_uuid.uuid4()), str(_uuid.uuid4())
    root, sub_oc = str(_uuid.uuid4()), f'ses_sub{_uuid.uuid4().hex[:8]}'
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                        "VALUES (%s, %s, 'x', 'SC', 'developer')", (uid, f'sc_{uid[:8]}'))
            cur.execute("INSERT INTO ai_chat_batches (id, user_id, name, prompt, total) "
                        "VALUES (%s, %s, 'c', 'p', 1)", (bid, uid))
            cur.execute("INSERT INTO ai_chat_sessions (id, user_id, status, batch_id, "
                        "  batch_seq, opencode_session_id, session_token) "
                        "VALUES (%s, %s, 'running', %s, 0, 'oc_parent_x', %s)",
                        (root, uid, bid, f'tok-{root[:12]}'))
            cur.execute("INSERT INTO ai_chat_subtasks (id, root_session_id, agent, status) "
                        "VALUES (%s, %s, 'general', 'completed')", (sub_oc, root))
    db_conn.commit()
    try:
        assert _platform_session_id('oc_parent_x') == root     # 既有路径
        assert _platform_session_id(sub_oc) == root            # 修复：子代理回退
        assert _platform_session_id('ses_unknown') == ''       # 均无 → 空
    finally:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM ai_chat_subtasks WHERE id = %s", (sub_oc,))
                cur.execute("DELETE FROM ai_chat_sessions WHERE id = %s", (root,))
                cur.execute("DELETE FROM ai_chat_batches WHERE id = %s", (bid,))
                cur.execute("DELETE FROM users WHERE id = %s", (uid,))
        db_conn.commit()


def test_runtime_event_from_subagent_records_subtask(db_conn):
    """subagent 的 skill 上报：归属根会话 + subtask_id 标注 + outcome 收口。"""
    import uuid as _uuid
    from db import get_db
    from utils.skillopt import record_runtime_skill_event
    uid, bid = str(_uuid.uuid4()), str(_uuid.uuid4())
    root, sub_oc = str(_uuid.uuid4()), f'ses_sub{_uuid.uuid4().hex[:8]}'
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                        "VALUES (%s, %s, 'x', 'SC', 'developer')", (uid, f'sc_{uid[:8]}'))
            cur.execute("INSERT INTO ai_chat_batches (id, user_id, name, prompt, total) "
                        "VALUES (%s, %s, 'c', 'p', 1)", (bid, uid))
            cur.execute("INSERT INTO ai_chat_sessions (id, user_id, status, batch_id, "
                        "  batch_seq, opencode_session_id, session_token) "
                        "VALUES (%s, %s, 'running', %s, 0, %s, %s)",
                        (root, uid, bid, sub_oc, f'tok-{root[:12]}'))
            cur.execute("INSERT INTO ai_chat_subtasks (id, root_session_id, agent, status) "
                        "VALUES (%s, %s, 'general', 'completed')", (sub_oc, root))
    db_conn.commit()
    try:
        r = record_runtime_skill_event({
            'skillName': 'data-pull', 'sessionID': sub_oc,
            'messageID': 'msg_1', 'partID': 'prt_1', 'status': 'completed',
            'title': 'Loaded skill: data-pull'})
        assert r['ok'] is True and r.get('subtaskId') == sub_oc
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT session_id, subtask_id, evidence_level "
                            "FROM ai_skill_invocations WHERE skill_name='data-pull'")
                row = cur.fetchone()
        assert row and row[0] == root and row[1] == sub_oc and row[2] == 'confirmed'
    finally:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM ai_skill_invocations WHERE session_id = %s", (root,))
                cur.execute("DELETE FROM ai_chat_subtasks WHERE id = %s", (sub_oc,))
                cur.execute("DELETE FROM ai_chat_sessions WHERE id = %s", (root,))
                cur.execute("DELETE FROM ai_chat_batches WHERE id = %s", (bid,))
                cur.execute("DELETE FROM users WHERE id = %s", (uid,))
        db_conn.commit()
```

（`record_runtime_skill_event` 依赖 `execution_audit.get_attempts`——子代理无 attempt 时走 `attempt_id=None` 分支，测试已兼容。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_skillopt_collection.py -v`
Expected: FAIL（`_platform_session_id` 回退未实现 → 返回 ''）

- [ ] **Step 3: 实现**（`utils/skillopt.py`）

`_platform_session_id` 尾部（查 ai_chat_sessions 无行后）追加回退：

```python
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
```

`upsert_invocation` 签名加 `subtask_id: str | None = None`，INSERT 列清单与 VALUES 各加一列。

`record_runtime_skill_event` 改为（映射落空时回退子代理并标注）：

```python
    session_id = _platform_session_id(payload.get('sessionID') or '')
    subtask_id = None
    if not session_id:
        oc_id = payload.get('sessionID') or ''
        try:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT root_session_id FROM ai_chat_subtasks "
                                "WHERE id = %s", (oc_id,))
                    row = cur.fetchone()
            if row:
                session_id, subtask_id = row[0], oc_id
        except Exception as e:
            logger.warning('subtask lookup failed %s: %s', oc_id, e)
    if not session_id or not skill:
        return {'ok': False, 'reason': 'missing fields'}
```

（attempt 归属沿用既有 get_attempts 逻辑即可——子代理无独立 attempt 时落根 attempt。）
末尾 `upsert_invocation(..., subtask_id=subtask_id)`，返回 `{'ok': True, 'subtaskId': subtask_id}`。

- [ ] **Step 4: 跑测试确认通过 + 回归**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_skillopt_collection.py tests/test_skill_fit.py -q`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add server/utils/skillopt.py server/tests/test_skillopt_collection.py
git commit -m "fix(skillopt): subagent skill 调用采集——会话映射回退子代理表并标注归属"
```

---

### Task 4: API——fits 列表/明细/recompute/def-versions/label

**Files:**
- Modify: `server/routes/ai_session_admin.py`（`ai_execution_admin_bp` 内、相邻 skill-analytics 端点旁）
- Test: `server/tests/test_skill_fit_routes.py`（新建）

**Interfaces:**
- Consumes: Task 2 的 `skill_fit.compute_attempt_fit`；Task 1 的两张表。
- Produces:
  - `GET /ai/chat/admin/skill-fit?limit=50&sessionId=` → `{'fits': [{attemptId, sessionId, defKind, defName, defHash, stepsTotal, stepsHit, score, status, computedAt}]}`
  - `GET /ai/chat/admin/skill-fit/<attempt_id>` → `{'fits': [同上含 perStep/diagnosis]}`
  - `POST /ai/chat/admin/skill-fit/<attempt_id>/recompute` → `{'fits': [...]}`（幂等重算）
  - `GET /ai/chat/admin/skill-def-versions?defKind&defName` → `{'versions': [{id, defKind, defName, contentHash, versionLabel, note, firstSeenAt, tasks, avgScore, fitRate}]}`
  - `PATCH /ai/chat/admin/skill-def-versions/<id>` body `{versionLabel?, note?}` → `{'ok': true}`
- 权限：与相邻 skill-analytics 端点同装饰器（实现者先读 `routes/ai_session_admin.py:655-680` 的实际装饰器写法照抄）。

- [ ] **Step 1: 写失败测试**（新建 `tests/test_skill_fit_routes.py`；fixture 模式先读 `tests/test_ai_batch_admin_routes.py` 的 client/admin_headers 写法，端点前缀换成 `/ai/chat/admin`）

```python
# -*- coding: utf-8 -*-
# SkillOpt 拟合端点测试（admin 权限/camelCase 形状/recompute 幂等）。
# 注意：SKILL_MD 常量用三个双引号字符串字面量（与本文档示例同形）。
import hashlib
import json as _json
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

SKILL_MD = (
    '---\n'
    'description: 数据拉取\n'
    'fit:\n'
    '  steps:\n'
    "    - id: clone\n"
    '      name: 克隆仓库\n'
    '      expect:\n'
    '        - tool: bash\n'
    "          args_pattern: 'git clone'\n"
    '---\n'
    '正文。\n'
)


def _seed_fit_fixture(db_conn, tmp_path):
    """users + 批 + 会话 + attempt + manifest（path 指向带 fit 的定义文件）
    + 一条 fit 结果行。返回 (uid, bid, sid, attempt)。"""
    uid, bid, sid = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    attempt = str(uuid.uuid4())
    md = tmp_path / 'SKILL.md'
    md.write_text(SKILL_MD, encoding='utf-8')
    ws = tmp_path / 'ws'
    ws.mkdir()
    with db_conn.cursor() as cur:
        cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                    "VALUES (%s, %s, 'x', 'FR', 'developer')", (uid, f'fr_{uid[:8]}'))
        cur.execute("INSERT INTO ai_chat_batches (id, user_id, name, prompt, total) "
                    "VALUES (%s, %s, 'fr', 'p', 1)", (bid, uid))
        cur.execute("INSERT INTO ai_chat_sessions (id, user_id, status, batch_id, "
                    "  batch_seq, workspace_path, session_token) "
                    "VALUES (%s, %s, 'completed', %s, 0, %s, %s)",
                    (sid, uid, bid, str(ws), f'tok-{sid[:12]}'))
        cur.execute("INSERT INTO ai_execution_attempts (id, session_id, source_type, "
                    "  operation, started_at, finished_at) "
                    "VALUES (%s, %s, 'batch', 'send', NOW() - interval '1 minute', NOW())",
                    (attempt, sid))
        cur.execute("INSERT INTO ai_execution_manifests (id, attempt_id, kind, name, "
                    "  source, path, content_hash, injected) "
                    "VALUES (%s, %s, 'skill', 'demo-skill', 'session', %s, %s, true)",
                    ('man_' + uuid.uuid4().hex[:8], attempt, str(md),
                     hashlib.sha256(md.read_bytes()).hexdigest()))
        cur.execute("INSERT INTO ai_skill_fit_results (id, attempt_id, session_id, "
                    "  def_kind, def_name, def_hash, steps_total, steps_hit, "
                    "  score, status, per_step) "
                    "VALUES (%s, %s, %s, 'skill', 'demo-skill', %s, 1, 1, 100, "
                    "  'fit', %s::jsonb)",
                    ('fit_' + uuid.uuid4().hex[:10], attempt, sid,
                     hashlib.sha256(md.read_bytes()).hexdigest(),
                     json.dumps([{'id': 'clone', 'name': '克隆仓库',
                                  'status': 'hit', 'evidence': []}])))
    db_conn.commit()
    return uid, bid, sid, attempt


def test_skill_fit_requires_admin(client, dev_headers):
    r = client.get('/ai/chat/admin/skill-fit', headers=dev_headers)
    assert r.status_code in (401, 403)


def test_skill_fit_list_and_detail(client, admin_headers, db_conn, tmp_path):
    uid, bid, sid, attempt = _seed_fit_fixture(db_conn, tmp_path)
    try:
        r = client.get('/ai/chat/admin/skill-fit', headers=admin_headers)
        assert r.status_code == 200
        row = next(f for f in r.get_json()['fits'] if f['attemptId'] == attempt)
        assert row['defName'] == 'demo-skill' and row['score'] == 100
        assert row['status'] == 'fit'
        d = client.get(f'/ai/chat/admin/skill-fit/{attempt}', headers=admin_headers)
        assert d.status_code == 200
        assert d.get_json()['fits'][0]['perStep'][0]['id'] == 'clone'
        rc = client.post(f'/ai/chat/admin/skill-fit/{attempt}/recompute',
                         headers=admin_headers)
        assert rc.status_code == 200
        with db_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM ai_skill_fit_results WHERE attempt_id=%s",
                        (attempt,))
            assert cur.fetchone()[0] == 1        # recompute 幂等
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_skill_fit_results WHERE attempt_id=%s", (attempt,))
            cur.execute("DELETE FROM ai_execution_manifests WHERE attempt_id=%s", (attempt,))
            cur.execute("DELETE FROM ai_execution_attempts WHERE id=%s", (attempt,))
            cur.execute("DELETE FROM ai_chat_sessions WHERE batch_id=%s", (bid,))
            cur.execute("DELETE FROM ai_chat_batches WHERE id=%s", (bid,))
            cur.execute("DELETE FROM users WHERE id=%s", (uid,))
        db_conn.commit()


def test_skill_fit_detail_missing_404(client, admin_headers):
    r = client.get('/ai/chat/admin/skill-fit/no-such-attempt', headers=admin_headers)
    assert r.status_code == 404
```

（fixture 使用裸 `db_conn.cursor()` 而非 `get_db` 上下文——与 `tests/test_batch_routes.py` 的真库测试同款；种子清理顺序符合 FK 依赖。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_skill_fit_routes.py -v`
Expected: FAIL（404，端点不存在）

- [ ] **Step 3: 实现五个端点**（读相邻 skill-analytics 端点，同装饰器/同返回风格；查询一律带归属过滤；fits 列表 `ORDER BY computed_at DESC LIMIT %s`；recompute 调 `skill_fit.compute_attempt_fit`；def-versions 聚合 SQL 按 `(def_kind, def_name, content_hash)` 关联 fit_results 做 tasks/avgScore/fitRate）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_skill_fit_routes.py tests/test_ai_batch_admin_routes.py -q`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add server/routes/ai_session_admin.py server/tests/test_skill_fit_routes.py
git commit -m "feat(skillopt): 任务拟合 API——列表/明细/重算/版本聚合/label"
```

---

### Task 5: AI 步骤生成器 + 回写 + 偏差诊断（三个端点）

**Files:**
- Modify: `server/routes/ai_session_admin.py`（同 Task 4 位置）
- Create: `server/utils/skill_fit_ai.py`
- Test: `server/tests/test_skill_fit_ai.py`（新建）

**Interfaces:**
- Consumes: `utils.action_check_extractor` 的 AI 设置通道模式（`get_ai_settings`/`get_http_session`，失败 RuntimeError→502）；`utils.agent_ledger.validate_pg_regex`；Task 2 的 `match_steps`（试算）；Task 1 的表。
- Produces:
  - `skill_fit_ai.generate_steps(definition_text: str) -> list[dict]`：系统提示约束只输出 fit.steps JSON（含 expect 工具建议），低 temperature，解析失败/非法 → RuntimeError
  - `skill_fit_ai.apply_steps(path: str, steps: list[dict]) -> str`：解析原 frontmatter → 合并 fit（保留其余字段与正文）→ `validate_pg_regex` 校验每个 args_pattern → 写回；返回文件路径
  - `skill_fit_ai.diagnose(result_row: dict, definition_text: str, prompt: str, trace_tail: list[dict]) -> dict`：结构化诊断 `{cause, suggestions[], revised_steps}`；`cause` 枚举校验
  - 诊断缓存：`(result_id, def_hash, trace_sig)` 进进程内 dict；同签名直接返回缓存
- 端点（Task 4 同文件）：
  - `POST /ai/chat/admin/skill-def-steps/generate` body `{kind, path}` → `{'steps': [...]}`
  - `POST /ai/chat/admin/skill-def-steps/apply` body `{path, steps}` → `{'path': ...}`（apply 前对每个 args_pattern 跑 `validate_pg_regex`，非法 400）
  - `POST /ai/chat/admin/skill-fit/<result_id>/diagnose` → `{'diagnosis': {...}}`（result 行不存在 404；非 partial/diverged 400）

- [ ] **Step 1: 写失败测试**（新建 `tests/test_skill_fit_ai.py`，AI 通道打桩模式照抄 `tests/test_action_check_extractor.py` 的 `_patch_ai`）

```python
# -*- coding: utf-8 -*-
"""AI 步骤生成器 / 回写 / 偏差诊断单元（打桩 AI 通道）。"""
import json as _json
import os
import sys
import uuid
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _patch_ai(monkeypatch, content):
    """照抄 test_action_check_extractor.py 的打桩模式：AI 设置 + HTTP 会话。"""
    import utils.skill_fit_ai as fa
    monkeypatch.setattr(fa, 'get_ai_settings',
                        lambda: {'enabled': True, 'apiKey': 'k', 'endpoint': 'http://x',
                                 'model': 'm', 'timeout': 5, 'maxTokens': 1024})
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {'choices': [{'message': {'content': content}}]}
    monkeypatch.setattr(fa, 'get_http_session', lambda: MagicMock(post=lambda *a, **kw: resp))


GOOD_STEPS = _json.dumps({'steps': [
    {'id': 'clone', 'name': '克隆仓库',
     'expect': [{'tool': 'bash', 'args_pattern': 'git clone'}]},
    {'id': 'report', 'name': '写报告',
     'expect': [{'tool': 'write', 'args_pattern': 'outputs?/'}]},
]}, ensure_ascii=False)


def test_generate_steps_schema_constrained(monkeypatch):
    _patch_ai(monkeypatch, GOOD_STEPS)
    from utils.skill_fit_ai import generate_steps
    steps = generate_steps('# 数据拉取技能\n1. clone 2. report')
    assert [s['id'] for s in steps] == ['clone', 'report']
    assert steps[0]['expect'] == [{'tool': 'bash', 'args_pattern': 'git clone'}]


def test_generate_steps_invalid_output_raises(monkeypatch):
    _patch_ai(monkeypatch, '完全不是 JSON')
    from utils.skill_fit_ai import generate_steps
    with pytest.raises(RuntimeError):
        generate_steps('x')


def test_apply_steps_merges_frontmatter(tmp_path):
    from utils.skill_fit_ai import apply_steps
    f = tmp_path / 'SKILL.md'
    f.write_text('---\ndescription: 数据拉取\n其他: 保留\n---\n\n正文内容。\n',
                 encoding='utf-8')
    steps = [{'id': 'clone', 'name': '克隆仓库',
              'expect': [{'tool': 'bash', 'args_pattern': 'git clone'}]}]
    apply_steps(str(f), steps)
    out = f.read_text(encoding='utf-8')
    assert 'description: 数据拉取' in out and '其他: 保留' in out   # 原字段保留
    assert 'fit:' in out and 'git clone' in out
    assert out.endswith('正文内容。\n')                              # 正文保留


def test_apply_steps_rejects_bad_regex(tmp_path):
    from utils.skill_fit_ai import apply_steps
    f = tmp_path / 'SKILL.md'
    f.write_text('---\ndescription: x\n---\n正文\n', encoding='utf-8')
    with pytest.raises(ValueError):
        apply_steps(str(f), [{'id': 'a', 'name': 'a',
                              'expect': [{'tool': 'bash', 'args_pattern': '('}]}])
    assert 'fit:' not in f.read_text(encoding='utf-8')   # 未写文件


def test_diagnose_shape_and_cache(monkeypatch, db_conn, tmp_path):
    """种子 partial 结果行 → diagnose 返回结构化诊断并落 diagnosis 列；
    同轨迹签名二次调用不再打 LLM（缓存命中）。"""
    import uuid as _uuid
    from db import get_db
    import utils.skill_fit_ai as fa
    uid, sid, attempt, result = (str(_uuid.uuid4()) for _ in range(4))
    md = tmp_path / 'SKILL.md'
    md.write_text('---\nfit:\n  steps:\n    - id: a\n      name: A\n'
                  '      expect:\n        - tool: bash\n---\n正文\n', encoding='utf-8')
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                        "VALUES (%s, %s, 'x', 'DG', 'developer')", (uid, f'dg_{uid[:8]}'))
            cur.execute("INSERT INTO ai_chat_sessions (id, user_id, status, session_token) "
                        "VALUES (%s, %s, 'completed', %s)",
                        (sid, uid, f'tok-{sid[:12]}'))
            cur.execute("INSERT INTO ai_execution_attempts (id, session_id, source_type, "
                        "  operation, started_at, finished_at) "
                        "VALUES (%s, %s, 'batch', 'send', NOW(), NOW())", (attempt, sid))
            cur.execute("INSERT INTO ai_skill_fit_results (id, attempt_id, session_id, "
                        "  def_kind, def_name, def_hash, status, per_step) "
                        "VALUES (%s, %s, %s, 'skill', 'demo', 'h1', 'partial', %s::jsonb)",
                        (result, attempt, sid,
                         _json.dumps([{'id': 'a', 'name': 'A', 'status': 'miss',
                                       'evidence': []}])))
    db_conn.commit()
    diagnosis = {'cause': 'definition_stale', 'suggestions': ['更新步骤'],
                 'revised_steps': [{'id': 'a', 'name': 'A', 'expect': []}]}
    calls = []
    monkeypatch.setattr(fa, '_llm_json',
                        lambda system, user: calls.append(1) or diagnosis)
    try:
        from utils.skill_fit_ai import diagnose_result
        d1 = diagnose_result(result, get_db=get_db)
        assert d1['cause'] == 'definition_stale'
        d2 = diagnose_result(result, get_db=get_db)
        assert len(calls) == 1                      # 缓存命中，LLM 只调一次
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT diagnosis->>'cause' FROM ai_skill_fit_results "
                            "WHERE id=%s", (result,))
                assert cur.fetchone()[0] == 'definition_stale'
    finally:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM ai_skill_fit_results WHERE id=%s", (result,))
                cur.execute("DELETE FROM ai_execution_attempts WHERE id=%s", (attempt,))
                cur.execute("DELETE FROM ai_chat_sessions WHERE id=%s", (sid,))
                cur.execute("DELETE FROM users WHERE id=%s", (uid,))
        db_conn.commit()
```

（`_llm_json(system, user)` 是 `skill_fit_ai` 内部的「LLM 调用并解析 JSON」薄函数——generate/diagnose 共用；打桩点集中于此。diagnose 的输入材料（定义全文/per_step/轨迹尾部/prompt）由函数内部按 result_id 从库组装。）

- [ ] **Step 2: 跑测试确认失败** → **Step 3: 实现**（生成器系统提示要求只输出 `{"steps":[...]}`、低 temperature；`apply_steps` 用 yaml 重排 frontmatter 并保留正文；`diagnose` 的系统提示给出五类 cause 枚举与 JSON 输出契约）→ **Step 4: 全 PASS**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_skill_fit_ai.py -v`

- [ ] **Step 5: Commit**

```bash
git add server/utils/skill_fit_ai.py server/routes/ai_session_admin.py server/tests/test_skill_fit_ai.py
git commit -m "feat(skillopt): AI 步骤生成器/回写/偏差诊断——结构化建议与修订草案"
```

---

### Task 6: 收敛钩子接线（交互 + 批两条路径）

**Files:**
- Modify: `server/utils/chat_persist.py:704` 附近（`collect_skill_invocations` 调用旁）
- Modify: `server/utils/batch_engine.py`（三个 `_mark_*` 收口方法的 audit finish 之后）
- Test: `server/tests/test_skill_fit.py` 追加编排用例（打桩 compute_attempt_fit 断言被调）

**Interfaces:**
- Consumes: Task 2 的 `skill_fit.compute_for_session(session_id)`。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_skill_fit.py`）

```python
def test_compute_for_session_hook_invoked(db_conn, monkeypatch):
    """compute_for_session：会话无 attempt → None 且不抛；有 attempt → 委托
    compute_attempt_fit（打桩计数）。"""
    ...  # 种子 users/批/会话/attempt（同 Task 2 编排测试的种子段）
    called = []
    monkeypatch.setattr(skill_fit, 'compute_attempt_fit',
                        lambda a, get_db=None: called.append(a) or [])
    assert skill_fit.compute_for_session(sid, get_db=get_db) is None  # 无 attempt 的会话
    assert skill_fit.compute_for_session(attempt_sess, get_db=get_db) == []
    assert called == [attempt_sess]
```

- [ ] **Step 2: 跑测试确认失败** → **Step 3: 实现**

```python
def compute_for_session(session_id: str, get_db=None) -> list[dict] | None:
    """收敛钩子入口：按会话现查最新 attempt；无 attempt（非审计来源）→ None。
    任何异常只记日志（best-effort，不影响主流程）。"""
    try:
        db_ctx = get_db or _default_get_db
        with db_ctx() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM ai_execution_attempts WHERE session_id = %s "
                    "ORDER BY started_at DESC NULLS LAST LIMIT 1", (session_id,))
                row = cur.fetchone()
        if not row:
            return None
        return compute_attempt_fit(row[0], get_db=get_db)
    except Exception as e:
        log.warning('compute_for_session failed sid=%s: %s', session_id, e)
        return None
```

接线点：
- `chat_persist.py:704` 的 `_sk.collect_skill_invocations(...)` 之后追加：

```python
                    from utils import skill_fit as _sfit
                    _sfit.compute_for_session(sid)
```

- `batch_engine.py` 的 `_mark_done` / `_mark_failed` / `_mark_cancelled` 中 `execution_audit.finish_latest_running(...)` 调用之后各追加：

```python
        from utils import skill_fit as _sfit
        _sfit.compute_for_session(session_id)
```

- [ ] **Step 4: 跑测试确认通过 + 两条路径回归**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_skill_fit.py tests/test_batch_persist.py tests/test_batch_engine.py -q`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add server/utils/skill_fit.py server/utils/chat_persist.py server/utils/batch_engine.py server/tests/test_skill_fit.py
git commit -m "feat(skillopt): 收敛钩子接线——交互与批两条路径触发拟合计算"
```

---

### Task 7: 前端——任务拟合 tab（图标/明细/版本对比/生成器/诊断）

**Files:**
- Create: `src/components/admin/SkillFitBadge.vue`
- Modify: `src/views/admin/AiSkillOpt.vue`（新 tab）、`src/api/aiSkills.ts`（新 API 函数）
- Test: `src/components/admin/__tests__/SkillFitBadge.test.ts`（新建）

**Interfaces:**
- Consumes: Task 4/5 的端点（camelCase 字段：attemptId/defName/score/status/perStep/diagnosis）。
- Produces: `SkillFitBadge` props `{ stepsTotal, perStep: [{status}], score, status }`。

- [ ] **Step 1: 写失败测试**（SkillFitBadge 渲染断言：hit 点绿/miss 点红/skipped 点灰的类名、score 文本、status 徽标文案映射）

- [ ] **Step 2: 跑测试确认失败** → **Step 3: 实现组件与页面**

组件（要点）：`perStep` 渲染 N 个圆点 span（class 按状态），旁边 score 数字；status 徽标映射：fit=拟合(成功)/partial=部分拟合(警告)/diverged=偏离(危险)/no_trace=无轨迹(信息)/parse_error=解析失败(信息)。

页面（`AiSkillOpt.vue` 顶部加 ElTabs：既有内容收进「调用聚合」tab，新增「任务拟合」tab）：
- 列表：`GET /ai/chat/admin/skill-fit` → 行 = 时间/agent/来源/定义名/SkillFitBadge/「展开」；
- 展开行：步骤明细表（步骤名/状态图标/证据 args 截断）+「重新计算」按钮 + partial/diverged 时「分析偏差」按钮 → 诊断面板（cause 中文标签映射 + suggestions 列表 + revisedSteps 的 JSON 预览）；
- 版本对比区：`GET /ai/chat/admin/skill-def-versions` → 版本时间线表 + 相邻版本 Δ 卡（平均分变化数值上色：↑绿 ↓红）+ label 编辑（`PATCH`）；
- 生成器：定义行「生成步骤」→ 对话框（可编辑的步骤表格：id/name/tool/args_pattern）→「试算」（选历史 attempt 调明细端点展示拟合预览）→「保存」调 apply。

Run: `npx vitest run src/components/admin/__tests__/SkillFitBadge.test.ts && npx vue-tsc --noEmit`
Expected: PASS / 0 错误

- [ ] **Step 4: Commit**

```bash
git add src/components/admin/SkillFitBadge.vue src/components/admin/__tests__/SkillFitBadge.test.ts src/views/admin/AiSkillOpt.vue src/api/aiSkills.ts
git commit -m "feat(skillopt-ui): 任务拟合视图——分段图标/步骤明细/版本效果对比/步骤生成器/偏差诊断"
```

---

### Task 8: 全量回归 + 文档同步

**Files:**
- Modify: `CLAUDE.md`（SkillOpt 段补任务拟合一句）、`docs/design/ai/SkillOpt任务拟合设计.md`（状态改已实现）

- [ ] **Step 1:** CLAUDE.md SkillOpt 描述处补：任务拟合（attempt 收敛时按 frontmatter `fit.steps` 与工具轨迹贪心对照，`ai_skill_fit_results` 落库；管理页 SkillOpt「任务拟合」tab 图标呈现；AI 步骤生成器/偏差诊断/版本效果对比；subagent skill 调用确证归属修复）。
- [ ] **Step 2:** 设计文档头部状态改 `已实现（2026-10-01）`。
- [ ] **Step 3: 全量回归**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_skill_fit.py tests/test_skill_fit_routes.py tests/test_skill_fit_ai.py tests/test_skillopt_collection.py tests/test_batch_engine.py tests/test_batch_persist.py tests/test_batch_execution_safety_p0.py tests/test_agent_action_gate.py -q`
Expected: 全 PASS

Run: `npx vitest run src/components/admin/__tests__/SkillFitBadge.test.ts src/stores/__tests__/aiChat.permission.test.ts`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md docs/design/ai/SkillOpt任务拟合设计.md
git commit -m "docs: SkillOpt 任务拟合落地同步"
```
