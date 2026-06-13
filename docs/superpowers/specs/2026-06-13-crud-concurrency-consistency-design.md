# CRUD 并发读写一致性加固 设计文档

> 日期：2026-06-13
> 分支：`feat/neutral-slate-ui-redesign`（或新建独立分支，见实现计划）
> 子系统 A（两子系统拆分中的第一个；子系统 B「跨页可自定义工作流引擎」单独 spec）

## 背景与现状诊断

平台所有业务数据走单表 `dynamic_data`（JSONB），CRUD 由通用处理器 `server/routes/dynamic.py` 承载。底层：`server/db.py` 连接池（`ThreadedConnectionPool` 2–20）+ 默认 READ COMMITTED；`get_db()` 上下文管理器在正常退出时 `commit`、异常 `rollback`。

`dynamic_data` 结构（`server/init_db.py:33`）：`PRIMARY KEY (id, branch_id)`、`version INTEGER NOT NULL DEFAULT 1`、`branch_id`。技术行 `id` 在分支内唯一（DB 保证）。

### 已有防护（无需改）
**单记录更新乐观锁**：`update_item`（`dynamic.py:594`）先 `SELECT data, version`（`:642`），客户端回传 `_version` 不符即 `409 VERSION_CONFLICT`（`:680`）；即便不回传，UPDATE 也是 `WHERE version = db_version` 的 CAS + `rowcount==0` 兜底（`:714`）；字段级 merge `{**old, **new}`（`:653`）使改不同字段的并发互不覆盖。前端单记录表单**确实回传** `_version`（`src/stores/pageConfig.ts:583`）。→ 更新冲突已真实防护。

### 真实并发缺陷（本 spec 修复）
1. **autoSequence 客户端生成**（`src/stores/pageConfig.ts:731` `generateNextSequenceValue`、`:765` `batchGenerateSequenceValues`）：从缓存 `pageDataCache` 取 `max+1`。两个客户端基于相同缓存生成**相同序列号**；无后端原子分配。
2. **业务主键唯一性 check-then-insert 竞态**（`create_item` `dynamic.py:526` 检查 → `:550` 插入）：READ COMMITTED 下两个并发创建都 SELECT 不到、都 INSERT；技术 `id` 不同，DB 主键拦不住 → **重复业务主键**（JSONB 字段上无唯一索引）。

### 不在范围（现状保留）
- 触发器/webhook 在主事务提交后异步另起事务（`dynamic.py:572`）——最终一致，设计取舍，保留。
- 分支 merge 并发——属版本系统，另议。
- 跨记录关系强一致——关系写入已与主数据同事务（`:557`/`:724`），足够。

## 目标

消除**创建路径**的重号 / 重复主键并发缺陷；把已生效的更新乐观锁补齐为**全路径契约 + 统一冲突 UX**。后端成为标识的权威分配方，前端不再自行生成易冲突标识。**不改业务语义、不改数据模型（仅新增计数表）**。

### 已确认取向（brainstorming 决议）
1. autoSequence：后端保存时原子分配，表单显示「保存后生成」。
2. 手填主键去竞态：事务级 `pg_advisory_xact_lock`（按 collection + 主键值）。

## 设计

### ① autoSequence 后端原子分配

**新表** `dynamic_sequences`：
```sql
CREATE TABLE IF NOT EXISTS dynamic_sequences (
    collection   VARCHAR(200) NOT NULL,
    branch_id    VARCHAR(100) NOT NULL DEFAULT 'main',
    field_name   VARCHAR(200) NOT NULL,
    current_value BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (collection, branch_id, field_name)
);
```

**分配（在 `create_item` 内、同一事务）**：对目标计数行 `SELECT current_value FROM dynamic_sequences WHERE … FOR UPDATE`（行锁串行化分配）。
- 计数行存在：`next = current_value + 1`，`UPDATE … SET current_value = next`。
- 计数行不存在：从现有数据该字段的最大数值**播种**——扫描 `dynamic_data`（同 collection+branch）该字段已有值、去前缀解析数字取 max（`seed`），`next = seed + 1`，`INSERT … VALUES (…, next)`。避免与历史记录撞号。
- 批量创建 N 条：一次性 `next_start … next_start+N-1`（`current_value += N`，原子取一段区间）。

**格式化移到后端**：读字段 `sequenceConfig`（`prefix` + `max` 决定补零位数），后端 `get_page_info` 已能拿到字段配置；生成 `f"{prefix}{n:0{pad}d}"`。写入 `data[field_name]` 后再做主键唯一检查与插入。

