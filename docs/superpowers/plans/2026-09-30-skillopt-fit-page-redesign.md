# SkillOpt 任务拟合页「定义主从布局」重设计 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 SkillOpt 管理页「任务拟合」tab 从三段竖向堆叠重写为「左栏定义列表 + 右侧单定义完整故事（概览卡 + 拟合结果/版本演进/偏离模式三个子标签）」的主从布局，后端补三个查询参数级 API。

**Architecture:** 父组件 `AiSkillOpt.vue` 只持有汇总数据与选中定义状态；五个新子组件 props/emits 通信。后端在既有 Flask 蓝图上加可选过滤参数并新增一个汇总端点（无表结构变更、无迁移）。

**Tech Stack:** Vue 3 `<script setup lang="ts">` + Element Plus（按需具名导入）+ Flask/PostgreSQL；测试 pytest（真实开发 PG）+ Playwright。

**Spec:** `docs/design/ai/SkillOpt任务拟合页面重设计.md`（实现前先读，本计划从它出发）

## Global Constraints

- Element Plus 组件一律按需具名导入（`import { ElTable } from 'element-plus'`），与现有代码一致；不新增 npm 依赖。
- 后端端点全部挂在 `ai_execution_admin_bp`，一律带 `@require_permission('admin.ai_chat_admin')`。
- pytest 走真实开发 PG：`cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_skill_fit_routes.py -v`；测试数据用后即删（finally 清理）。
- 测试用 def_name 一律带随机后缀（如 `demo-agent-<uuid6>`），避免与开发库既有数据/重跑撞名。
- 平台 Windows + Git Bash；提交信息中文、conventional 前缀（feat/fix/refactor/test/docs + 作用域）。
- spec 偏差记录（已裁定）：① 拟合结果表不设「操作」列——操作按钮统一放展开区右上角工具条（spec §5 两处表述取后者）；② Δ 内联徽标含拟合率变化（spec §6 明确要求，现状代码缺）。
- e2e 需要本地服务运行中（前端 5173 / 后端 8000；`npm run dev:all` 或 `bash start.sh`）。

## File Structure

```
server/routes/ai_session_admin.py            修改：/skill-fit 加参数；/skill-def-patterns 加参数；
                                             新增 /skill-fit/definition-summary（注册在 <attempt_id> 路由之前）
server/tests/test_skill_fit_routes.py        修改：追加 3 组路由测试
src/api/aiSkills.ts                          修改：+SkillFitDefinitionSummary/SkillDefPattern 类型；+3 函数；listSkillFits 加参
src/components/admin/skillopt/               新建目录：
  StepGeneratorDialog.vue                        生成器对话框（从 AiSkillOpt.vue 原样抽取）
  FitResultsPanel.vue                            拟合结果子标签（表格+筛选+展开两栏+诊断卡）
  DefVersionTimeline.vue                         版本演进子标签（时间线+Δ内联+标注编辑）
  DefPatternsPanel.vue                           偏离模式子标签（props 缺省=全局视图，供对话框复用）
  FitDefinitionList.vue                          左栏定义列表
  DefOverview.vue                                定义头+指标卡
src/views/admin/AiSkillOpt.vue               重写「任务拟合」tab 为主从布局；「调用聚合」tab 原样保留
e2e/ai-full/ai-skillopt-page.spec.ts         新建：页面冒烟
```

---

### Task 1: 后端 — `/skill-fit` 支持 defKind/defName 过滤

**Files:**
- Modify: `server/routes/ai_session_admin.py:869-895`（`list_skill_fits`）
- Test: `server/tests/test_skill_fit_routes.py`（文件尾部追加）

**Interfaces:**
- Produces: `GET /ai/chat/admin/skill-fit?sessionId=&defKind=&defName=&limit=` —— `defKind`/`defName` 为可选等值过滤，可与 `sessionId` 叠加。前端 Task 4 的 `listSkillFits` 依赖此签名。

- [ ] **Step 1: 写失败测试**（`server/tests/test_skill_fit_routes.py` 尾部追加）

```python
def test_skill_fit_list_filters_by_def(client, admin_headers, db_conn, tmp_path):
    """defKind/defName 过滤：同 attempt 两条不同定义，过滤后只回对应定义。"""
    uid, bid, sid, attempt = _seed_fit_fixture(db_conn, tmp_path)
    agent_name = f'demo-agent-{uuid.uuid4().hex[:6]}'
    try:
        with db_conn.cursor() as cur:
            cur.execute("INSERT INTO ai_skill_fit_results (id, attempt_id, session_id, "
                        "  def_kind, def_name, steps_total, steps_hit, score, status, per_step) "
                        "VALUES (%s, %s, %s, 'agent', %s, 1, 0, 0, 'diverged', %s::jsonb)",
                        ('fit_' + uuid.uuid4().hex[:10], attempt, sid, agent_name,
                         _json.dumps([])))
        db_conn.commit()
        r = client.get('/ai/chat/admin/skill-fit', headers=admin_headers,
                       query_string={'defName': agent_name})
        assert r.status_code == 200
        rows = [f for f in r.get_json()['fits'] if f['attemptId'] == attempt]
        assert rows and {f['defName'] for f in rows} == {agent_name}
        r2 = client.get('/ai/chat/admin/skill-fit', headers=admin_headers,
                        query_string={'defKind': 'skill'})
        mine = [f for f in r2.get_json()['fits'] if f['attemptId'] == attempt]
        assert mine and {f['defKind'] for f in mine} == {'skill'}
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_skill_fit_results WHERE attempt_id=%s", (attempt,))
            cur.execute("DELETE FROM ai_execution_manifests WHERE attempt_id=%s", (attempt,))
            cur.execute("DELETE FROM ai_execution_attempts WHERE id=%s", (attempt,))
            cur.execute("DELETE FROM ai_chat_sessions WHERE batch_id=%s", (bid,))
            cur.execute("DELETE FROM ai_chat_batches WHERE id=%s", (bid,))
            cur.execute("DELETE FROM users WHERE id=%s", (uid,))
        db_conn.commit()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_skill_fit_routes.py::test_skill_fit_list_filters_by_def -v`
Expected: FAIL —— 过滤参数被忽略，`rows` 里同时含 demo-skill 与 agent 定义（集合断言不等）。

- [ ] **Step 3: 实现**（`list_skill_fits`，改 WHERE 组装为条件列表）

```python
    session_id = (request.args.get('sessionId') or '').strip() or None
    def_kind = (request.args.get('defKind') or '').strip() or None
    def_name = (request.args.get('defName') or '').strip() or None
    sql = ("SELECT r.id, r.attempt_id, r.session_id, r.def_kind, r.def_name, r.def_hash, "
           "       r.steps_total, r.steps_hit, r.score, r.status, r.computed_at, "
           "       a.effective_agent "
           "FROM ai_skill_fit_results r "
           "LEFT JOIN ai_execution_attempts a ON a.id = r.attempt_id")
    conds: list = []
    params: list = []
    if session_id:
        conds.append("r.session_id = %s")
        params.append(session_id)
    if def_kind:
        conds.append("r.def_kind = %s")
        params.append(def_kind)
    if def_name:
        conds.append("r.def_name = %s")
        params.append(def_name)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY r.computed_at DESC LIMIT %s"
    params.append(limit)
```

