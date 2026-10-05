# 数据管理功能全量 E2E 测试体系设计

> 日期：2026-10-06 ｜ 状态：设计已确认（待实施计划）
> 关联：[docs/ai-testing/01-测试方案.md](../../ai-testing/01-测试方案.md)（AI 侧姊妹篇；本套件补齐其 §2.2 明确排除的数据管理功能）

## 1. 背景与目标

check-manage 是配置驱动的动态数据管理平台，核心是「PageConfig 定义 schema → 动态生成 UI 与 API → 单表 JSONB 存数据」。已交付的 AI 全量测试体系（`e2e/ai-full/` + `server/tests/test_ai_full_*.py` + `docs/ai-testing/` 四件套）明确将**非 AI 的数据管理功能排除在外**，且 `e2e/` 目录下目前没有任何针对动态数据页面的 spec——系统最核心的主干（动态 CRUD、字段类型、关联、分支、导入导出、自动化引擎）处于 e2e 裸奔状态。

本设计在数据侧镜像 ai-full 模式，沉淀一套可复用的全量回归测试资产，验证数据管理能力的功能完整性、健壮性、可靠性与安全性。

## 2. 范围

### 2.1 八个能力族（全量）

| # | 能力族 | 核心代码 | 主要端点/入口 |
|---|--------|----------|--------------|
| A | 动态数据 CRUD + 三视图 | `server/routes/dynamic.py`、`src/views/dynamic/DynamicPage.vue` | `/api/<collection>`（无前缀动态 catch-all，RESERVED 集合排除保留路径）、`/api/<collection>/batch-*`、`row-actions/:id/run`、动态页面路由 |
| B | 字段控制类型 + workflow 状态机 | `src/types/field.ts`、`FieldConfigEditor.vue`、`routes/workflows.py` | PageConfig.fields、`/workflow/*` |
| C | 关联体系 | `routes/relations.py`、`data_relations` 表、`relation_graph.py` | `/relations/*`、`/relation-graph/*` |
| D | 项目分支 + 跨项目依赖 | `routes/project_versions.py`、`cross_project_dependencies.py` | `/project-versions/*`、`/projects/*/dependencies*` |
| E | 数据进出（导入/导出/ETL） | `import_runs.py`、`export_scripts.py`、`etl_tasks.py`、`menu_export.py`、`data_files.py` | `/importRuns*`、`/exportScripts*`、`/etlTasks*` |
| F | 自动化引擎 | `trigger_rules.py`、`validation_scripts.py`、`webhooks.py`、`open_api_row_actions.py` + dynamic.py 内 row-actions | `/triggerRules*`、`/validationScripts*`、`/webhook/*` |
| G | 列视图 + 查询台 | `column_views.py`、`query.py` | `/<page_id>/views*`、`/query/*` |
| H | 周边支撑 | `comments.py`、`timeline.py`、`backups.py` | `/comments/*`、`/timeline/*`、备份端点 |

### 2.2 明确排除

- AI 侧能力（已被 ai-full 覆盖）；数据管理仅在与 AI 交互相交处不重复展开。
- 工厂重置（FactoryReset）——破坏性操作，不进自动化用例。
- L1 单测查漏补缺——存量前后端单测已覆盖部分，缺口另行立项。
- 智能客服 kefu、首页组件、工作流设计器画布等非数据管理模块。

## 3. 交付物（三层全家桶）

| 层 | 位置 | 形态 |
|----|------|------|
| 文档 | `docs/data-testing/01-测试方案.md`、`02-数据管理功能测试用例.md`、`03-测试执行报告.md`、`04-缺陷记录与修复.md` | 四件套，格式对齐 `docs/ai-testing/` |
| L2 API | `server/tests/test_data_full_<族>.py`（八族各一文件） | pytest，健壮性/边界/越权/状态机断言 |
| L3 E2E | `e2e/data-full/helpers.ts` + 八族各一 spec 文件 | Playwright 真实链路（真实前后端，非 mock） |

### 3.1 目录结构

```
e2e/data-full/
  helpers.ts                  # DTEST- 前缀脚手架
  data-crud.spec.ts           # 族A
  data-field-types.spec.ts    # 族B
  data-relations.spec.ts      # 族C
  data-branches.spec.ts       # 族D
  data-io.spec.ts             # 族E
  data-automation.spec.ts     # 族F
  data-views-query.spec.ts    # 族G
  data-support.spec.ts        # 族H

server/tests/
  test_data_full_crud.py  test_data_full_field_types.py
  test_data_full_relations.py  test_data_full_branches.py
  test_data_full_io.py  test_data_full_automation.py
  test_data_full_views_query.py  test_data_full_support.py
```

