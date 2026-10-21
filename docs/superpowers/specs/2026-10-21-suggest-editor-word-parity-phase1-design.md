# 智能写作一期：表格 / 图片 / 排版 / Word 导入导出 设计

- 日期：2026-10-21
- 状态：待评审
- 范围：智能写作（suggest-editor）一期“最小可用”的 Word 对齐能力
- 相关代码：`src/components/suggest-editor/`、`src/views/suggest/`、`server/routes/suggest_editor.py`、`server/utils/suggest_editor_repo.py`、`suggest-editor-server/src/{flatten,engine}.ts`

## 1. 背景与目标

当前编辑器只支持标题、段落、列表、引用、代码块、链接和基础行内格式；没有表格、图片、字体/颜色/对齐，也不能导入或导出 Word。技术方案文档中大量存在参数表、拓扑图、图注和版式要求，导致 Word 文档无法迁入，交付仍需回 Word 排版。

一期目标（结构化写作 + Word 作为交付格式）：

1. **表格**：插入、编辑、增删行列、合并/拆分、表头、列宽。
2. **图片**：上传、粘贴/拖拽、图注、属主鉴权访问。
3. **排版**：对齐、字号、文字颜色、高亮。
4. **Word 导入**：`.docx` → 新建文档（结构化保真）。
5. **Word 导出**：当前文档 → `.docx`（结构化保真）。
6. **AI 检查不退化**：本地规则覆盖表格单元格与图注文本；AI 分析适配表格序列化与导入后的分析预算。

### 1.1 已确认的产品决策

| 决策 | 结论 |
| --- | --- |
| Word 导入入口 | 只在列表页“新建文档时导入”；不支持向已有文档追加/替换 |
| 图片存储 | 服务端文件 + 鉴权 URL；不把 base64 写进 Yjs |
| 导入/导出保真 | 结构化保真（标题/段落/列表/表格/图片/基础 marks）；不保证字体、颜色、页眉页脚、目录、分页 |
| 导入后 AI 检查 | 不自动全文分析；首次分析只建立基线，之后只分析用户改动的块 |

### 1.2 非目标（本期不做）

- 页眉页脚、页码、目录、分页/分节、多栏、封面
- 公式、脚注/尾注、题注交叉引用、自动多级编号
- 修订模式、人工批注、版本历史快照
- `.doc`（Word 97-2003）、加密/受保护文档、PDF 导出
- Word 视觉版式还原（字体族/字号/颜色/缩进等以 mammoth 能语义映射的为准）
- 图片内容 AI 检查（只检查图注文本）；格式类规则（“标题必须加粗”）不在检查范围
- 向已有文档导入、导入时合并/追加
- 图片拖拽缩放、裁剪、对齐浮排（MVP 宽度固定最大 100%）

## 2. 技术路线

采用 **前端解析/生成 + 服务端附件与种子内容**：

- 浏览器用 `mammoth` 解析 `.docx`，用 `docx` 生成导出文件；两者懒加载，不进首屏包。
- Flask 只负责：
  - 图片附件上传/读取/属主鉴权/删除清理；
  - 导入种子 HTML 的暂存与一次性消费；
  - 文档删除时级联清理。
- 不新增 Python 运行时依赖，不引入 LibreOffice/pandoc。

被否决的方案：

- **服务端 mammoth/python-docx**：mammoth 无 Python 版；python-docx 的导入映射工作量显著更大，且新增部署依赖。
- **LibreOffice/pandoc 转换**：依赖重、字体环境不可控，且仍达不到 Word 分页还原，超出本期目标。

## 3. 依赖与数据模型

### 3.1 前端依赖

| 包 | 用途 |
| --- | --- |
| `@tiptap/extension-table` | TableKit：table/row/cell/header、合并、列宽 |
| `@tiptap/extension-image` | 图片节点（作为 figure 的子节点） |
| `@tiptap/extension-text-style` | TextStyle 基础 mark，承载 Color/FontSize |
| `@tiptap/extension-color` | 文字颜色 |
| `@tiptap/extension-highlight` | 多色高亮 |
| `@tiptap/extension-text-align` | 段落/标题对齐 |
| `mammoth` | docx → HTML（懒加载） |
| `docx` | Tiptap JSON → docx（懒加载） |