docstring 同步改为：`"""任务拟合结果列表（computed_at 倒序；?sessionId/?defKind/?defName 过滤，limit 默认 50）。"""`

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_skill_fit_routes.py -v`
Expected: 全部 PASS（含既有用例——过滤不传时行为不变）。

- [ ] **Step 5: 提交**

```bash
git add server/routes/ai_session_admin.py server/tests/test_skill_fit_routes.py
git commit -m "feat(ai): skill-fit 列表支持 defKind/defName 过滤"
```

---

### Task 2: 后端 — `/skill-def-patterns` 支持 defName 过滤

**Files:**
- Modify: `server/routes/ai_session_admin.py:1065-1112`（`skill_def_patterns`）
- Test: `server/tests/test_skill_fit_routes.py`（追加）

**Interfaces:**
- Produces: `GET /ai/chat/admin/skill-def-patterns?defKind=&defName=&limit=`。Task 8 的 `DefPatternsPanel` 依赖。

- [ ] **Step 1: 写失败测试**（追加）

```python
def test_skill_def_patterns_filter_by_def_name(client, admin_headers, db_conn, tmp_path):
    """defName 过滤：只回该定义的步骤级偏离聚合。"""
    uid, bid, sid, attempt = _seed_fit_fixture(db_conn, tmp_path)
    agent_name = f'demo-agent-{uuid.uuid4().hex[:6]}'
    step_row = _json.dumps([{'id': 'clone', 'name': '克隆仓库',
                             'status': 'miss', 'evidence': []}])
    try:
        with db_conn.cursor() as cur:
            cur.execute("INSERT INTO ai_skill_fit_results (id, attempt_id, session_id, "
                        "  def_kind, def_name, steps_total, steps_hit, score, status, per_step) "
                        "VALUES (%s, %s, %s, 'agent', %s, 1, 0, 0, 'diverged', %s::jsonb)",
                        ('fit_' + uuid.uuid4().hex[:10], attempt, sid, agent_name, step_row))
        db_conn.commit()
        r = client.get('/ai/chat/admin/skill-def-patterns', headers=admin_headers,
                       query_string={'defName': agent_name})
        assert r.status_code == 200
        pats = r.get_json()['patterns']
        assert pats and all(p['defName'] == agent_name for p in pats)
        assert any(p['stepId'] == 'clone' for p in pats)
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_skill_fit_results WHERE attempt_id=%s", (attempt,))
            cur.execute("DELETE FROM ai_execution_manifests WHERE attempt_id=%s", (attempt,))
            cur.execute("DELETE FROM ai_execution_attempts WHERE id=%s", (attempt,))
            cur.execute("DELETE FROM ai_chat_sessions WHERE batch_id=%s", (bid,))
            cur.execute("DELETE FROM ai_chat_batches WHERE id=%s", (bid,))
            cur.execute("DELETE FROM users WHERE id=%s", (uid,))
        db_conn.commit()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_skill_fit_routes.py::test_skill_def_patterns_filter_by_def_name -v`
Expected: FAIL —— defName 未过滤，`pats` 里出现其他定义的行（`all(...)` 断言失败）。

- [ ] **Step 3: 实现**（`skill_def_patterns`，镜像现有 defKind 写法）

```python
    def_kind = (request.args.get('defKind') or '').strip() or None
    def_name = (request.args.get('defName') or '').strip() or None
```

SQL 的 WHERE 段追加一行、params 增加 4 个绑定：

```python
                WHERE f.status IN ('partial', 'diverged')
                  AND p->>'status' IN ('miss', 'out_of_order')
                  AND (%s IS NULL OR f.def_kind = %s)
                  AND (%s IS NULL OR f.def_name = %s)
