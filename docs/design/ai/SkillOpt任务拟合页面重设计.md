# SkillOpt 任务拟合页「定义主从布局」重设计

- 日期：2026-09-30
- 状态：已评审（brainstorming 通过，方向：定义主从布局）
- 关联：`docs/design/ai/SkillOpt任务拟合设计.md`（功能设计）、`2026-09-17-ai-execution-audit-skillopt-spec.md`（审计底座）

## 1. 背景与问题

任务拟合页（`AiSkillOpt.vue`，913 行单文件）当前把三类不同形状的数据竖向堆在一个滚动页里：

1. **拟合结果表**（6 列 + 展开行内又塞 4 层内容：步骤明细、操作按钮、子代理拟合表、诊断面板）；
2. **偏离模式表**（跨定义全局聚合）；
3. **定义版本对比**（每个定义一块：头部 + Δ 卡行 + 7 列表格，v-for 块随定义数无限拉长）。

三类区块没有主从关系，想看「定义 X 的拟合 → 偏离 → 版本演进」要在三处之间来回对照；每个区块前还有一段 muted 说明文字，视觉锚点弱；无任何筛选。

## 2. 目标 / 非目标

**目标**

- 以「定义」为中心重组信息架构：左栏定义列表，右侧只讲选中定义的完整故事。
- 消除版本对比的堆叠块，改为纵向时间线，Δ 并入行内。
- 重组拟合结果展开行（两栏网格），控制展开后的视觉长度。
- 为列表加状态筛选；跨定义聚合（偏离 Top）收进对话框入口。
- 顺带治理单文件：拆出子组件，父组件只留数据加载与选中态。

**非目标**

- 调用聚合 tab 完全不动（本次只重设计任务拟合 tab）。
- 不做趋势 sparkline、移动端适配；不改后端聚合口径与表结构（无迁移）。
- 不改变任何现有 API 的语义；只新增可选查询参数与一个汇总端点。

## 3. 总体布局

```
┌────────────────────────────────────────────────────────────┐
│ SkillOpt — 技能优化            共 N 技能 · M 次调用  [刷新]  │
├──────────────┬─────────────────────────────────────────────┤
│ 定义列表      │ 定义头：名称 · 类型徽标 · 最新版本(标注/hash)  │
│ (280px 固定) │ 指标卡：任务数 · 拟合率 · 偏离数 · 版本数      │
│              │ ──────────────────────────────────────────  │
│  每项：名称   │ [拟合结果] [版本演进] [偏离模式]  ← 子标签     │
│  类型+拟合率  │                                             │
│  +版本/偏离   │              （子标签内容区）                 │
│ ──────────   │                                             │
│ 全局偏离 Top5 │                                             │
└──────────────┴─────────────────────────────────────────────┘
```

- 进入页面默认选中「最近活动」的定义；左栏为空时右侧显示引导性空状态。
- 选中定义是整页唯一的主状态，右侧三个子标签全部围绕它渲染。

## 4. 左栏：定义列表（`FitDefinitionList.vue`）

- 每项展示：定义名、类型徽标（技能/代理/其他）、拟合率（无数据显示 `—`）、版本数、偏离数小徽标（diverged+partial，0 不显示）。
- 排序：最近活动倒序（`lastActivity`）；无活动的定义排其后（按名称）。
- 选中态高亮；键盘导航不做（YAGNI）。
- 底部固定入口「全局偏离 Top」：打开对话框，内容为现有 `/skill-def-patterns`（不加过滤）的跨定义聚合表，原样搬入。

## 5. 右栏子标签一：拟合结果（`FitResultsPanel.vue`）

- 数据：`/skill-fit?defKind=&defName=&limit=100`（后端加这两个可选参数，见 §9）。
- 列精简为：时间 / 会话（短 hash，title 全值）/ Agent / 拟合点阵 + 状态（`SkillFitBadge`）/ 分 / 操作。
- 表格上方状态筛选 chips：全部 / 拟合(fit) / 部分(partial) / 偏离(diverged) / 其他（failed · inconclusive）。前端过滤即可（单定义数据量 ≤ limit）。
- **展开行两栏重组**：
  - 左列（约 60%）：步骤明细表（现有列不变）+ 子代理层拟合表（现有逻辑不变）。
  - 右列（约 40%）：诊断卡——偏差原因徽标、建议列表、修订草案 JSON（默认折叠，展开后可复制）。
  - 「重新计算 / 分析偏差」收进展开区右上角小工具条；「分析偏差」仅 partial/diverged 显示（现有逻辑）。
- 明细仍按需懒加载（展开时拉 `getSkillFitDetail`），保留 `detailMap` 缓存与重算后失效逻辑。

## 6. 右栏子标签二：版本演进（`DefVersionTimeline.vue`）

- 数据：`/skill-def-versions?defKind=&defName=`（已支持），**纵向时间线**（新版本在上）。
- 每个版本节点一行内联呈现：
  - 版本标注（行内编辑，回车保存，保留现有 `saveLabel` 交互与「已保存」提示）；
  - 短 hash（title 全值）+ 首见时间；
  - 指标：任务数 / 均分 / 拟合率；
  - 偏离分布徽标（偏离 N · 部分 N，均为 0 时显示「无偏离」）；
  - **「较上一版」Δ 内联徽标**：均分（↑绿 ↓红）、偏离数（变多红、变少绿）、拟合率。首个版本无 Δ。
  - 行尾「生成步骤」按钮（打开生成器对话框，行为不变）。