说明：字号通过 TextStyle 的 `fontSize` 属性实现（Tiptap v3 若提供 FontSize 扩展则直接使用，否则在 `core/formatting.ts` 内自定义 mark）。项目 npm 安装沿用 `.npmrc-local`（本地代理端口 1000）。

### 3.2 数据库迁移

新增 `server/migrations/2026-10-21-suggest-editor-attachments.sql`，并按项目惯例在 `server/app.py` 迁移注册表**双注册**：

```sql
CREATE TABLE IF NOT EXISTS se_attachments (
  id            VARCHAR(64) PRIMARY KEY,
  document_id   VARCHAR(64) NOT NULL,
  owner_user_id VARCHAR(64) NOT NULL,
  filename      VARCHAR(255) NOT NULL,
  mime          VARCHAR(100) NOT NULL,
  size          BIGINT NOT NULL,
  stored_path   TEXT NOT NULL,
  created_at    TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_se_attachments_document ON se_attachments(document_id);

ALTER TABLE se_documents ADD COLUMN IF NOT EXISTS seed_content_html TEXT;
```

- `seed_content_html`：导入文档的初始正文，编辑器应用后清空；与 `template_id` 互斥（导入文档不带模板）。
- 不改 `se_document_states`；Yjs 状态仍由 Node 服务写入。

## 4. 编辑器节点与工具栏

### 4.1 表格

- 使用 TableKit，开启 `resizable`；表头行由 `tableHeader` 节点表达。
- 斜杠菜单新增「表格」：插入 3×3、首行为表头。
- 工具栏在选区位于表格内时显示表格控件：上方/下方插入行、删除行、左侧/右侧插入列、删除列、合并单元格、拆分单元格、切换表头行、删除表格。
- 表格样式沿用现有纸张风格：细边框、表头浅灰底、单元格内边距；不引入彩色主题。
- 合并单元格使用 ProseMirror 的 colspan/rowspan；导出时映射，flatten 按单元格文本顺序序列化（不重建合并结构）。

### 4.2 图片与图注

- 新增 `figure` 块节点，内容表达式为 `image figureCaption`：
  - `image`：Tiptap Image（`inline: false`、`allowBase64: false`），attrs 含 `src`、`alt`、`title`；
  - `figureCaption`：块级图注，`content: inline*`，渲染为 `<figcaption>`，允许空内容；
  - `figure` 渲染为 `<figure><img/><figcaption/></figure>`，图注可直接在正文中编辑。
- **文档中只存 canonical URL**：`/suggest-editor/attachments/<attachment_id>`（Flask 路由，不带 `/api` 前缀）。
- 图片 NodeView 在渲染时从 localStorage 读取当前 JWT，拼接为 `/api/suggest-editor/attachments/<id>?access_token=<jwt>` 作为实际 `src`（`/api` 由 vite/proxy 转发），避免 token 过期后写死在文档里。重新登录后刷新页面即可。导出时用 `Authorization` 头拉取 canonical URL 对应的 `/api/...` 地址。
- 入口：工具栏「插入图片」打开文件选择；粘贴/拖拽图片先上传再插入 figure。
- 图片宽度：CSS `max-width: 100%`，不提供拖拽缩放。
- 图注输入框 placeholder：「图注（可选）」。

### 4.3 排版

- 对齐：`@tiptap/extension-text-align`，支持 paragraph/heading 左/中/右/两端对齐。
- 字号：12/14/16/18/20/24 pt + 「默认」（移除字号 mark）。
- 颜色：预设 8 色 + 清除。
- 高亮：预设 6 色 + 清除。
- 「清除格式」同时移除 marks 与对齐/字号/颜色/高亮。
- 工具栏保持现有固定顶部布局；新增控件分组不改变纸张居中与宽度断言。

## 5. 附件服务

### 5.1 上传

`POST /suggest-editor/documents/<doc_id>/attachments`

- 鉴权：登录 + 文档属主（`_get_own_document`）；非属主/不存在统一 404（与现有文档语义一致，admin 不旁路）。
- Body：multipart，字段名 `file`。
- 允许类型：png / jpeg / gif / webp；扩展名与 magic bytes 双重校验。
- 单文件上限 10 MB。
- 存储：`SE_DATA_DIR/attachments/<doc_id>/<attachment_id>.<ext>`；写入前用与 `_vocab_safe_path` 相同的 realpath 包含校验。
- 响应 `201`：

