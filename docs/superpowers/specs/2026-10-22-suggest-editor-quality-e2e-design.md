# 智能写作质量检查 E2E 测试设计

- 日期：2026-10-22
- 范围：智能写作编辑器的词表、智能检查（本地规则 + AI 语义）、表格/导入场景
- 目标：用**确定性、可复现**的 E2E 覆盖技术文档常见缺陷，而不是只验证“输入后出现一张卡片”
- 现有相关 spec：`e2e/suggest-editor.spec.ts`、`suggest-editor-rules.spec.ts`、`suggest-editor-word-parity.spec.ts`、`suggest-editor-snippets.spec.ts`

## 1. 设计原则

1. **确定性优先**：机械可判定的缺陷全部用本地规则（maps/patterns/whitelists/presence + 内置格式规则）覆盖；不依赖模型措辞。
2. **AI 语义也要可测**：不调用真模型。扩展内置 mock agent，按“prompt 里的规则/要点名 + 文档里的缺陷短语”返回固定建议，验证 ACP 全链路、建议锚定与 UI。
3. **一个缺陷一个断言**：每个场景只放一个主要缺陷，避免多张卡片互相干扰；断言卡片来源、机制、锚定文本、严重级别与接受/忽略行为。
4. **状态自包含**：每个用例通过 API 建文档/写规则/写词表，finally 恢复全局规则与删除文档；不依赖前一个用例。
5. **真实链路**：浏览器 → 编辑器 → Flask/Node/ACP → 卡片 → 接受后正文替换 → 刷新恢复，全部走真实组件，不 mock 前端。
6. **可定位失败**：卡片增加稳定的 `data-rule-id` / `data-source` / `data-mechanism`（实现建议见 §7），测试不以文案模糊匹配为主。

## 2. 技术文档常见缺陷分类（覆盖靶子）

| 类别 | 典型缺陷 | 检查机制 |
| --- | --- | --- |
| 术语与命名 | 同一设备多种叫法、缩写未定义、型号大小写/连字符不一致 | 词表别名、maps、whitelists |
| 数据与单位 | 带宽/容量单位不一致（M/G vs Mbps/Gbps）、日期/版本格式混乱、IP/VLAN/端口越界 | patterns、whitelists |
| 结构与完整性 | 缺背景/范围/风险/回滚/验收标准、缺负责人/日期、章节层级跳级 | 检查要点、presence、AI |
| 逻辑与一致性 | 前后数字矛盾、总计≠明细、时序倒置、结论无依据、绝对化承诺 | AI（aiPrompt/检查要点） |
| 指代与歧义 | “该系统/前者/上述”无明确指代、模糊限定词 | AI |
| 表达与格式 | 连续空格、标点前空格、重复标点、的的、半角逗号、ASCII 省略号 | 内置本地规则 |
| 安全与合规 | 明文密码、缺权限说明、危险操作无回滚、敏感信息外发 | AI + presence |
| 表格/图 | 单元格型号/单位错误、空单元格、图注缺失/与正文引用不一致 | 本地规则（单元格文本）+ AI（表格序列化/图注） |
| 导入/协作 | Word 导入后规则失效、导入即全量模型调用、跨标签页规则不生效 | 本地规则 + 导入基线 + BroadcastChannel |

## 3. 检查机制与断言口径

| 机制 | 触发方式 | UI 表现 | 可接受替换 |
| --- | --- | --- | --- |
| maps | 文本出现 wrong | 本地卡片，可接受 | 是 |
| patterns | extract 命中且 valid 不通过 | 本地提醒，不可接受 | 否 |
| whitelists | 锚点后一个/多个值不在清单 | 本地提醒 | 否 |
| presence | 全文有 triggers 且 requiresAny 全缺 | 本地提醒；补任一词即消失 | 否 |
| aiPrompt / 检查要点 | 停笔后 AI 分析 | AI 卡片，reason 含规则/要点名 | 由建议决定 |
| 内置格式规则 | 输入即算 | 本地卡片 | 仅部分（连续空格等） |
| 词表 | 输入前缀/别名 | 联想/替换 | 是 |

断言必须至少包含：卡片来源（local/AI）、严重级别、锚定文本、reason 中的规则/要点名、接受/忽略后的正文或列表变化。

## 4. 确定性 AI 方案（必须的前置改造）

现状：`suggest-editor-server/mock-agent.mjs` 只有 5 条固定规则，无法覆盖语义类缺陷。

设计：给 mock agent 增加一张**测试语义表**，只在分析 prompt 同时满足以下条件时产出建议：