**前端**：创建时不再生成 autoSequence。`DynamicForm` 中 autoSequence 字段渲染为只读占位「保存后生成」；保存后用后端返回记录回填展示。移除创建期对 `generateNextSequenceValue`/`batchGenerateSequenceValues` 的调用（函数可保留供他用或一并删除，由实现计划定）。

### ② 手填主键 advisory lock 去竞态

`create_item` 与 `update_item` 在唯一性检查**之前**、同一事务内：对每个**非 autoSequence** 主键字段的当前值取
`SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))`，两个 int4 参数为 `collection` 与 `主键值字符串`（复合主键：对各主键字段值拼接后取一次锁，或逐字段取锁——实现计划定，优先「拼接值一次锁」）。
- 事务级锁，提交/回滚自动释放。
- 同一主键值的并发创建被串行化：后到者阻塞至先到者提交，再 SELECT 即见 → `409`。
- autoSequence 主键由 ① 原子分配天然唯一，**不**对其取 advisory lock（其值在分配前未知，且分配已串行）。
- `update_item` 仅当更新会改变主键字段值时取锁（与现有 `check_primary_key_unique(exclude_id=…)` 配合）。

### ③ 更新乐观锁全路径契约 + 统一冲突 UX

- **审计补齐**：核查所有发起更新的前端路径都回传 `_version` —— 单记录表单已确认（`pageConfig.ts:583`）；重点核查 **Excel 视图批量保存**、**批任务/AI 写回**等路径，缺失则补上（从各自缓存记录取 `_version`）。后端保持 best-effort（不强制要求 `_version`，CAS 仍兜底），但前端应尽量提供以获得跨会话防护。
- **统一冲突处理**：前端对 `409 VERSION_CONFLICT` 统一拦截 —— 提示「数据已被他人修改，请刷新后重试」+ 一键重新拉取该记录（覆盖表单/缓存），用户基于最新值重做修改。**不做差异合并视图**（YAGNI）。

### ④ 隔离级别

维持 READ COMMITTED + 上述**定点锁**（计数行锁 + 主键 advisory lock），不全局升 SERIALIZABLE（避免全局重试复杂度）。

## 迁移

`server/migrations/<date>_dynamic_sequences.py`（幂等）：
1. 建 `dynamic_sequences` 表（`IF NOT EXISTS`）。
2. 为每个 `(collection, branch_id, autoSequence 字段)` 按现有 `dynamic_data` 该字段 max 播种 `current_value`（仅当该计数行不存在时）。需读 `page_configs` 找出各 collection 的 autoSequence 字段及其 prefix。
3. 可重复执行：再次运行不重复播种、不回退已分配值。

## 测试策略

**后端（核心，pytest）**：
1. **并发创建无重号**：多线程并发对同一 collection 创建带 autoSequence 的记录（如 20 线程），断言生成的序列号集合无重复、连续。
2. **并发创建无重复主键**：多线程并发用**相同手填主键**创建，断言恰好 1 条成功、其余 `409`。
3. **序列原子分配**：单元测试分配函数——存在计数行 `+1`、不存在则按现有 max 播种 `+1`、批量 `+N` 区间正确、格式（prefix+补零）正确。
4. **播种迁移**：构造含既有编号的数据 → 运行迁移 → 计数值 == max；再运行幂等。
5. **advisory lock 串行化**：验证同主键并发被串行（先到成功、后到 409）。
6. **回归**：现有 `dynamic.py` 相关测试保持通过。

**前端（vitest）**：
1. autoSequence 字段在创建表单渲染为只读「保存后生成」，创建提交体不含客户端生成的序列值。
2. 创建流程不调用 `generateNextSequenceValue`/`batchGenerateSequenceValues`。
3. `409 VERSION_CONFLICT` → 触发刷新 UX（提示 + 重新拉取）。

**手工/集成**：在 dev 库用两个会话并发创建验证无重号。

## 净效果

- 创建路径：序列号由后端原子分配（计数表 + 行锁 + 批量区间）、手填主键由 advisory lock 串行去重 → **无重号、无重复主键**。
- 更新路径：已生效的乐观锁补齐为全前端路径契约 + 统一 `409` 刷新体验。
- 仅新增一张计数表 + 一处幂等迁移；不改数据模型与业务语义。
