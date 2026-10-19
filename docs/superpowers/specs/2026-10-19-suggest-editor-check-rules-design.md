# 智能写作 · 检查规则（Check Rules）设计

日期：2026-10-19
状态：待用户审阅

## 1. 背景与目标

技术人员写的方案文档存在领域规范类问题：设备类型不规范、单板名称错误、设备配套/拖带声明缺失等。
此前用 skill 对**完稿**做检查；现在要在**编辑阶段**实时检查。

现有能力（已上线）：

- 本地硬编码通用规则（连续空格等），毫秒级；
- AI 停笔分析（检查要点清单注入 prompt，只分析变动段落，块级缓存）；
- 「检查要点」管理面（自由文本清单，全局）。

本设计新增**可配置的领域检查规则库**，双通道执行：

- **本地通道**（浏览器，毫秒级，零 token）：错误映射 / 格式提取校验 / 锚定白名单 / 文档级条件完整性；
- **AI 通道**（opencode，停笔后 10–40s）：规则的自然语言说明注入分析 prompt，做语义核对（措辞变体兜底）。

## 2. 范围

**In scope**：规则模型（4 种本地判定 + AI 说明）；全局与文档级规则；管理面表格 + CSV/TXT 导入导出；
本地执行与建议卡片融合；prompt 注入；测试与文档。

**Out of scope**：多规则集按文档类型套用；Excel 直接导入；知识图谱/关系推理；跨文档检查；规则版本化。

## 3. 规则模型

规则文件为 `{ "rules": CheckRule[] }`：

```ts
interface CheckRule {
  id: string                     // 稳定 id（新建生成 rule-<uuid8>；导入按内容去重）
  name: string                   // 命中时建议 reason 前缀 [name]
  enabled: boolean
  severity: 'info' | 'warn' | 'error'
  aiPrompt?: string              // 可选：注入 AI 分析的规则说明
  // 本地判定（可同时存在多类）
  maps?: { wrong: string; right: string; caseSensitive?: boolean }[]
  patterns?: { extract: string; valid: string; flags?: string; message: string }[]
  whitelists?: { anchors: string[]; values: string[]; valuePattern?: string; message: string }[]
  presence?: { triggers: string[]; requiresAny: string[]; message: string }[]
}
```

各机制语义：

| 机制 | 判定 | 命中后的建议 |
| --- | --- | --- |
| `maps` | 文本中出现 wrong（默认大小写不敏感） | 替换型建议（right），可接受 |
| `patterns` | 用 `extract` 全文提取候选 token，`valid` 校验；候选不合法即命中 | 完整性提醒（notice），不可接受 |
| `whitelists` | 找 `anchors` 任一出现处，取其后的值（`valuePattern`，默认 `[A-Za-z0-9_\-./]+`），不在 `values` 内即命中 | notice |
| `presence` | 全文出现 `triggers` 任一，且 `requiresAny` **一个都未出现** → 命中（文档级条件完整性；requiresAny 可含豁免声明词） | notice，锚定在首个触发词位置 |

示例（用户真实场景）：

```jsonc
{
  "id": "rule-9800ups-drag",
  "name": "9800UPS 拖带完整性",
  "enabled": true,
  "severity": "warn",
  "presence": [{
    "triggers": ["9800M12"],
    "requiresAny": ["9800UPS", "不涉及拖带", "无需拖带", "不拖带"],
    "message": "方案涉及 9800M12，但未提及 9800UPS（可拖带 9800M12），也未标识「不涉及拖带」"
  }],
  "aiPrompt": "检查设备配套与拖带声明：涉及 9800M12 时须写明 9800UPS 或明确不涉及拖带。"
}
```

校验（保存与导入时逐条执行，任何一条失败整批拒绝）：

- `id` 全局唯一；`name` 非空；`severity` 枚举内；
- 所有正则（extract/valid/valuePattern/?）必须可编译（`re.compile` 校验；Node 侧无需校验——本地在浏览器编译）；
- 长度上限：单条 message/aiPrompt ≤ 500 字符；每类数组 ≤ 200 项；整文件 ≤ 2 MB。