- prompt 包含指定规则名或检查要点标记（说明规则已注入）；
- 文档快照包含指定缺陷短语。

示例（实现时写入 `mock-agent.mjs`）：

```js
const SEMANTIC_CASES = [
  { promptHas: "绝对化表述", textHas: "支持所有型号",
    quote: "支持所有型号", replacement: "支持 9800M12、9600M12",
    reason: "[绝对化表述] 不要承诺支持所有型号，需列出适用范围", severity: "warn" },
  { promptHas: "指代明确", textHas: "该系统",
    quote: "该系统", replacement: "本方案中的 9800UPS 系统",
    reason: "[指代明确] “该系统”缺少明确指代", severity: "info" },
  { promptHas: "数据一致性", textHas: "总计 10 台",
    quote: "总计 10 台", replacement: "总计 12 台",
    reason: "[数据一致性] 总计与明细表 12 台不一致", severity: "error" },
  { promptHas: "回滚方案", textHas: "上线步骤",
    quote: "上线步骤", replacement: "",
    reason: "[回滚方案] 缺少回滚步骤与触发条件", severity: "warn" },
  { promptHas: "权限与加密", textHas: "管理员密码",
    quote: "管理员密码", replacement: "管理员密码（加密存储）",
    reason: "[权限与加密] 不得明文描述密码处理方式", severity: "error" },
]
```

`buildSuggestions(text, promptText)` 在原有 5 条规则之后追加匹配项，仍受“最多 5 条”上限约束（每个用例只放 1 个缺陷，不会互相挤掉）。

约束：
- 该增强仅影响 `AGENT_KIND=mock`，生产真模型路径不变；
- 语义用例必须同时验证“规则名出现在 prompt 里”，避免 mock 在规则未注入时也误报；
- 保留一个可选的真模型烟测脚本（`e2e/manual/`，不进 CI），人工验证真实模型对同类缺陷的判断。

## 5. 测试文件与职责

| 文件 | 职责 | 预计用例 |
| --- | --- | --- |
| `e2e/helpers/suggest-editor-quality.ts` | 登录/建文档/写全局规则/写文档规则/写词表/断言卡片/清理 | — |
| `e2e/suggest-editor-quality-local.spec.ts` | 内置格式规则、maps、patterns、whitelists、presence、严重级别、禁用、忽略/接受、表格单元格 | 12–14 |
| `e2e/suggest-editor-quality-ai.spec.ts` | 绝对化、指代、数据一致性、缺回滚、安全、AI 表格锚定、跨标签页 | 7–8 |
| `e2e/suggest-editor-quality-vocab.spec.ts` | 全局/文档词表、别名替换、覆盖、导入导出、隔离 | 5–6 |
| `e2e/suggest-editor-quality-import.spec.ts` | 导入文档的本地规则、导入基线、图注规则 | 3–4 |
| `e2e/manual/suggest-editor-real-model-quality.mjs` | 可选真模型语义烟测（不进 CI） | 1 脚本 |

已有用例保留，不重复：
- `suggest-editor-rules.spec.ts`：map/presence/隔离、AI 生成、跨标签页本地规则、502、白名单多值；
- `suggest-editor-word-parity.spec.ts`：表格单元格白名单、图片图注、导入基线/导出；
- `suggest-editor.spec.ts`：模板、AI mock 建议、持久化、词表入口。

新套件补齐的是：**内置格式规则全量、patterns/whitelists/disabled/severity、AI 语义缺陷矩阵、词表矩阵、导入后规则仍生效**。

## 6. 场景目录

### 6.1 本地确定性（`quality-local.spec.ts`）