## 4. 关键约定

1. **测试数据前缀**：一切测试资产（menu 名、页面名、记录标识字段值、任务名、脚本名）带 `DTEST-` + 时间戳（`DTEST-<族>-<用途>-<ts>`），与 AI 套件 `AITEST-` 互不干扰；前缀登记进 dev 库定点清理约定。
2. **用例自建页面**：每条用例（或每 spec 的 beforeAll）自建 PageConfig + Menu，collection 天然隔离，不依赖共享 demo 数据。
3. **登录**：走真实 `/api/auth/login`（admin/admin123）+ 共享 `e2e/.auth/admin.json`；低权限角色用例临时建角色/用户，用后删除。
4. **L3 模式**：API 断言为主 + 关键 UI 节点截图留证；但三视图操作与表单控件用例必须真实浏览器交互（点击、拖拽、填写），不得用 API 绕过。
5. **运行方式**：沿用根 `playwright.config.ts`（workers=1、baseURL :5173），`npx playwright test e2e/data-full`；pytest 按 `npm run test:server` 同款环境变量（Windows 下 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`）。
6. **Webhook stub**：在测试进程内起本地 HTTP server（Node `http`模块）接收回调，不依赖外部服务。
7. **环境依赖顺序**：Postgres → Flask(:3002) → Vite(:5173)。本套件不依赖 OpenCode/MCP（与 ai-full 的关键差异，跑得更快）。

## 5. 各族用例设计

用例编号规则：`TD-<族><序号>`（如 TD-A01），L2/L3 共用同一编号体系，02-用例文档中标注各编号落哪层。每族均按「正路径 → 边界/健壮性 → 安全/越权」配齐。

### 族A 动态数据 CRUD + 三视图
- 建页（PageConfig+Menu）→ 表格视图新建/编辑/删除记录（DynamicForm 真实 UI）→ 分页、关键字搜索。
- `batch-create`/`batch-delete`：批量语义、部分失败处理。
- 三视图切换：表格↔看板↔Excel（Univer）；看板拖拽卡片改分组字段并回写；Excel 单元格编辑回写；视图切换后数据一致。
- 并发更新冲突：两条会话改同一条记录，冲突提示与解决路径（`conflict.ts`）。
- 健壮性：不存在 collection 404（不泄漏存在性）、畸形 JSON、超长/越界字段值。
- 安全：低权限角色读写 collection 被拒（403）。

### 族B 字段控制类型
- `autoSequence`：按配置格式自增（如 IC-001→IC-002）；并发创建不重号。
- `autoTimestamp`：创建时填充、更新时刷新。
- `compositeText`：sourceFields 按分隔符拼接、源字段变更后重算。
- `select/multiSelect/radio/checkbox`：枚举值校验、非法值拒绝；`date/datetime` 边界与时区表现。
- `file/image`：真实上传小文件、列表/详情回显、替换与删除。
- `markdown`：编辑、表格内摘要、详情渲染。
- **workflow 状态机**：定义 transitions（含角色门禁/条件/动作）→ 创建实例 → 正向流转 → 越权流转被拒 → inbox 列表可见。

### 族C 关联体系
- `relation`（M:N）：A 侧挂 B 后 B 侧反向可见；解绑对称清理 `data_relations`。
- `reference`（1:N）：子记录继承父字段；父字段变更的继承语义；删除父记录时子记录约束行为。
- `quoteSelect`：单向多选引用；被引用记录删除时的引用方表现。
- `relation-graph`：出图数据两级 API 结构正确；表单内关联选择器真实交互（搜索、选中、回显）。

### 族D 分支 + 跨项目依赖
- 建分支：全 collection 记录复制到新 branch_id；分支内修改不影响主分支数据。
- `diff` 输出分支差异；`merge`/`merge-detailed` 合并回主分支；`merge-history` 留痕。
- 分支锁/主分支锁：lock 后写入被拒、unlock 恢复。
- `switch`/`switch-main`/`current-branch`(GET/PUT) 分支切换语义。
- `restore` 恢复、`delete-impact` 影响查询、DELETE 分支（删除后主分支数据完好）。
- 跨项目依赖：建依赖 → `validate` → `dependents`/`scan-relations` → `delete-check`/`merge-check`/`merge-order` → `update-dependencies-after-merge` 全链路。

### 族E 数据进出
- 导入：上传真实 xlsx → 校验/预览 → 落库 → 失败行 `retry-result` 重试成功。
- ETL：建多步任务 → `dry_run` 不落库 → 真跑（totalRecords/successCount/errorCount 断言）→ logs 与进度 → 运行中 `cancel` → 重跑幂等（结果不重复翻倍）。
- 导出脚本：CRUD → `test`/`debug` → `execute` 下载并断言文件内容 → `batchExport`。
- 菜单导出→导入回环：导出的包重新导入，页面/配置/数据等价。

### 族F 自动化引擎
- 触发规则：字段变更命中条件 → `/triggerRules/<id>/logs` 断言触发；不命中不触发；禁用规则不触发。
- 校验脚本：`test` 端点通过/不通过两态；记录保存时非法值被拦截且错误信息可见。
- Webhook：规则指向测试内本地 stub → 修改记录 → stub 收到请求（断言方法/路径/payload 结构/签名头）→ 目标不可达时的重试与 `logs` 留痕。
- 行动作：配置 action → UI 行上按钮触发 `row-actions/:action_id/run` → 目标字段回写生效；Open API 行动作入口与内部入口行为一致。

### 族G 列视图 + 查询台
- 列视图：建/改/删、设默认视图、`copy`；UI 列显隐/顺序随视图持久化（换视图后表格列变化）。
- 查询台：`/query/collections` 返回目录；`/query/execute` 合法查询出数、非法语法被拒且不 500。

### 族H 周边支撑
- 评论：CRUD；只能编辑/删除自己的评论（他人 403）。
- 时间线：记录创建/变更自动落痕，`/timeline/*` 可读且顺序正确。
- 备份：创建备份 → 列表可见 → 下载校验内容；测试产生的备份测完即删。

## 6. 测试维度与准出

| 维度 | 数据侧关注点 | 准出标准 |
|------|-------------|----------|
| 功能完整性 | 每个端点/UI 入口正路径、分支状态机、ETL/导入状态流转、边界值（分页、批量上限、文件大小） | 高/中优先级用例全部通过 |
| 健壮性 | 畸形 JSON、不存在资源 404 不泄漏存在性、错误契约（中文 error + code）、并发建单/并发更新 | 无 500 泄漏堆栈 |
| 可靠性 | ETL 中断后重跑幂等、导入失败行重试、分支合并冲突收敛、锁防串写 | 容灾机制按设计生效 |
| 安全性 | 跨 collection/记录 IDOR、低权限角色写被拒、校验脚本与导出脚本的执行面（平台执行用户脚本，恶意脚本须被拒/沙箱约束） | 安全用例 100% 通过，发现即修 |

## 7. 数据隔离与清理

1. 清理顺序：先删分支/依赖/关联/记录 → 再删 menu 与 pageConfig。menu 删除是否级联清理 pageConfig 与 dynamic 数据，**在族A 打样时核实**；若不级联，helpers 提供二段删。
2. 每条用例 afterAll/teardown 自清理；异常中断残留靠 `DTEST-` 前缀做 dev 库定点清理（扩展现有清理约定）。
3. 红线：不修改/删除任何非 `DTEST-` 数据；不触发工厂重置；备份用例产物测完即删；低权限角色/角色授权用后删除。

## 8. 留证与执行

- 截图：`e2e/screenshots/data-full/`（Playwright 自动留证 + 关键节点显式 `screenshot()`）。
- 关键证据归档 `docs/data-testing/evidence/`：导出文件内容、stub 收到的 webhook payload、合并 diff 报告、冲突解决过程。
- 每轮全量执行 = `npx playwright test e2e/data-full` + `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest server/tests/ -k test_data_full -v`，结果与时长写入 `03-测试执行报告.md`。
- 规模预估：L2 约 80–120 例、L3 约 40–60 例，以 02-用例文档定稿为准。

## 9. 缺陷流程（硬规则）

1. **发现即修**：缺陷立即修复，逐条记入 `04-缺陷记录与修复.md`（现象 → 根因 → 修复 → 回归证据）。
2. **判别力先行**：新测试必须先在修复前的 commit 上跑出失败，证明测试能抓住问题，再修复转绿。
3. 行为变更涉及用户可见语义的，同步核对 `docs/user-guide/` 对应文档。

## 10. 实施节奏

- 分支：`feat/data-e2e`（避开并行的 `feat/batch-e2e-redesign`）。
- 顺序：helpers + 族A 打样（核实级联删除语义、定型 helpers）→ 族B/C → 族D/E → 族F/G/H → 02-用例文档定稿 → 首轮全量跑 → 03-执行报告 + 04-缺陷记录。
- 每族独立跑绿后再进下一族；中途发现缺陷按 §9 处置。

## 11. 后续步骤

设计经用户确认后，转入 writing-plans 技能产出实施计划（按 §10 节奏拆任务）。