## 4. 存储与生效

真源为文件（与词表/检查要点同模式，Node 直读、Flask 管理面原子读写）：

- 全局：`.data/规则-全局.json` —— 首次 GET 播种示例规则（含上述 9800 示例）；
- 文档级：`.data/ws/<docId>/规则.json` —— 仅对该文档生效，**叠加**在全局规则之上（同名 id 文档级覆盖全局）。

前端打开文档时经 REST 拉取全局 + 文档规则 → 合并（文档级优先）→ 编译执行；
Node 每轮分析前现读两份 → 注入 prompt。保存规则后前端立即重新拉取（不必等下一次编辑）。

## 5. 端点（Flask，`routes/suggest_editor.py`）

全局（`admin.suggest_editor` 门）：

- `GET /suggest-editor/rules/global` → `{rules: [...]}`（缺文件时播种示例并返回）
- `PUT /suggest-editor/rules/global` ← `{rules: [...]}`（整份替换，校验后原子写）
- `POST /suggest-editor/rules/global/import`（multipart：`file`, `mode=append|replace`）
- `GET /suggest-editor/rules/global/export`（`?access_token=`，admin）

文档级（`login_required`/`write_required` + 属主门，admin 无旁路，与文档一致）：

- `GET|PUT /suggest-editor/documents/<id>/rules`
- `POST .../rules/import`、`GET .../rules/export`

导入格式（CSV，UTF-8；Excel 另存 CSV 即可）：

```csv
type,name,severity,enabled,wrong,right,extract,valid,anchors,values,triggers,requiresAny,message,aiPrompt
map,设备型号纠错,error,true,S-5700,S5700,,,,,,,,,
presence,拖带完整性,warn,true,,,,,,,9800M12,9800UPS|不涉及拖带,方案涉及 9800M12 但未提及 9800UPS 也未标识不涉及拖带,涉及 9800M12 时须写明 9800UPS 或明确不涉及拖带
```

- 一行一条规则（单机制）；多值列用 `|` 分隔；空列忽略；
- `type ∈ {map, pattern, whitelist, presence, ai}`（`ai` = 仅 aiPrompt 的规则）；
- 追加模式按 (type + 关键字段拼接) 去重；`replace` 整份替换；
- TXT 简版：一行 `错误写法=>正确写法` → 生成 map 规则（导出始终 CSV）；
- 导入失败（坏行/坏正则/超限）整批拒绝并返回**行号与原因**，不做部分成功。

## 6. 本地执行（前端）

- 拉取与编译：`core/check-rules.ts`（新）——拉取（REST）、合并、编译为匹配器；
- 接入现有本地规则管线（`core/local-rules.ts` 扩展为接受动态规则）：
  - 每次编辑（现有 decoration 管线）全量重算：maps 合并为按规则分组的 alternation 正则（一次扫描）；
  - patterns/whitelists 在单文本节点内匹配（与现有 local 规则同约束）；
  - presence 基于**全文纯文本**做确定性 contains（大小写不敏感；全文 > 500k 字符跳过并留一次 console.warn）；
- `LocalMatch` 扩展：`ruleId`、`ruleName`、`notice: boolean`；
- 建议卡片：`notice=true` 隐藏「接受」按钮，仅「忽略」（忽略按 `(ruleId, quote)` 记忆至下次文档加载）；reason 前缀 `[规则名]`；
- 与 AI 建议的范围重叠去重逻辑沿用（本地让位 AI）；
- 规则对话框保存成功后：重新拉取 + 重算本地规则（立即生效）。

## 7. AI 通道（Node）

- `engine` 每轮分析前读全局 + 文档规则文件（坏 JSON 跳过并 `[rules]` 日志，不阻断分析）；
- 注入格式（与检查要点并列，规则在前，各自有标题）：