| ID | 缺陷 | 文档输入 | 规则/机制 | 断言 |
| --- | --- | --- | --- | --- |
| L1 | 连续空格 | `方案  说明` | 内置 | 卡片“连续空格”，source=local，error；接受后变单空格 |
| L2 | 标点前空格 | `完成 ，继续` | 内置 | “标点前多余空格”，warn；接受后 `完成，继续` |
| L3 | 重复标点 | `完成。。` | 内置 | “重复标点”，error；接受后单句号 |
| L4 | 重复助词 | `我们的的方案` | 内置 | “重复助词「的的」”，error |
| L5 | 半角逗号 | `设备A,设备B` | 内置 | “中文之间的半角逗号”，info；接受后全角 |
| L6 | ASCII 省略号 | `等待...` | 内置 | “省略号应为「……」”，info |
| L7 | 型号命名 | `设备 S-5700 上线` | map `S-5700→S5700` | 卡片锚定 `S-5700`，可接受；接受后正文 `S5700`，刷新仍生效 |
| L8 | 端口类型白名单（含多值） | `端口类型：GE；10GE；25GE` | whitelist values `GE/10GE/100GE` | 只锚定 `25GE`，notice 不可接受；改 `100GE` 后消失 |
| L9 | 单位格式 | `带宽 10G` | pattern extract `\d+\s*[MG](?:bps)?`、valid `^\d+\s*[MG]bps$` | 提醒“单位需写 Mbps/Gbps”；改 `10Gbps` 后消失 |
| L10 | 日期格式 | `计划 2026/10/1 上线` | pattern extract `\d{4}/\d{1,2}/\d{1,2}`、valid `^\d{4}-\d{2}-\d{2}$` | 提醒日期格式；改 `2026-10-01` 后消失 |
| L11 | 条件完整性（配套设备） | `9600M12 组网` | presence triggers `9600M12`、requiresAny `9600UPS/不涉及拖带` | 提醒；补 `不涉及拖带` 后**立即**消失 |
| L12 | 条件完整性（许可/回滚） | `开启智能分析功能` | presence triggers `智能分析`、requiresAny `许可/授权/回滚` | 提醒；补 `已获授权，回滚见 5.2` 后消失 |
| L13 | 严重级别 | 三条规则 error/warn/info 各一 | 四类机制 | 卡片徽标分别为错误/警告/提示 |
| L14 | 禁用规则 | 上述 map 规则 `enabled=false` | maps | 不出现卡片；重新启用并广播后出现 |
| L15 | 忽略 vs 接受 | map + notice 各一 | maps/whitelist | 接受后正文替换并持久化；忽略本地提醒仅当前会话消失（刷新会重新计算，属已知语义）；AI 建议忽略后删除并持久化（见 A9） |
| L16 | 表格单元格内置格式 | 单元格 `完成。。  继续` | 内置重复标点/连续空格 | 单元格内出现两张本地卡片；接受后单元格文本规范化 |

实现要点：
- 每个用例独立文档；规则通过 API PUT，用例结束恢复快照。
- L1–L6 可合并在一个文档但分别断言 reason/severity，避免 6 次建文档；若合并导致卡片重叠，拆成两个文档。
- L9/L10 的 pattern 用 `extract` 只匹配候选，`valid` 判断合规；避免正则跨文本节点。

### 6.2 AI 语义（`quality-ai.spec.ts`）

前置：mock agent 已加入 §4 的 `SEMANTIC_CASES`。每个用例创建文档级 aiPrompt 或全局检查要点，输入缺陷文本，等待 AI 卡片（≤10s）。

| ID | 缺陷 | 文档输入 | 注入 | 断言 |
| --- | --- | --- | --- | --- |
| A1 | 绝对化承诺 | `本产品支持所有型号。` | aiPrompt 规则名“绝对化表述” | AI 卡片锚定 `支持所有型号`；reason 含规则名；接受后替换为限定型号 |
| A2 | 指代不明 | `该系统负责转发。` | aiPrompt “指代明确” | AI 卡片锚定 `该系统`；reason 含“指代” |
| A3 | 数据不一致 | 正文 `总计 10 台` + 表格明细 12 台 | aiPrompt “数据一致性” | AI 卡片锚定 `总计 10 台`；reason 指出明细 12 |
| A4 | 缺回滚方案 | 文档有 `上线步骤`，无回滚 | 全局检查要点 `回滚方案` | AI 卡片锚定 `上线步骤`；reason 含要点名 |
| A5 | 安全/权限 | `管理员密码写入配置文件` | 检查要点 `权限与加密` | AI 卡片锚定 `管理员密码`；severity=error |
| A6 | 表格内 AI 建议 | 表格单元格 `支持所有型号` | aiPrompt | AI 卡片锚定单元格内文本；刷新后仍在 |
| A7 | 跨标签页 AI 规则 | A 页文档已打开；B 页把 aiPrompt 从“无”改为“绝对化表述” | BroadcastChannel | A 页规则徽标数量更新；随后输入一个字符触发下一轮分析，出现对应卡片 |
| A8 | 本地/AI 同范围去重 | `这个方案非常不错。` + 本地 map `非常→空` | 内置 local + mock 既有“非常”规则 | AI 卡片到达后，同一 `非常` 范围只剩 AI 卡片；本地 map 卡片不与其重叠堆叠 |
| A9 | AI 建议忽略持久化 | AI 卡片出现后点击忽略 | 任一 aiPrompt 用例 | 卡片消失；刷新后不再出现（建议 map 删除已持久化） |