```json
{ "attachment": { "id": "att_xxx", "url": "/suggest-editor/attachments/att_xxx",
                  "filename": "topology.png", "mime": "image/png", "size": 12345 } }
```

- 失败：400（类型/大小/空文件）、404（非属主）、500（磁盘写入失败）。

### 5.2 读取

`GET /suggest-editor/attachments/<attachment_id>`

- `@login_required_sse`：支持 `Authorization` 头与 `?access_token=`（与现有导出下载一致）。
- 校验附件存在且所属文档的 owner == 当前用户；非属主 404。
- 响应 `Content-Type` 为记录的 mime，`Content-Disposition: inline`，`Cache-Control: private, max-age=31536000, immutable`。

### 5.3 清理

- 删除文档时：删除该文档的 `se_attachments` 行 + `rmtree(SE_DATA_DIR/attachments/<doc_id>)`（路径包含校验），再删文档行与 Yjs 状态（复用现有顺序）。
- 从正文删除图片**不立即删除附件**（避免撤销/协作误删）；孤儿文件随文档删除回收。
- 导入失败回滚：客户端调用删除文档接口，级联清理已上传图片。

## 6. Word 导入

### 6.1 UI 与限制

- 列表页 header 新增「导入 Word」按钮（与「新建文档」并列）。
- 只接受 `.docx`；`.doc`、加密/损坏文件、超过 20 MB 直接拒绝并提示。
- 其他限制：图片 ≤ 200 张；提取文本 ≤ 2 MB 或块数 ≤ 2000，超限拒绝（避免首轮加载与 DB 过大）。
- 导入过程弹窗显示阶段：解析 → 上传图片 N/M → 创建文档；失败显示具体原因，且不留下半成品文档。

### 6.2 流程

1. 读取 ArrayBuffer，`mammoth.convertToHtml({ arrayBuffer })`，使用 `convertImage` 拦截图片为 Blob 占位符。
2. 清洗 HTML：DOMParser + 允许标签/属性白名单；去掉 script/style/on* 属性；允许 http/https/mailto 链接。
3. 映射：
   - `<img>` → `figure`（image + 空 figureCaption；alt 取原 alt），占位符稍后替换为附件 URL；
   - `<table>` → TableKit 节点；嵌套表格降级为段落并记 warning；
   - 标题/段落/列表/引用/代码块直接映射；其余不支持的标签降级为段落。
4. 创建空文档（标题 = 文件名去扩展名，最长 200 字符；空则「导入文档」，不带 templateId）。
5. 逐张上传图片（并发 3），拿到 URL 后替换占位符。
6. `PUT /suggest-editor/documents/<id>/seed` 保存最终 HTML。
7. 跳转编辑页。
8. 任一步失败：调用删除文档接口回滚，提示失败阶段与原因。

### 6.3 种子内容接口

`PUT /suggest-editor/documents/<doc_id>/seed`

- 属主校验；body `{ "contentHtml": "<...>" }`，上限 5 MB。
- 仅当 `seed_content_html IS NULL` 且该文档没有 `se_document_states` 行时允许写入；否则 409（避免覆盖已开始编辑的文档）。
- 响应 `{ "ok": true }`。

`POST /suggest-editor/documents/<doc_id>/seed/applied`

- 属主校验；将 `seed_content_html` 置 NULL；幂等。

`GET /suggest-editor/documents/<doc_id>`

- 属主响应中增加 `document.seedContentHtml`（无种子时为 null）；文档列表 `GET /documents` 不返回该字段，避免大 HTML 进列表。

### 6.4 客户端应用种子

`SuggestEditorDocView` 在 `synced` 后：

1. 若正文为空且文档返回 `seedContentHtml` 非空：`editor.commands.setContent(html)`（单次事务）。
2. 调用 `seed/applied` 清空服务端种子。
3. 在 Y.Doc 的 `meta` map 设置 `skipInitialAnalysis = true`（见 8.3）。
4. 若正文非空（说明已应用/用户已编辑），忽略种子内容，但仍调用 `seed/applied` 清空，避免陈旧 HTML 残留。
5. `templateId` 与 seed 同时存在时 seed 优先；正常导入不会设置 templateId。