```
领域检查规则（逐条核对，命中在 reason 中注明规则名）：
- [9800UPS 拖带完整性] 检查设备配套与拖带声明：涉及 9800M12 时须写明 9800UPS 或明确不涉及拖带。
检查要点（逐条核对，命中才提；reason 里注明命中的要点名）：
- …（现有检查要点，保持独立）
```

- 上限：注入规则 ≤ 40 条（超出截断并日志）；
- 「检查要点」与「检查规则」并存、分工不变（前者通用写作质量，后者领域约束）。

## 8. 管理 UI

- 列表页新增「**检查规则**」按钮（`admin.suggest_editor`）→ `RuleManagerDialog`（作用域=全局）；
- 编辑页顶栏新增「**规则**」按钮（属主）→ 同一组件（作用域=本文档，标题注明"仅本文档生效"）；
- 表格列：名称 / 严重度 / 启用 / 类型摘要（映射 N 条·正则 N·白名单 N·完整性 N·AI）/ 操作（编辑、删除）；
- 顶部操作：新建规则、导入（追加/覆盖，CSV/TXT）、导出、下载 CSV 模板；
- 编辑表单：名称、严重度、启用、AI 说明 + 按类型动态编辑区：
  - 映射：行编辑器（wrong → right，大小写开关）；
  - 正则：extract / valid / message；
  - 白名单：锚点列表、值列表（多行文本，一行一个）、valuePattern（可选）；
  - 完整性：触发词列表、必须出现其一列表（含豁免词）、message；
- 表单内联校验（正则可编译、必填项），保存 = PUT 整份 JSON。

## 9. 错误处理

- 规则文件坏 JSON：Flask GET 返回 500 与明确错误（管理面提示修文件/覆盖导入）；Node/前端跳过并告警，不阻断编辑与分析；
- 单条正则编译失败：保存/导入时拒绝（定位到条）；前端编译时 try/catch 跳过该条并 console.warn；
- 导入整批原子（见 §5）。

## 10. 测试

- **pytest**：全局/文档级 CRUD、权限门（admin / 属主 / 非属主 404）、导入导出、坏规则与坏正则拒绝、默认示例播种、路径安全闸复用。
- **vitest**：`core/check-rules` 编译与匹配纯函数——maps（大小写/重叠）、patterns（提取+校验）、whitelists（锚定边界/valuePattern）、presence（触发/豁免/消失）、notice 标记。
- **E2E**（`e2e/suggest-editor-rules.spec.ts`，mock 栈）：
  1. 管理面建映射规则 → 编辑器输入错误写法 → 毫秒级本地卡片出现 → 接受后替换生效；
  2. 管理面建 presence 规则（9800 场景）→ 新建文档写 9800M12 → notice 出现 → 补写「不涉及拖带」→ notice 消失；
  3. 文档级规则只对本文档生效（另一文档不出现）；
  4. 既有布局/主链路用例保持绿。
- **AI 通道**：真模型探针（`suggest-editor-real-agent-check.mjs` 扩展或新增）验证 prompt 注入且 reason 标注规则名（手工，非 CI）。

## 11. 已知识别边界（v1 接受）

- 本地匹配限于单个文本节点（跨加粗/普通样式切分的词会漏）；
- presence 只做字面匹配（同义词/措辞变体靠 requiresAny 词表维护；AI 仅作非确定性兜底）；
- 锚定白名单的值默认取 ASCII 串（中文型号需配置 valuePattern）；
- notice 不可"接受"，只能忽略；
- 全文 > 500k 字符跳过 presence 扫描。

## 12. 验收标准

1. 管理面可维护四类规则 + CSV 导入导出，非法输入被拒且定位到行/条；
2. 映射类规则输入即出卡片（毫秒级），接受后替换生效；
3. 9800M12 场景按描述出 notice，补豁免词后自动消失；
4. 文档级规则不影响其他文档；全局规则对所有文档生效；
5. AI 通道真模型分析时 reason 能标注规则名；
6. pytest / vitest / E2E 全绿。