```

```python
                """, (def_kind, def_kind, def_name, def_name, limit))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_skill_fit_routes.py -v`
Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add server/routes/ai_session_admin.py server/tests/test_skill_fit_routes.py
git commit -m "feat(ai): skill-def-patterns 支持 defName 过滤"
```

---

### Task 3: 后端 — 新增 `/skill-fit/definition-summary` 汇总端点

**Files:**
- Modify: `server/routes/ai_session_admin.py`（新端点，**必须注册在 `skill_fit_detail`（`/skill-fit/<attempt_id>`）之前**——虽然 Werkzeug 静态段优先于动态段，但显式放在前面消除歧义）
- Test: `server/tests/test_skill_fit_routes.py`（追加）

**Interfaces:**
- Produces: `GET /ai/chat/admin/skill-fit/definition-summary` →
  `{ definitions: [{ defKind, defName, tasks, fitRate, avgScore, partialCount, divergedCount, versions, latestLabel, latestHash, lastActivity }] }`
  按 `lastActivity` 倒序（无活动的排后）。Task 4/6/9 消费此形状。

- [ ] **Step 1: 写失败测试**（追加；`ai_skill_def_versions` 列：id 主键、first_seen_at 有 DEFAULT NOW()，只插 4 列即可）

```python
def test_skill_fit_definition_summary_union_and_metrics(client, admin_headers,
                                                        db_conn, tmp_path):
    """清单 = 版本表 ∪ 拟合结果并集（裸版本定义也出现）；指标与版本端点同口径。"""
    uid, bid, sid, attempt = _seed_fit_fixture(db_conn, tmp_path)
    agent_name = f'demo-agent-{uuid.uuid4().hex[:6]}'
    bare_name = f'bare-def-{uuid.uuid4().hex[:6]}'
    try:
        with db_conn.cursor() as cur:
            cur.execute("INSERT INTO ai_skill_fit_results (id, attempt_id, session_id, "
                        "  def_kind, def_name, steps_total, steps_hit, score, status, per_step) "
                        "VALUES (%s, %s, %s, 'agent', %s, 1, 0, 0, 'diverged', %s::jsonb)",
                        ('fit_' + uuid.uuid4().hex[:10], attempt, sid, agent_name,
                         _json.dumps([])))
            cur.execute("INSERT INTO ai_skill_def_versions (id, def_kind, def_name, content_hash) "
                        "VALUES (%s, 'agent', %s, 'h1'), (%s, 'agent', %s, 'h2')",
                        ('dv_' + uuid.uuid4().hex[:10], agent_name,
                         'dv_' + uuid.uuid4().hex[:10], bare_name))
        db_conn.commit()
        r = client.get('/ai/chat/admin/skill-fit/definition-summary', headers=admin_headers)
        assert r.status_code == 200
        defs = {f"{x['defKind']}/{x['defName']}": x
                for x in r.get_json()['definitions']}
        # 并集：从未跑任务的裸版本定义也在清单里
        bare = defs[f'agent/{bare_name}']
        assert bare['tasks'] == 0 and bare['fitRate'] is None
        # 指标口径
        d = defs[f'agent/{agent_name}']
        assert d['tasks'] == 1 and d['fitRate'] == 0.0 and d['divergedCount'] == 1
        assert d['versions'] == 1 and d['latestHash'] == 'h1'
        assert d['lastActivity'] is not None
        # 与版本端点口径一致（同一份数据 fitRate 相等）
        v = client.get('/ai/chat/admin/skill-def-versions', headers=admin_headers,
                       query_string={'defName': agent_name})
        vr = next(x for x in v.get_json()['versions'] if x['contentHash'] == 'h1')
        assert vr['fitRate'] == d['fitRate']
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_skill_fit_results WHERE attempt_id=%s", (attempt,))
            cur.execute("DELETE FROM ai_execution_manifests WHERE attempt_id=%s", (attempt,))
            cur.execute("DELETE FROM ai_execution_attempts WHERE id=%s", (attempt,))
            cur.execute("DELETE FROM ai_chat_sessions WHERE batch_id=%s", (bid,))
            cur.execute("DELETE FROM ai_chat_batches WHERE id=%s", (bid,))
            cur.execute("DELETE FROM users WHERE id=%s", (uid,))
            cur.execute("DELETE FROM ai_skill_def_versions WHERE def_kind='agent' "
                        "AND def_name IN (%s, %s)", (agent_name, bare_name))
        db_conn.commit()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_skill_fit_routes.py::test_skill_fit_definition_summary_union_and_metrics -v`
Expected: 404 —— 端点不存在（落到 `<attempt_id>` 路由也返回 404）。

- [ ] **Step 3: 实现端点**（插入位置：`skill_fit_detail` 定义之前）

```python
@ai_execution_admin_bp.get('/skill-fit/definition-summary')
@require_permission('admin.ai_chat_admin')
def skill_fit_definition_summary():
    """左栏定义清单 + 概览指标（页面重设计 spec §9.3）。

    清单 = ai_skill_def_versions ∪ ai_skill_fit_results 的 (def_kind,
    def_name) 并集——从未跑过任务的新定义也出现在左栏，否则「生成步骤」
    入口不可达（旧版本对比区可达，不能回归）。指标与 /skill-def-versions
    同口径：fitRate = fit 状态行占比（分母只计有拟合结果的任务）。"""
    from db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.def_kind, d.def_name,
                       COALESCE(t.tasks, 0)          AS tasks,
                       COALESCE(t.fit_tasks, 0)      AS fit_tasks,
                       COALESCE(t.partial_tasks, 0)  AS partial_tasks,
                       COALESCE(t.diverged_tasks, 0) AS diverged_tasks,
                       COALESCE(t.score_sum, 0)      AS score_sum,
                       d.versions                    AS versions,
                       lv.version_label              AS latest_label,
                       lv.content_hash               AS latest_hash,
                       t.last_activity               AS last_activity
                FROM (
                  SELECT u.def_kind, u.def_name, max(u.versions) AS versions
                  FROM (
                    SELECT def_kind, def_name,
                           count(DISTINCT content_hash) AS versions
                    FROM ai_skill_def_versions
                    GROUP BY def_kind, def_name
                    UNION ALL
                    SELECT def_kind, def_name, 0 AS versions
                    FROM ai_skill_fit_results
                    GROUP BY def_kind, def_name
                  ) u GROUP BY u.def_kind, u.def_name
                ) d
                LEFT JOIN (
                  SELECT def_kind, def_name,
                         count(*)                                     AS tasks,
                         count(*) FILTER (WHERE status = 'fit')       AS fit_tasks,
                         count(*) FILTER (WHERE status = 'partial')   AS partial_tasks,
                         count(*) FILTER (WHERE status = 'diverged')  AS diverged_tasks,
                         sum(score)                                   AS score_sum,
                         max(computed_at)                             AS last_activity
                  FROM ai_skill_fit_results
                  GROUP BY def_kind, def_name
                ) t ON t.def_kind = d.def_kind AND t.def_name = d.def_name
                LEFT JOIN LATERAL (
                  SELECT v.version_label, v.content_hash
                  FROM ai_skill_def_versions v
                  WHERE v.def_kind = d.def_kind AND v.def_name = d.def_name
                  ORDER BY v.first_seen_at DESC LIMIT 1
                ) lv ON true
                """)
            cols = [c[0] for c in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    defs = []
    for r in rows:
        tasks = r['tasks'] or 0
        defs.append({
            'defKind': r['def_kind'], 'defName': r['def_name'],
            'tasks': tasks,
            'fitRate': round(r['fit_tasks'] / tasks, 4) if tasks else None,
            'avgScore': round(r['score_sum'] / tasks, 1) if tasks else None,
            'partialCount': r['partial_tasks'],
            'divergedCount': r['diverged_tasks'],
            'versions': r['versions'] or 0,
            'latestLabel': r['latest_label'],
            'latestHash': r['latest_hash'],
            'lastActivity': r['last_activity'].isoformat() if r['last_activity'] else None,
        })
    # 最近活动倒序；无活动的（'' 排最后）按名称补充排序
    defs.sort(key=lambda x: (x['lastActivity'] or '', x['defName']), reverse=True)
    return jsonify({'definitions': defs})
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_skill_fit_routes.py -v`
Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add server/routes/ai_session_admin.py server/tests/test_skill_fit_routes.py
git commit -m "feat(ai): 新增 skill-fit definition-summary 汇总端点"
```

---

### Task 4: 前端 — `aiSkills.ts` API 层扩展

**Files:**
- Modify: `src/api/aiSkills.ts`（SkillOpt 段尾部追加；`listSkillFits` 加参）

**Interfaces:**
- Consumes: Task 1/2/3 的后端签名。
- Produces（Task 5-9 全部依赖，命名/类型以此为准）:
  - `interface SkillFitDefinitionSummary { defKind: string; defName: string; tasks: number; fitRate: number | null; avgScore: number | null; partialCount: number; divergedCount: number; versions: number; latestLabel: string | null; latestHash: string | null; lastActivity: string | null }`
  - `interface SkillDefPattern { defKind: string; defName: string; stepId: string; missTasks: number; versionsAffected: number; versionHashes: string[]; avgScore: number | null; lastSeenAt: string | null }`
  - `getSkillFitDefinitionSummary(): Promise<{ definitions: SkillFitDefinitionSummary[] }>`
  - `listSkillDefPatterns(params?: { defKind?: string; defName?: string; limit?: number }): Promise<{ patterns: SkillDefPattern[] }>`
  - `listSkillFits(params?: { sessionId?: string; defKind?: string; defName?: string; limit?: number })`

- [ ] **Step 1: 修改 `listSkillFits` 并追加类型与函数**

```ts
/** 左栏定义清单 + 概览指标（/skill-fit/definition-summary） */
export interface SkillFitDefinitionSummary {
  defKind: string
  defName: string
  tasks: number
  fitRate: number | null
  avgScore: number | null
  partialCount: number
  divergedCount: number
  versions: number
  latestLabel: string | null
  latestHash: string | null
  lastActivity: string | null
}

/** 步骤级偏离聚合（/skill-def-patterns） */
export interface SkillDefPattern {
  defKind: string
  defName: string
  stepId: string
  missTasks: number
  versionsAffected: number
  versionHashes: string[]
  avgScore: number | null
  lastSeenAt: string | null
}
```

`listSkillFits` 签名改为：

```ts
export function listSkillFits(params?: {
  sessionId?: string; defKind?: string; defName?: string; limit?: number
}) {
  return get<{ fits: SkillFitRow[] }>(`${ADMIN}/skill-fit`, params)
}
```

追加：

```ts
export function getSkillFitDefinitionSummary() {
  return get<{ definitions: SkillFitDefinitionSummary[] }>(
    `${ADMIN}/skill-fit/definition-summary`)
}

export function listSkillDefPatterns(params?: {
  defKind?: string; defName?: string; limit?: number
}) {
  return get<{ patterns: SkillDefPattern[] }>(`${ADMIN}/skill-def-patterns`, params)
}
```

- [ ] **Step 2: 类型检查**

Run: `npx vue-tsc`
Expected: 无错误。

- [ ] **Step 3: 提交**

```bash
git add src/api/aiSkills.ts
git commit -m "feat(ui): SkillOpt API 层新增定义汇总/偏离模式封装"
```

---

### Task 5: 前端 — 抽取 `StepGeneratorDialog.vue`

**Files:**
- Create: `src/components/admin/skillopt/StepGeneratorDialog.vue`
- Modify: `src/views/admin/AiSkillOpt.vue`（对话框模板与 gen 逻辑换成组件引用）

**Interfaces:**
- Consumes: `generateSkillDefSteps` / `applySkillDefSteps` / `previewSkillDefSteps` / `SkillFitBadge`（既有）。
- Produces: 组件 props `{ visible: boolean; defKind: string; defName: string }`，emit `update:visible`。父组件用法：

```html
<StepGeneratorDialog :visible="genVisible" :def-kind="genKind"
                     :def-name="genName" @update:visible="genVisible = $event" />
```

- [ ] **Step 1: 新建组件**（内容 = 现 `AiSkillOpt.vue` 步骤生成器对话框整体搬迁；`visible/kind/name` 从 gen ref 里拿出来改为 props；`FitStep/FitPreview` 类型与格式化 helpers（trunc/dotClass/stepStatusText）随迁）

```vue
<template>
  <ElDialog :model-value="visible" :title="`生成步骤 — ${defName || '定义'}`"
            width="780px" append-to-body
            @update:model-value="$emit('update:visible', $event)">
    <!-- 原 gen 对话框 body 原样搬入；gen.value.kind/name 的引用改为 props.defKind/props.defName -->
  </ElDialog>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElDialog, ElTable, ElTableColumn, ElInput, ElButton, ElMessage } from 'element-plus'
import SkillFitBadge from '@/components/admin/SkillFitBadge.vue'
import {
  generateSkillDefSteps, applySkillDefSteps, previewSkillDefSteps,
} from '@/api/aiSkills'
import type { FitStep, FitPreview } from '@/api/aiSkills'

const props = defineProps<{ visible: boolean; defKind: string; defName: string }>()
defineEmits<{ (e: 'update:visible', v: boolean): void }>()

interface GenStepRow {
  id: string
  name: string
  tool: string
  args_pattern: string
  /** 生成结果里首个 expect 之外的项，编辑时保留不丢 */
  extraExpect: FitStep['expect']
}
const gen = ref({
  path: '', steps: [] as GenStepRow[], attemptId: '',
  preview: null as FitPreview | null,
  busy: false, previewing: false, saving: false, error: '',
})
// runGenerate/runPreview/saveSteps/addStep/removeStep/toGenRow/fromGenRow
// 原样搬入；runGenerate 的 kind 参数改为 props.defKind
</script>
```

样式 `.gen__*` 与 `fit-dot` 相关类随迁（组件内保留自己用到的那部分）。

- [ ] **Step 2: 父组件接线**（此时其余逻辑不动）：删除父组件中 gen ref 的 `visible/kind/name` 三字段与对话框模板，改为上面的组件用法；`openGenerator(v)` 改为：

```ts
const genVisible = ref(false)
const genKind = ref('')
const genName = ref('')
function openGenerator(v: SkillDefVersion) {
  genKind.value = v.defKind
  genName.value = v.defName
  genVisible.value = true
}
```

- [ ] **Step 3: 类型检查**

Run: `npx vue-tsc`
Expected: 无错误。

- [ ] **Step 4: 提交**

```bash
git add src/components/admin/skillopt/StepGeneratorDialog.vue src/views/admin/AiSkillOpt.vue
git commit -m "refactor(ui): 抽取 SkillOpt 步骤生成器对话框组件"
```

---

### Task 6: 前端 — `FitResultsPanel.vue`（拟合结果子标签）

**Files:**
- Create: `src/components/admin/skillopt/FitResultsPanel.vue`

**Interfaces:**
- Consumes: `listSkillFits({ defKind, defName, limit: 100 })`（Task 1/4）、`getSkillFitDetail`/`recomputeSkillFit`/`diagnoseSkillFit`（既有）、`SkillFitBadge`。
- Produces: props `{ defKind: string; defName: string }`，emit `recomputed`（重算成功后父刷新汇总）。状态筛选规则：`fit`/`partial`/`diverged` 等值；`other` = `['failed', 'inconclusive', 'no_trace', 'parse_error']` 之外不匹配的兜底集。展开行两栏网格：左列步骤明细+子代理拟合，右列诊断卡；操作按钮在展开区右上工具条（Global Constraints 偏差①）。

- [ ] **Step 1: 新建组件**（逻辑从现 AiSkillOpt.vue 的 fits 段搬迁：`detailMap`/`diagMap`/`recomputing`/`diagnosing`/`fitRowKey`/`detailOf`/`detailSteps`/`detailLoaded`/`dotsOf`/`diagnosisOf`/`subagentFitsOf`/`ensureFitDetail`/`onFitExpand`/`recomputeFit`/`diagnose`/`copyRevised` 全部内化；数据加载改为按 props 过滤）

```vue
<template>
  <div class="fit-results">
    <div class="fit-results__bar">
      <ElRadioGroup v-model="statusFilter" size="small">
        <ElRadioButton value="">全部</ElRadioButton>
        <ElRadioButton value="fit">拟合</ElRadioButton>
        <ElRadioButton value="partial">部分</ElRadioButton>
        <ElRadioButton value="diverged">偏离</ElRadioButton>
        <ElRadioButton value="other">其他</ElRadioButton>
      </ElRadioGroup>
    </div>
    <p v-if="!filteredFits.length" class="muted">该定义暂无拟合结果。</p>
    <ElTable v-else :data="filteredFits" size="small" :row-key="fitRowKey"
             @expand-change="onFitExpand">
      <ElTableColumn type="expand">
        <template #default="{ row }">
          <div v-if="!detailLoaded(row)" class="muted">加载明细中…</div>
          <div v-else class="fit-detail">
            <div class="fit-detail__ops">
              <ElButton size="small" :loading="recomputing === fitRowKey(row)"
                        @click="recomputeFit(row)">重新计算</ElButton>
              <ElButton v-if="row.status === 'partial' || row.status === 'diverged'"
                        size="small" type="warning"
                        :loading="diagnosing === fitRowKey(row)"
                        @click="diagnose(row)">分析偏差</ElButton>
            </div>
            <div class="fit-detail__grid">
              <div><!-- 左列：步骤明细 + 子代理拟合（现有两表原样搬入） --></div>
              <div><!-- 右列：诊断卡（原因徽标/建议/修订草案 ElCollapse 折叠 + 复制） --></div>
            </div>
          </div>
        </template>
      </ElTableColumn>
      <ElTableColumn label="时间" width="165">
        <template #default="{ row }">{{ fmtTime(row.computedAt) }}</template>
      </ElTableColumn>
      <ElTableColumn label="会话" width="120">
        <template #default="{ row }">
          <span class="mono" :title="row.sessionId">{{ shortHash(row.sessionId) }}</span>
        </template>
      </ElTableColumn>
      <ElTableColumn label="Agent" width="140">
        <template #default="{ row }">
          <span v-if="row.agent" class="mono" :title="row.agent">{{ trunc(row.agent, 16) }}</span>
          <span v-else class="muted">—</span>
        </template>
      </ElTableColumn>
      <ElTableColumn label="拟合" min-width="220">
        <template #default="{ row }">
          <SkillFitBadge :steps-total="row.stepsTotal" :per-step="dotsOf(row)"
                         :score="row.score" :status="row.status" />
        </template>
      </ElTableColumn>
    </ElTable>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import {
  ElTable, ElTableColumn, ElButton, ElRadioGroup, ElRadioButton,
  ElTag, ElCollapse, ElCollapseItem, ElMessage,
} from 'element-plus'
import SkillFitBadge from '@/components/admin/SkillFitBadge.vue'
import {
  listSkillFits, getSkillFitDetail, recomputeSkillFit, diagnoseSkillFit,
} from '@/api/aiSkills'
import type {
  SkillFitRow, SkillFitDetail, SkillFitDiagnosis, FitPerStep,
} from '@/api/aiSkills'

const props = defineProps<{ defKind: string; defName: string }>()
const emit = defineEmits<{ (e: 'recomputed'): void }>()

const fits = ref<SkillFitRow[]>([])
const statusFilter = ref('')
const OTHER_STATUSES = ['failed', 'inconclusive', 'no_trace', 'parse_error']
const filteredFits = computed(() => {
  if (!statusFilter.value) return fits.value
  if (statusFilter.value === 'other')
    return fits.value.filter(f => OTHER_STATUSES.includes(f.status))
  return fits.value.filter(f => f.status === statusFilter.value)
})

async function load() {
  const res = await listSkillFits({
    defKind: props.defKind, defName: props.defName, limit: 100,
  })
  fits.value = res.fits || []
}
watch([() => props.defKind, () => props.defName], () => {
  statusFilter.value = ''
  detailMap.value = {}
  diagMap.value = {}
  void load()
}, { immediate: true })

// …搬迁的 detailMap/diagMap/重算/诊断逻辑；recomputeFit 成功尾部追加：
//   emit('recomputed')
</script>
```

样式：`.fit-detail__grid { display: grid; grid-template-columns: 3fr 2fr; gap: 16px; }`；诊断卡样式 `.fit-diag*` 从父组件迁入；修订草案 JSON 用 `ElCollapse > ElCollapseItem title="修订步骤草案"` 包 `pre.fit-diag__json` + 复制按钮。

- [ ] **Step 2: 类型检查**

Run: `npx vue-tsc`
Expected: 无错误。（组件此时尚未被父引用，属预期——Task 9 接线。）

- [ ] **Step 3: 提交**

```bash
git add src/components/admin/skillopt/FitResultsPanel.vue
git commit -m "feat(ui): FitResultsPanel 拟合结果子标签组件"
```

---

### Task 7: 前端 — `DefVersionTimeline.vue`（版本演进子标签）

**Files:**
- Create: `src/components/admin/skillopt/DefVersionTimeline.vue`

**Interfaces:**
- Consumes: `listSkillDefVersions({ defKind, defName })`（既有，返回旧→新）、`updateSkillDefVersion`（既有）。
- Produces: props `{ defKind: string; defName: string }`，emit `generate(v: SkillDefVersion)`。Δ 计算 = 相邻版本差值（沿用现 `versionGroups` 逻辑：`deltaScore` 一位小数、`deltaFitRate` 原值、`deltaDiverged` 整数）；展示新版本在上，Δ 显示在较新版本的行内。

- [ ] **Step 1: 新建组件**

```vue
<template>
  <div class="vt">
    <p v-if="!displayVersions.length" class="muted">该定义暂无版本记录。</p>
    <ElTimeline v-else>
      <ElTimelineItem v-for="v in displayVersions" :key="v.id"
                      :timestamp="fmtTime(v.firstSeenAt)" placement="top">
        <div class="vt__head">
          <ElInput v-model="labelDrafts[v.id]" size="small" class="vt__label"
                   placeholder="版本标注，回车保存" @change="saveLabel(v)" />
          <span class="mono" :title="v.contentHash ?? ''">{{ shortHash(v.contentHash) }}</span>
          <ElButton link size="small" type="primary" @click="emit('generate', v)">
            生成步骤
          </ElButton>
        </div>
        <div class="vt__metrics">
          任务 {{ v.tasks }} · 均分 {{ v.avgScore ?? '—' }} · 拟合率 {{ fmtRate(v.fitRate) }}
          <ElTag v-if="v.divergedCount" size="small" type="danger">偏离 {{ v.divergedCount }}</ElTag>
          <ElTag v-if="v.partialCount" size="small" type="warning">部分 {{ v.partialCount }}</ElTag>
          <span v-if="v.tasks > 0 && !v.divergedCount && !v.partialCount" class="muted">无偏离</span>
        </div>
        <div v-if="hasDelta(v)" class="vt__deltas">
          <span>较上一版：</span>
          <span v-if="v.deltaScore != null" :class="deltaNumClass(v.deltaScore)">
            均分 {{ fmtDeltaNum(v.deltaScore) }}
          </span>
          <span v-if="v.deltaFitRate" :class="deltaClass(v.deltaFitRate)">
            拟合率 {{ fmtDelta(v.deltaFitRate) }}
          </span>
          <span v-if="v.deltaDiverged != null && v.deltaDiverged !== 0"
                :class="divergeDeltaClass(v.deltaDiverged)">
            偏离 {{ v.deltaDiverged > 0 ? '+' : '' }}{{ v.deltaDiverged }}
          </span>
        </div>
      </ElTimelineItem>
    </ElTimeline>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import {
  ElTimeline, ElTimelineItem, ElInput, ElButton, ElTag, ElMessage,
} from 'element-plus'
import { listSkillDefVersions, updateSkillDefVersion } from '@/api/aiSkills'
import type { SkillDefVersion } from '@/api/aiSkills'

const props = defineProps<{ defKind: string; defName: string }>()
const emit = defineEmits<{ (e: 'generate', v: SkillDefVersion): void }>()

interface VersionView extends SkillDefVersion {
  deltaScore: number | null
  deltaFitRate: number | null
  deltaDiverged: number | null
}
const versions = ref<VersionView[]>([])
const labelDrafts = ref<Record<string, string>>({})

const displayVersions = computed(() => [...versions.value].reverse())

async function load() {
  const res = await listSkillDefVersions({
    defKind: props.defKind, defName: props.defName,
  })
  const list: VersionView[] = (res.versions || []).map(v => ({
    ...v, deltaScore: null, deltaFitRate: null, deltaDiverged: null,
  }))
  // 后端 ORDER BY first_seen_at：组内旧 → 新；Δ = 相邻版本差值
  for (let i = 1; i < list.length; i++) {
    const prev = list[i - 1]
    const cur = list[i]
    cur.deltaScore = prev.avgScore != null && cur.avgScore != null
      ? Math.round((cur.avgScore - prev.avgScore) * 10) / 10 : null
    cur.deltaFitRate = prev.fitRate != null && cur.fitRate != null
      ? cur.fitRate - prev.fitRate : null
    cur.deltaDiverged = cur.divergedCount - prev.divergedCount
  }
  versions.value = list
  for (const v of list) labelDrafts.value[v.id] = v.versionLabel ?? ''
}
watch([() => props.defKind, () => props.defName], () => void load(), { immediate: true })

function hasDelta(v: VersionView) {
  return v.deltaScore != null || (v.deltaFitRate != null && v.deltaFitRate !== 0)
    || (v.deltaDiverged != null && v.deltaDiverged !== 0)
}

async function saveLabel(v: SkillDefVersion) {
  const draft = (labelDrafts.value[v.id] ?? '').trim()
  if (draft === (v.versionLabel ?? '')) return
  try {
    await updateSkillDefVersion(v.id, { versionLabel: draft || null })
    ElMessage.success('版本标注已保存')
    await load()
  } catch { /* 全局 toast 已提示 */ }
}

// fmtTime/shortHash/fmtRate/fmtDelta/fmtDeltaNum/deltaClass/deltaNumClass/divergeDeltaClass
// 从现父组件就近迁入（divergeDeltaClass：偏离变多=红(delta-down)、变少=绿(delta-up)）
</script>
```

样式：`.vt__head { display: flex; align-items: center; gap: 8px; }`、`.vt__label { width: 180px; }`、`.vt__metrics/.vt__deltas { font-size: 12.5px; margin-top: 4px; display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }`、`.delta-up { color: var(--el-color-success); font-weight: 600; }`、`.delta-down { color: var(--el-color-danger); font-weight: 600; }`。

- [ ] **Step 2: 类型检查**

Run: `npx vue-tsc`
Expected: 无错误。

- [ ] **Step 3: 提交**

```bash
git add src/components/admin/skillopt/DefVersionTimeline.vue
git commit -m "feat(ui): DefVersionTimeline 版本演进时间线组件"
```

---

### Task 8: 前端 — `DefPatternsPanel.vue`（偏离模式，兼容全局视图）

**Files:**
- Create: `src/components/admin/skillopt/DefPatternsPanel.vue`

**Interfaces:**
- Consumes: `listSkillDefPatterns(params?)`（Task 2/4）。props 均可选：**两个都缺省 = 全局视图**（父组件的全局偏离对话框直接复用本组件，无 props 调用）；给了就按定义过滤。
- Produces: props `{ defKind?: string; defName?: string }`。

- [ ] **Step 1: 新建组件**

```vue
<template>
  <div class="fit-patterns">
    <p v-if="!patterns.length" class="muted">
      {{ isGlobal ? '暂无偏离模式数据。' : '该定义无步骤级偏离记录。' }}
    </p>
    <ElTable v-else :data="patterns" size="small">
      <ElTableColumn v-if="isGlobal" prop="defName" label="定义" min-width="150" />
      <ElTableColumn prop="stepId" label="偏差步骤" width="140" />
      <ElTableColumn prop="missTasks" label="偏离任务数" width="110" sortable />
      <ElTableColumn prop="versionsAffected" label="涉及版本" width="90" />
      <ElTableColumn label="版本 hash" min-width="160">
        <template #default="{ row }">
          <span class="mono">{{ (row.versionHashes || []).join(' ') }}</span>
        </template>
      </ElTableColumn>
      <ElTableColumn prop="avgScore" label="均分" width="70" />
    </ElTable>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { ElTable, ElTableColumn } from 'element-plus'
import { listSkillDefPatterns } from '@/api/aiSkills'
import type { SkillDefPattern } from '@/api/aiSkills'

const props = defineProps<{ defKind?: string; defName?: string }>()
const isGlobal = computed(() => !props.defName)

const patterns = ref<SkillDefPattern[]>([])
async function load() {
  const res = await listSkillDefPatterns({
    defKind: props.defKind, defName: props.defName, limit: 50,
  })
  patterns.value = res.patterns || []
}
watch([() => props.defKind, () => props.defName], () => void load(), { immediate: true })
</script>
```

- [ ] **Step 2: 类型检查**

Run: `npx vue-tsc`
Expected: 无错误。

- [ ] **Step 3: 提交**

```bash
git add src/components/admin/skillopt/DefPatternsPanel.vue
git commit -m "feat(ui): DefPatternsPanel 偏离模式组件（兼容全局视图）"
```

---

### Task 9: 前端 — 左栏/概览组件 + 父容器主从布局重写

**Files:**
- Create: `src/components/admin/skillopt/FitDefinitionList.vue`
- Create: `src/components/admin/skillopt/DefOverview.vue`
- Modify: `src/views/admin/AiSkillOpt.vue`（「任务拟合」tab 重写；「调用聚合」tab 原样保留）

**Interfaces:**
- Consumes: Task 4 全部签名；Task 5-8 组件。
- Produces: 页面级交互形态——选中定义是唯一主状态；`FitResultsPanel @recomputed` → 父刷新 `getSkillFitDefinitionSummary()`。

- [ ] **Step 1: 新建 `FitDefinitionList.vue`**

```vue
<template>
  <div class="fit-defs">
    <div v-for="d in definitions" :key="d.defKind + '/' + d.defName"
         class="fit-def" :class="{ 'is-active': isActive(d) }"
         @click="emit('select', d)">
      <div class="fit-def__head">
        <ElTag size="small" :type="d.defKind === 'skill' ? 'success' : 'primary'">
          {{ kindText(d.defKind) }}
        </ElTag>
        <span class="fit-def__name" :title="d.defName">{{ d.defName }}</span>
      </div>
      <div class="fit-def__meta">
        <span>拟合率 {{ fmtRate(d.fitRate) }}</span>
        <span>{{ d.versions }} 版</span>
        <ElTag v-if="d.divergedCount + d.partialCount" size="small" type="danger">
          偏离 {{ d.divergedCount + d.partialCount }}
        </ElTag>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ElTag } from 'element-plus'
import type { SkillFitDefinitionSummary } from '@/api/aiSkills'

const props = defineProps<{
  definitions: SkillFitDefinitionSummary[]
  selected: SkillFitDefinitionSummary | null
}>()
const emit = defineEmits<{ (e: 'select', d: SkillFitDefinitionSummary): void }>()

function isActive(d: SkillFitDefinitionSummary) {
  return !!props.selected && props.selected.defKind === d.defKind
    && props.selected.defName === d.defName
}
function kindText(kind: string) {
  if (kind === 'skill') return '技能'
  if (kind === 'agent') return '代理'
  return kind
}
function fmtRate(v: number | null | undefined) {
  return v == null ? '—' : Math.round(v * 100) + '%'
}
</script>

<style scoped>
.fit-defs { display: flex; flex-direction: column; gap: 4px; }
.fit-def {
  padding: 8px 10px; border-radius: 6px; cursor: pointer;
  border: 1px solid transparent;
}
.fit-def:hover { background: var(--el-fill-color-light); }
.fit-def.is-active {
  background: var(--el-color-primary-light-9);
  border-color: var(--el-color-primary-light-7);
}
.fit-def__head { display: flex; align-items: center; gap: 6px; }
.fit-def__name {
  font-size: 13px; font-weight: 600;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.fit-def__meta {
  display: flex; align-items: center; gap: 8px; margin-top: 4px;
  font-size: 12px; color: var(--el-text-color-secondary);
}
</style>
```

- [ ] **Step 2: 新建 `DefOverview.vue`**

```vue
<template>
  <div class="def-ov">
    <div class="def-ov__head">
      <strong class="def-ov__name">{{ def.defName }}</strong>
      <ElTag size="small" :type="def.defKind === 'skill' ? 'success' : 'primary'">
        {{ kindText(def.defKind) }}
      </ElTag>
      <span class="muted">
        最新版本 {{ def.latestLabel || shortHash(def.latestHash) }}
      </span>
    </div>
    <div class="def-ov__cards">
      <div class="def-ov__card"><b>{{ def.tasks }}</b><span>任务</span></div>
      <div class="def-ov__card"><b>{{ fmtRate(def.fitRate) }}</b><span>拟合率</span></div>
      <div class="def-ov__card">
        <b :class="def.divergedCount + def.partialCount ? 'is-bad' : ''">
          {{ def.divergedCount + def.partialCount }}
        </b><span>偏离</span>
      </div>
      <div class="def-ov__card"><b>{{ def.versions }}</b><span>版本</span></div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ElTag } from 'element-plus'
import type { SkillFitDefinitionSummary } from '@/api/aiSkills'

const props = defineProps<{ def: SkillFitDefinitionSummary }>()

function kindText(kind: string) {
  if (kind === 'skill') return '技能'
  if (kind === 'agent') return '代理'
  return kind
}
function fmtRate(v: number | null | undefined) {
  return v == null ? '—' : Math.round(v * 100) + '%'
}
function shortHash(h?: string | null) {
  return h ? h.slice(0, 10) + '…' : '—'
}
</script>

<style scoped>
.def-ov__head { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.def-ov__name { font-size: 15px; }
.def-ov__cards { display: flex; gap: 10px; margin-bottom: 4px; }
.def-ov__card {
  flex: 1; padding: 10px 12px; border-radius: 6px;
  background: var(--el-fill-color-light);
  display: flex; flex-direction: column; gap: 2px;
}
.def-ov__card b { font-size: 18px; font-variant-numeric: tabular-nums; }
.def-ov__card b.is-bad { color: var(--el-color-danger); }
.def-ov__card span { font-size: 12px; color: var(--el-text-color-secondary); }
</style>
```

- [ ] **Step 3: 重写 `AiSkillOpt.vue` 的「任务拟合」tab**

模板替换（原三段 `<h4>` 区块 + 表格全部删除）：

```html
<ElTabPane label="任务拟合" name="fit">
  <div v-loading="fitLoading" class="skillopt__pane">
    <ElAlert v-if="fitError" type="error" :closable="false" :title="fitError" />
    <div v-else-if="!definitions.length" class="fit-empty">
      <p>暂无拟合数据——任务执行后自动计算拟合结果。</p>
      <p class="muted">可在技能/代理定义 frontmatter 配置 fit.steps 声明步骤契约。</p>
    </div>
    <div v-else class="fit-layout">
      <aside class="fit-layout__side">
        <FitDefinitionList :definitions="definitions" :selected="selected"
                           @select="selected = $event" />
        <ElButton link size="small" class="fit-layout__global"
                  @click="globalPatternsVisible = true">
          全局偏离 Top
        </ElButton>
      </aside>
      <section class="fit-layout__main">
        <DefOverview v-if="selected" :def="selected" />
        <ElTabs v-model="detailTab">
          <ElTabPane label="拟合结果" name="results">
            <FitResultsPanel v-if="selected" :def-kind="selected.defKind"
                             :def-name="selected.defName"
                             @recomputed="loadDefinitions" />
          </ElTabPane>
          <ElTabPane label="版本演进" name="versions">
            <DefVersionTimeline v-if="selected" :def-kind="selected.defKind"
                                :def-name="selected.defName"
                                @generate="openGeneratorFromVersion" />
          </ElTabPane>
          <ElTabPane label="偏离模式" name="patterns">
            <DefPatternsPanel v-if="selected" :def-kind="selected.defKind"
                              :def-name="selected.defName" />
          </ElTabPane>
        </ElTabs>
      </section>
    </div>
  </div>
</ElTabPane>
```

页面级对话框（与两个 tab 平级放置）：

```html
<ElDialog v-model="globalPatternsVisible" title="全局偏离模式（跨定义 Top）"
          width="860px" append-to-body>
  <DefPatternsPanel v-if="globalPatternsVisible" />
</ElDialog>
<StepGeneratorDialog :visible="genVisible" :def-kind="genKind"
                     :def-name="genName" @update:visible="genVisible = $event" />
```

script 段改动：

```ts
// ── 任务拟合：主从状态 ──────────────────────────────────────────────
const fitLoading = ref(false)
const fitError = ref('')
const definitions = ref<SkillFitDefinitionSummary[]>([])
const selected = ref<SkillFitDefinitionSummary | null>(null)
const detailTab = ref('results')
const globalPatternsVisible = ref(false)
const genVisible = ref(false)
const genKind = ref('')
const genName = ref('')

async function loadDefinitions() {
  fitLoading.value = true
  fitError.value = ''
  try {
    const res = await getSkillFitDefinitionSummary()
    definitions.value = res.definitions || []
    // 选中项刷新后按 (defKind, defName) 重新对齐；无选中/已消失则取第一个
    const cur = selected.value
    const found = cur
      ? definitions.value.find(
          d => d.defKind === cur.defKind && d.defName === cur.defName)
      : undefined
    selected.value = found ?? definitions.value[0] ?? null
  } catch (e: unknown) {
    fitError.value = errMsg(e)
  } finally {
    fitLoading.value = false
  }
}
function openGeneratorFromVersion(v: SkillDefVersion) {
  genKind.value = v.defKind
  genName.value = v.defName
  genVisible.value = true
}
```

- 删除父组件中已内化到子组件的段落：fits 列表/明细/重算/诊断、fitPatterns、defVersions/versionGroups/deltaCards/labelDrafts/saveLabel、gen 生成器逻辑、版本对比模板。`listSkillFits/getSkillFitDetail/recomputeSkillFit/listSkillDefVersions/updateSkillDefVersion/generateSkillDefSteps/applySkillDefSteps/previewSkillDefSteps/diagnoseSkillFit` 导入一并清理，只留 `getSkillFitDefinitionSummary` 与 `SkillDefVersion` 类型。
- `refresh()` 改为 `void load(); void loadDefinitions()`；`onMounted(refresh)` 不变。
- 样式：`.fit-layout { display: grid; grid-template-columns: 280px 1fr; gap: 16px; align-items: start; }`、`.fit-layout__side { display: flex; flex-direction: column; gap: 8px; }`、`.fit-layout__global { align-self: flex-start; margin-top: 4px; }`、`.fit-empty { padding: 48px 0; text-align: center; }`。删除不再使用的 `.fit-vg*`/`.fit-delta*` 样式。
- 「调用聚合」tab（`<ElTabPane label="调用聚合" name="invocations">` 整块）与其 script 段**原样保留**，一字不动。

- [ ] **Step 4: 类型检查 + 构建**

Run: `npx vue-tsc && npx vite build`
Expected: 均无错误。

- [ ] **Step 5: 手动冒烟**（本地服务运行中）：打开 `http://127.0.0.1:5173/admin/ai-skillopt`，确认左栏列表渲染、点选定义右侧三个子标签数据正确、展开行两栏布局与诊断卡正常、全局偏离对话框可开、生成器对话框可开、调用聚合 tab 不变。

- [ ] **Step 6: 提交**

```bash
git add src/components/admin/skillopt/FitDefinitionList.vue src/components/admin/skillopt/DefOverview.vue src/views/admin/AiSkillOpt.vue
git commit -m "feat(ui): SkillOpt 任务拟合页重写为定义主从布局"
```

---

### Task 10: e2e 冒烟 + 全量构建收尾

**Files:**
- Create: `e2e/ai-full/ai-skillopt-page.spec.ts`

**Interfaces:**
- Consumes: Task 9 的页面结构（`.fit-def` 左栏项、`.def-ov` 概览、子标签名「版本演进/偏离模式/拟合结果」、空状态文案「暂无拟合数据」）。

- [ ] **Step 1: 写冒烟 spec**（登录模式照抄 `agent-action-gate.spec.ts` 的 `gotoChat` 前半段：真实表单登录，等真实元素，绝不等 networkidle）

```ts
/**
 * SkillOpt 任务拟合页冒烟（定义主从布局）——纯 UI，不烧 LLM。
 * 设计: docs/design/ai/SkillOpt任务拟合页面重设计.md
 * 约定: 等真实元素,绝不等 networkidle;空库时以空状态文案即视为通过。
 */
import { test, expect, type Page } from '@playwright/test'
import fs from 'node:fs'

async function login(page: Page): Promise<void> {
  await page.goto('/')
  const userInput = page.locator('input[placeholder*="用户名"]')
  try {
    await userInput.waitFor({ state: 'visible', timeout: 30_000 })
    await userInput.fill('admin')
    await page.locator('input[placeholder*="密码"]').fill('admin123')
    await page.getByRole('button', { name: /登\s*录/ }).click()
    await userInput.waitFor({ state: 'hidden', timeout: 15_000 })
  } catch {
    // 已是登录态(首页无登录表单)——直接继续
  }
}

test('主从布局：左栏列表或空状态 + 子标签切换', async ({ page }) => {
  test.setTimeout(180_000)
  await login(page)
  await page.goto('/admin/ai-skillopt')
  await expect(page.getByText('SkillOpt — 技能优化')).toBeVisible()
  // vite 冷编译可能触发整页重载——元素迟迟不出现就重进一次
  const firstDef = page.locator('.fit-def').first()
  const empty = page.getByText('暂无拟合数据')
  for (let i = 0; i < 3; i++) {
    if (await firstDef.isVisible().catch(() => false)) break
    if (await empty.isVisible().catch(() => false)) return  // 空库：空状态即通过
    await page.goto('/admin/ai-skillopt')
  }
  await firstDef.waitFor({ state: 'visible', timeout: 30_000 })
  await firstDef.click()
  await expect(page.locator('.def-ov')).toBeVisible()
  for (const name of ['版本演进', '偏离模式', '拟合结果']) {
    await page.getByRole('tab', { name }).click()
    await expect(
      page.locator('.fit-layout__main').getByRole('tabpanel').first(),
    ).toBeVisible()
  }
  fs.mkdirSync('e2e/screenshots/ai-full', { recursive: true })
  await page.screenshot({ path: 'e2e/screenshots/ai-full/skillopt-redesign.png',
                          fullPage: true })
})
```

- [ ] **Step 2: 跑冒烟**

Run: `npx playwright test e2e/ai-full/ai-skillopt-page.spec.ts`
Expected: PASS（本地服务 5173/8000 运行中）。

- [ ] **Step 3: 全量前端构建**

Run: `npm run build`
Expected: vue-tsc + vite build 通过。

- [ ] **Step 4: 后端回归**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_skill_fit_routes.py tests/test_skill_fit.py tests/test_skill_fit_ai.py -v`
Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add e2e/ai-full/ai-skillopt-page.spec.ts
git commit -m "test(e2e): SkillOpt 任务拟合页主从布局冒烟"
```

---

## 任务依赖

- Task 1/2/3 相互独立，可并行；Task 4 依赖 1/2/3。
- Task 5/6/7/8 只依赖 Task 4，可并行；Task 9 依赖 5/6/7/8；Task 10 依赖 9。