### 6.5 导入提示

- mammoth 的 messages（不支持的样式/元素）与自定义 warning（嵌套表格降级、图片失败）在导入完成后用可滚动弹窗汇总，最多展示前 20 条。
- 部分图片上传失败不阻断导入：该图片位置降级为普通段落「[图片上传失败：原文件名]」，并在提示中列出（不生成缺 src 的 figure，避免 schema 非法）。

## 7. Word 导出

### 7.1 UI

- 编辑页顶栏新增「导出 Word」按钮；空文档禁用。
- 导出中按钮 loading；完成后浏览器下载 `标题-YYYYMMDD.docx`。

### 7.2 映射

以当前编辑器 JSON 为准（不是数据库快照）：

| Tiptap 节点/标记 | docx 输出 |
| --- | --- |
| paragraph / heading 1-6 | Paragraph / HeadingLevel；保留对齐 |
| bold / italic / underline / strike | 对应 run 属性 |
| color / highlight / fontSize | run 的 color / shading / size（pt×2） |
| inline code | 等宽字体 + 浅灰底 |
| link | ExternalHyperlink；非法 URL 降级为文本 |
| bulletList / orderedList | bullet / numbering，嵌套按 level |
| taskList | bullet + ☐/☑ 前缀（MVP） |
| blockquote | 缩进段落 |
| codeBlock | 等宽 + 浅灰底段落，保留换行 |
| horizontalRule | 带下边框的空段落 |
| table | Table/TableRow/TableCell，表头加粗；colspan/rowspan 按 attrs 映射 |
| figure | ImageRun 段落（宽度按图片原始尺寸，上限 600px）+ 居中斜体图注段落 |
| 图片取数 | 对 canonical URL 拼 `/api` 前缀后，用 `Authorization: Bearer` fetch；失败插入「[图片缺失: alt]」并 warning |

- 页面：A4、默认页边距；单 section。
- 不导出：建议/批注 decoration、检查标记、词表提示、快捷填充占位符高亮。
- 文件名清洗：去掉 `\/:*?"<>|`，空标题回退「未命名文档」。

### 7.3 实现边界

- 纯前端生成，不新增后端导出端点。
- 导出失败（图片拉取、生成异常）显示错误弹窗；不产生半截下载文件。
- 不包含页眉页脚、页码、目录、分页符。

## 8. AI 检查适配

### 8.1 flatten 表格

`suggest-editor-server/src/flatten.ts` 新增 `table` 分支：

- 每个 `tableRow` 输出一行：`| cell1 | cell2 |`；首行为 `tableHeader` 时追加 `| --- | --- |`。
- 单元格内多段落用空格连接；`hardBreak` 视为空格；合并单元格按实际存在的 cell 文本输出（不补空格子）。
- 仍然通过 `appendYText` 记录每个单元格文本的 segment 区间，保证建议引文可锚定到单元格内部。
- 整张表是一个顶层 block（hash 覆盖全表）；表格无任何文本时不产生可分析块。

### 8.2 flatten 图注

- `figure` 分支：输出 alt（非空时）与 `figureCaption` 文本；图片节点本身不产生文本。
- 图注文本照常进入 block 文本与 hash；图片内容不送模型。

### 8.3 导入后的分析基线

- Y.Doc 新增 `meta` map（`DOC_META_MAP = "meta"`），键 `skipInitialAnalysis`。
- `engine.analyze` 在计算 dirty 前检查：
  - 若为 `true`：对当前所有非空 block 执行 `rememberHash`，删除该标记，发布状态「已建立导入基线，后续只分析改动段落」，**本轮不调用模型**。
  - 若为 `false`/不存在：走现有逻辑。
- 标记随 Yjs 状态持久化；若用户在首次分析前关闭页面，下次打开仍会先建立基线。
- 已知竞态：应用种子后 800ms 内用户立即编辑的块会被计入基线、本轮不分析；下一次编辑即可命中。可接受。

### 8.4 分析预算