实现要点：
- AI 分析有 ~0.8s debounce + mock 延迟；用 `expect(card).toBeVisible({timeout:10s})`，禁止固定长 sleep。
- 每次只放一个缺陷短语，避免 mock 的“最多 5 条”上限与多卡片干扰。
- A3 的表格与正文必须落在**同一轮脏块**：连续输入（间隔小于 debounce）或先表格后立即正文；不要停顿等第一轮分析，否则第二轮快照只有正文，mock 看不到表格明细。
- A8 允许本地卡片先出现；等 AI 卡片到达后再断言重叠范围只剩 AI 卡片，避免把中间态当失败。
- A7 的规则更新用另一页面 UI 保存（管理面保存会广播）；API PUT 本身不广播。广播只重载规则，不会主动分析，需要再输入一个字符触发下一轮。

### 6.3 词表（`quality-vocab.spec.ts`）

| ID | 场景 | 步骤 | 断言 |
| --- | --- | --- | --- |
| V1 | 全局别名替换 | 全局词表 `人工智能|AI`；输入 `AI` | 联想出现“人工智能”；接受后正文为“人工智能” |
| V2 | 文档词表覆盖全局 | 全局 `AI→人工智能`；文档 `AI→智能体` | 本文档联想/替换为“智能体”；另一文档仍为“人工智能” |
| V3 | 导入/导出 | 管理面写入条目 → 导出文件 → 清空 → 导入（追加/覆盖） | 导出内容含条目；导入后词表恢复；去重生效 |
| V4 | 前缀/模糊联想 | 输入 `人工` | 候选包含“人工智能”，Tab 接受 |
| V5 | 隔离 | 文档 A 的词表 | 文档 B 不出现 A 的候选 |
| V6 | 词表命中与规则并存 | 词表别名 + 白名单规则同时存在 | 联想与检查卡片互不干扰，接受顺序不破坏正文 |

实现要点：
- 词表真源是文件；通过现有 `/vocabularies/*` 端点写入，测试结束恢复快照。
- 联想候选的选择器优先用组件 data 属性（见 §7），避免依赖候选文案顺序。

### 6.4 导入文档（`quality-import.spec.ts`）

| ID | 场景 | 断言 |
| --- | --- | --- |
| I1 | 导入的表格单元格型号错误 | 导入 fixture（表格含 `S-5700`）；预置 map；打开后毫秒级出现本地卡片，锚定单元格 |
| I2 | 导入基线不触发全量 AI | 导入后等待状态“已建立导入基线”；改一个段落；只分析 1 个变动块 |
| I3 | 图注规则 | 导入图片+图注 `图注：9800M12`；presence 规则命中（缺 UPS/豁免），补豁免后消失 |
| I4 | 导入失败不留半成品 | 用一个损坏/超限 docx；断言错误提示，列表无新增文档 |

## 7. 可测试性改造（建议）

为减少选择器脆弱性，给 `SuggestionNote.vue` 增加：

```vue
<div class="se-suggest-note"
  :data-source="item.source"
  :data-rule-id="item.ruleId ?? ''"
  :data-mechanism="item.mechanism ?? ''"
  :data-severity="item.severity">
```

- `SuggestionItem` 增加可选 `ruleId`/`mechanism`：本地匹配已带 `ruleId` 与机制 id（`rule:${id}:${mechanism}:...`），AI 卡片可由服务端在 suggestion 里写入 `ruleId`（没有则空）。
- 词表候选增加 `data-vocab-term`，避免按文本顺序取候选。
- 这些都是轻量属性，不改变 UI；测试用 `[data-rule-id="..."]` 精确断言“哪条规则命中”。

## 8. 执行与 CI

- 前置：`npm run dev:all`（Flask 3002 + vite 5173 + SE mock 1234），worker=1。
- 命令：
  ```bash
  npx playwright test e2e/suggest-editor-quality-local.spec.ts \
    e2e/suggest-editor-quality-ai.spec.ts \
    e2e/suggest-editor-quality-vocab.spec.ts \
    e2e/suggest-editor-quality-import.spec.ts --reporter=line
  ```
- 预计新增用例 27–32，运行 6–9 分钟；与现有 13 例合计 <15 分钟。
- 清理：每个用例 finally 恢复全局规则/词表/检查要点快照并删除文档；失败也执行清理，清理失败聚合抛出。
- 稳定性要求：同一套用例连续跑两次全绿；失败重跑不得依赖前一次残留状态。

## 9. 覆盖对照