- Δ 计算沿用现有 `versionGroups` 相邻版本差值逻辑，只是呈现位置从独立 Δ 卡行移入时间线行内。

## 7. 右栏子标签三：偏离模式（`DefPatternsPanel.vue`）

- 数据：`/skill-def-patterns?defKind=&defName=`（后端加 `defName` 可选参数，见 §9）。
- 列：偏差步骤 / 偏离任务数（可排序）/ 涉及版本数 / 版本 hash / 均分。
- 空数据文案：「该定义无步骤级偏离记录」。

## 8. 定义概览（`DefOverview.vue`）

- 定义头：名称、类型徽标、最新版本的标注或短 hash。
- 四张指标卡：任务数（该定义有拟合结果的任务数）、拟合率、偏离数（diverged+partial）、版本数。
- 数据全部来自汇总端点（§9.3），前端不再做跨端点拼算。

## 9. 后端 API 变更（`server/routes/ai_session_admin.py`）

无表结构变更、无迁移。三项均为查询参数级改动：

1. **`GET /skill-fit`** 加 `defKind` / `defName` 可选参数：`WHERE (%s IS NULL OR r.def_kind = %s) AND …`，与 `sessionId` 过滤可叠加。
2. **`GET /skill-def-patterns`** 加 `defName` 可选参数（现有 `defKind` 同款写法）。
3. **新增 `GET /skill-fit/definition-summary`**：左栏与概览卡数据源。单条 SQL：
   - 定义清单 = `ai_skill_def_versions` 与 `ai_skill_fit_results` 按 `(def_kind, def_name)` 的**并集**（保证从未跑过任务的新定义也出现在左栏，可到达「生成步骤」入口——当前版本对比区可达，不能回归）；
   - 每个定义的指标与版本端点同口径：`tasks = count(拟合行)`、`fitRate = fit 状态行占比`（分母只计有拟合结果的任务）、`avgScore = sum(score)/tasks`、`partialCount` / `divergedCount`（FILTER 统计）、`versions = def_versions 中该定义 content_hash 去重数`、`lastActivity = max(computed_at)`（无拟合行为 null）。
   - 响应：`{ definitions: [{ defKind, defName, tasks, fitRate, avgScore, partialCount, divergedCount, versions, lastActivity }] }`。

`aiSkills.ts` 同步：`listSkillFits` 参数加 `defKind`/`defName`，`listSkillDefPatterns` 新增封装（当前页面是裸 `get`，顺手收进 API 层），新增 `getSkillFitDefinitionSummary()`。

## 10. 前端组件拆分

```
src/views/admin/AiSkillOpt.vue                 容器：数据加载、选中定义状态、布局；
                                               调用聚合 tab 原样保留
src/components/admin/skillopt/
  FitDefinitionList.vue                        左栏（§4）
  DefOverview.vue                              定义头 + 指标卡（§8）
  FitResultsPanel.vue                          拟合结果子标签（§5，含展开明细与诊断卡）
  DefVersionTimeline.vue                       版本演进子标签（§6）
  DefPatternsPanel.vue                         偏离模式子标签（§7）
  StepGeneratorDialog.vue                      生成器对话框（自现有代码原样抽取，逻辑不变）
```

- 父组件持有：`definitions`（汇总）、`selected`（当前定义）、各子标签数据按需加载并随选中切换。
- `detailMap`（attempt 明细缓存）与 `diagMap`（诊断缓存）归属 `FitResultsPanel` 内部状态；重算/诊断完成后向父 emit，父刷新汇总端点数据以同步概览卡与左栏。
- 子组件全部 props 进 / emits 出，不引入共享 store 或 composable 抽象（YAGNI）。
- `SkillFitBadge` 不动；格式化 helpers（时间/hash/百分比/Δ 着色）按使用方就近分配，不做公共抽层。

## 11. 空状态与边界

- 左栏空：右侧整块空状态——解释拟合结果如何产生（任务执行后自动计算）+ 提示可在技能定义 frontmatter 配置 `fit.steps`。
- 某子标签空：各自空文案（§6/§7），不显示空表格骨架。
- 定义既有版本又无拟合行：指标卡显示 `—`，版本时间线正常可用（含生成步骤）。
- 重算后：当前选中定义的概览与子标签数据刷新（复用现有「重算覆盖后诊断失效 + 重新拉列表」逻辑）。

## 12. 测试与验收

- `vue-tsc` 类型检查 + 生产构建通过。
- Playwright 冒烟（沿用 `e2e/ai-full` 骨架，3002 端口）：管理员登录 → 打开 SkillOpt 管理页 → 断言左栏渲染（有定义则列表项、无则空状态文案）→ 点选定义 → 断言右栏定义头更新、三个子标签可切换且无控制台错误。
- 后端三项参数/端点：pytest 路由级用例（真实开发 PG）：过滤参数生效、summary 并集含无拟合定义、口径与版本端点一致（同一份数据 fitRate 相等）。
- 真实数据人工验收：与旧版页面数字对照（任务数/拟合率/偏离数应一致，仅呈现变化）。