- `MAX_ANALYZED_HASHES` 从 500 提升到 5000（约 80 KB/doc 的 hash 数组），避免大文档 hash 被驱逐导致重复分析。
- 新增 `MAX_DIRTY_BLOCKS_PER_RUN = 40`：
  - dirty 过滤后按文档顺序截取前 40 个；
  - 本轮结束后若仍有未分析块，延迟 2 秒自动触发下一轮（用户新编辑会重置计时），直到清空；
  - 状态 hint 显示「分析 40 个变动段落（剩余 N 个）」。
- 提示词措辞从固定「段落」调整为「未分析块」，并明确表格以 Markdown 表格呈现。

### 8.5 锚定与提示词约束

- AI 引文必须落在单个 `Y.XmlText` 内：单元格内短句、图注短句可以锚定；跨单元格/整行/跨图注的引文会被丢弃。
- `buildPrompt` 增加约束：
  - 表格内容按行/单元格理解；
  - 引用只取单元格内或图注内的连续短句，不要引用整行/跨单元格；
  - 不要对图片内容本身提建议，只对图注文字提建议。
- 本地规则：
  - 单元格文本、图注文本都是普通文本节点，maps/patterns/whitelists/presence 自动覆盖；
  - whitelist 的锚点匹配以单个文本节点为界，锚点与值跨单元格时不会命中（符合预期，文档需在说明中写明）。

## 9. 权限与安全

- 所有附件与种子端点严格属主私有；非属主/不存在统一 404；admin 不旁路。
- 上传：登录 + 属主 + 类型/大小/空文件校验；存储路径由 UUID 生成并做 realpath 包含校验。
- 读取：`login_required_sse`（支持 `?access_token=`）；`Cache-Control: private`。
- 导入 HTML：标签/属性白名单清洗，移除 script/style/on*；链接协议白名单。
- 种子 HTML 上限 5 MB；仅可写一次，避免覆盖已编辑文档。
- 导出完全在浏览器内完成，不上传正文。
- 日志：上传/读取/删除失败记录 warning（含 doc_id/attachment_id，不记录 token）；导入失败原因由前端提示。

## 10. 测试策略

### 10.1 前端单测（vitest）

- `core/__tests__/formatting.test.ts`：字号/颜色/高亮/对齐的 set/unset；清除格式移除全部 marks 与 attrs。
- `core/__tests__/docx-import.test.ts`：
  - mammoth HTML 清洗（script/on* 被移除）；
  - `<img>` → figure + 图注占位；
  - `<table>` → TableKit 节点；
  - 嵌套表格降级 warning；
  - 标题/列表/链接映射。
- `core/__tests__/docx-export.test.ts`：
  - 将包含标题/表格/图片/figure/基础 marks 的 JSON 生成为 docx，再用 mammoth 回读，断言文本、表格、图片存在；
  - 图片 fetch 失败时输出「[图片缺失]」而非整体失败。
- 现有 `check-rules.test.ts`、`suggestEditor.test.ts` 回归；新增 attachment/seed API 形参断言。

### 10.2 Node 单测（suggest-editor-server）

- 新增 `suggest-editor-server/tests/flatten.test.ts`，用 `tsx --test` 运行（新增 npm script）：
  - 构造含 table/figure 的 Y.XmlFragment，断言 Markdown 表格、图注文本、segment 偏移；
  - 修改单元格文本后 hash 变化、整表 block 数量为 1；
  - figure 无文本时不产生可分析块。

### 10.3 后端 pytest

- `test_suggest_editor_attachments.py`：
  - 上传成功（返回 URL/大小）、非属主 404、未登录 401；
  - 类型伪造（.png 扩展名但非图片 magic）400；>10MB 400；
  - 读取：属主 200 + 正确 mime，非属主 404，`?access_token=` 可用；
  - 删除文档后附件行与磁盘目录清空。
- `test_suggest_editor_seed.py`：
  - PUT seed 属主门；无 state 时可写；
  - 已有 state 或 seed 已存在 → 409；
  - applied 清空且幂等；
  - >5MB 400。

### 10.4 Playwright E2E

新增 `e2e/suggest-editor-word-parity.spec.ts`（复用现有登录/建文档 helper）：