| 缺陷类别 | 覆盖用例 |
| --- | --- |
| 术语与命名 | L7、L8、V1–V5、I1 |
| 数据与单位 | L8–L10、A3 |
| 结构与完整性 | A4、A5 |
| 逻辑与一致性 | A1、A3、A7 |
| 指代与歧义 | A2 |
| 表达与格式 | L1–L6 |
| 安全与合规 | A5、L12 |
| 表格/图 | L16、A6、I1、I3 |
| 导入/协作 | A7、I2–I4 |
| 机制本身 | L13–L15、A8、V6 |

## 10. 非目标与风险

非目标：
- 真实模型的语义准确率评估（另设人工/夜间烟测）；
- 性能/并发压测（已有 `tests/stress/` 体系）；
- Word 版式保真、页眉页脚等（不在一期范围）。

风险与应对：
- **mock 与真模型偏差**：mock 只验证契约与链路，不验证模型能力；保留真模型烟测。
- **AI 分析时序**：统一用卡片可见性超时，不用固定 sleep；分析状态徽标作为辅助信号。
- **规则/词表状态泄漏**：所有写操作走 API 快照恢复；用例串行。
- **卡片文案变化**：优先 `data-rule-id`/`data-source` 断言，文案只作辅助。
- **表格/导入依赖一期功能**：用例排在 word-parity 之后执行；失败时优先检查一期迁移与附件端点。

## 11. 验收标准

1. §2 的 9 类缺陷每类至少 1 个确定性用例；语义类通过 mock 契约覆盖。
2. 每个用例断言来源、规则/要点名、锚定文本、严重级别与接受/忽略行为中的至少 4 项。
3. 同一套用例连续运行两次全绿，清理后无残留文档/规则/词表。
4. 新增套件与现有智能写作 13 例合计 <15 分钟。
5. 失败时能从 Playwright trace/截图直接定位到“哪条规则、哪张卡片、哪段文本”。

## 12. 实施记录（2026-10-22）

- mock agent（`suggest-editor-server/mock-agent.mjs`）：新增 `SEMANTIC_CASES` 五条语义用例（绝对化/指代/数据一致性/回滚/安全），双条件生效（prompt 含规则名或要点名 **且** 快照含缺陷短语）；通用 5 条写作规则加 `HAS_CHECK_SECTION` 开关——分析 prompt 里没有「检查要点/领域检查规则」段落时不产出任何建议，本地规则用例由此与 AI 层完全隔离。仅影响 `AGENT_KIND=mock`。
- 可测试性改造：`SuggestionNote.vue` 增加 `data-source / data-rule-id / data-mechanism / data-severity / data-quote / data-notice / data-suggestion-id`；`SuggestionItem` 增加可选 `mechanism`（builtin / maps / patterns / whitelists / presence / ai）；词表候选行增加 `data-vocab-term / data-vocab-source`。
- 新增 `e2e/helpers/suggest-editor-quality.ts`：共享登录、全局规则/检查要点/词表的快照-复位-恢复（beforeEach 复位 + afterAll 恢复）、API 建文档/种子、打开文档等同步徽标、卡片定位与属性断言。
- 4 个套件 30 例：local 11（L1–L16，其中 L1–L6 合并一例）、ai 9（A1–A9）、vocab 6（V1–V6）、import 4（I1–I4）+ `e2e/manual/suggest-editor-real-model-quality.mjs` 真模型烟测（5 类缺陷可选，不进 CI）。
- 与设计的偏差（均有注释说明）：
  - L5 的半角逗号输入用 `设备甲,设备乙`（设计里的 `设备A,设备B` 两侧不是汉字，规则按定义不命中）；
  - A3 的表格经种子灌入并建立导入基线，改动正文段落触发分析——mock 的 reason 固定引用“明细 12 台”，快照里不重复放表格内容也能稳定验证锚定与 reason；
  - A7 的「规则徽标数量更新」用“全局规则从 0 条 → B 页新建 1 条 AI 规则”实现（给既有规则补 aiPrompt 不会改变计数）；
  - L14 的重新启用用 API PUT + 与 RuleManagerDialog 保存后完全一致的 BroadcastChannel 消息触发重载。
- 验证：新 30 例连续两轮全绿（每轮 2.5 分钟）；既有智能写作 13 例回归全绿；`vitest` suggest-editor 32 例、`vue-tsc` 通过。
- 环境注意：本机 proxy 栈常驻 `AGENT_KIND=opencode` 的 SE 服务时，AI 用例会卡在「正在分析」——质量 E2E 必须 mock 栈（`dev:all` 或 `AGENT_KIND=mock` 单独拉起 SE）。