1. **表格 + 本地规则**：斜杠插入表格；在单元格输入「设备类型：9800M12；5000M12」；预置白名单规则；断言单元格内实时出卡片；刷新后表格与内容恢复。
2. **图片 + 图注**：工具栏上传 1×1 PNG；填写图注；断言图片加载（naturalWidth > 0）；刷新恢复；图注中的本地规则命中。非属主访问附件的 404 由 pytest 覆盖（避免 E2E 依赖多账号创建）。
3. **排版**：选中文本设置字号/颜色/高亮/居中；刷新后保持；清除格式后恢复默认。
4. **Word 导入**：测试内用 `docx` 生成 fixture（标题 + 列表 + 2×2 表格 + 一张 PNG）→ 导入 → 断言标题/表格/图片/图注占位；首次编辑后状态 hint 为分析 1 个变动段落（mock agent），证明未全文分析。
5. **Word 导出**：下载导出的 docx，用 mammoth 解析，断言标题/表格/图片存在；断言建议文本未出现在导出文件中。
6. **清理**：每个用例结束删除文档与临时附件；全局规则按快照恢复。

现有 `suggest-editor.spec.ts`、`suggest-editor-layout.spec.ts`、`suggest-editor-rules.spec.ts`、`suggest-editor-snippets.spec.ts` 全量回归（dev mock 栈，worker=1）。

### 10.5 手工验证

- 用 Word 打开导出的 docx（Windows 本机）检查表格/图片/标题/图注可读。
- 导入一份真实技术方案（含合并单元格、图片、多级列表）记录 mammoth warning，确认无崩溃。

## 11. 验收标准

1. 表格：插入/编辑/增删行列/合并拆分/表头/列宽可用，刷新与多人协作恢复；单元格文本能被本地规则实时命中。
2. 图片：工具栏/粘贴/拖拽上传可用；图注可编辑；刷新恢复；非属主访问附件返回 404；删除文档后磁盘与 DB 无残留。
3. 排版：对齐/字号/颜色/高亮/清除格式可用并持久化。
4. 导入：含标题/列表/表格/图片的 `.docx` 可导入为新文档；图片可显示；失败不留下半成品文档；导入后首次分析不调用模型（只建立基线）。
5. 导出：生成 `.docx` 可被 Word/LibreOffice 打开，标题/表格/图片/图注/基础 marks 正确；建议与检查标记不导出。
6. 边界：`.doc`、损坏文件、>20MB、>200 图、>2000 块被拒绝并给出明确原因；单图 >10MB 拒绝。
7. 回归：现有 suggest-editor 全套 pytest/vitest/E2E 通过。
8. AI：表格走 Markdown 序列化；引文在单元格/图注内可锚定；跨单元格引文丢弃不报错；单轮分析块数上限 40；hash 上限 5000。

## 12. 风险与取舍

| 风险 | 应对 |
| --- | --- |
| 用户把“导入/导出”理解为 Word 版式还原 | 导入完成弹窗与用户文档明确“结构化保真”；不承诺字体/页眉/分页 |
| 表格整块 hash 导致大表每次改动全表重分析 | MVP 接受；`MAX_DIRTY_BLOCKS_PER_RUN` 限制单轮；后续可做行级 hash |
| 跨单元格引文无法锚定 | prompt 约束 + 明确边界；建议卡片只落在单元格内 |
| 20MB docx 在浏览器解析占内存 | 懒加载 + 大小限制 + 失败提示；必要时后续改服务端解析 |
| `docx` 打包体积 | 动态 import，仅导出时加载 |
| 旧前端缓存打开含表格/图片的新文档 | 发布必须前后端一起部署并强制刷新；发布说明写明 Ctrl+F5，避免旧 schema 丢弃新节点 |
| 附件 token 过期 | NodeView 每次渲染拼当前 token；重新登录后刷新页面 |
| MAX_ANALYZED_HASHES 提升导致状态体积增长 | 上限 5000，约 80 KB/文档；随文档删除清理 |

## 13. 发布与回滚

- 迁移随 Flask 启动执行（`app.py` 注册）；Node 服务需重启以加载 flatten/engine 改动。
- 前端需 `npm run build`；proxy 直接服务 `dist/`。
- 回滚：前端回退 dist、后端回退代码；新表与 `seed_content_html` 列可保留（旧代码不读，无副作用）。已导入文档的表格/图片在旧前端中可能被丢弃——回滚前需确认无此类文档或先导出。
