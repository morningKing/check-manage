# Script Upload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add script file upload functionality to ExportScriptManager and ValidationScriptManager pages, allowing developers to upload .py files and inject content into the editor.

**Architecture:** Pure frontend implementation using FileReader API to read .py file content, fill into CodeMirror editor, then save via existing API. No backend changes required.

**Tech Stack:** Vue 3, TypeScript, Element Plus, FileReader API

---

### Task 1: Add Upload Button to ExportScriptManager

**Files:**
- Modify: `src/views/admin/ExportScriptManager.vue`

- [ ] **Step 1: Add hidden file input element and upload button**

Add after the existing "新增" button in the card-header section (around line 10-13):

```vue
<el-button type="success" size="small" @click="triggerUpload">
  <el-icon><Upload /></el-icon>
  上传脚本
</el-button>
<input
  ref="fileInputRef"
  type="file"
  accept=".py"
  style="display: none"
  @change="handleFileUpload"
/>
```

- [ ] **Step 2: Import Upload icon**

Add `Upload` to the icon imports (around line 483):

```typescript
import { Plus, Upload } from '@element-plus/icons-vue'
```

- [ ] **Step 3: Add fileInputRef**

Add ref declaration after `formRef` (around line 698):

```typescript
const fileInputRef = ref<HTMLInputElement>()
```

- [ ] **Step 4: Add triggerUpload function**

Add after `handleAdd` function (around line 817):

```typescript
function triggerUpload() {
  fileInputRef.value?.click()
}
```

- [ ] **Step 5: Add handleFileUpload function**

Add after `triggerUpload` function:

```typescript
function handleFileUpload(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return

  // Validate file type
  if (!file.name.endsWith('.py')) {
    ElMessage.error('仅支持 .py 文件')
    return
  }

  // Validate file size (100KB limit)
  if (file.size > 100 * 1024) {
    ElMessage.error('文件大小不能超过 100KB')
    return
  }

  // Read file content
  const reader = new FileReader()
  reader.onload = (e) => {
    const content = e.target?.result as string
    if (content) {
      // Switch to new mode with content filled
      currentScriptId.value = '__new__'
      formData.value = {
        name: file.name.replace('.py', ''),
        description: '',
        outputFormat: 'json',
        scope: 'page',
        script: content,
      }
      testResult.value = null
      ElMessage.success('脚本已加载，请填写信息后保存')
    }
  }
  reader.onerror = () => {
    ElMessage.error('文件读取失败')
  }
  reader.readAsText(file, 'UTF-8')

  // Clear input to allow re-upload same file
  input.value = ''
}
```

- [ ] **Step 6: Verify changes work**

Run: `npm run dev`
Manual test:
1. Open http://localhost:5173
2. Login as admin
3. Navigate to 导出脚本管理
4. Click "上传脚本" button
5. Select a .py file
6. Verify content fills into editor

- [ ] **Step 7: Commit**

```bash
git add src/views/admin/ExportScriptManager.vue
git commit -m "feat: add script upload button to ExportScriptManager"
```

---

### Task 2: Add Upload Help Tab to ExportScriptManager

**Files:**
- Modify: `src/views/admin/ExportScriptManager.vue`

- [ ] **Step 1: Add new Tab pane for upload instructions**

Add after the "完整示例" tab-pane (around line 458, before `</el-tabs>`):

```vue
<el-tab-pane label="上传脚本">
  <div class="help-content">
    <h4>操作步骤</h4>
    <ol>
      <li>点击「上传脚本」按钮，选择本地 .py 文件</li>
      <li>脚本内容自动填充到编辑器</li>
      <li>填写脚本名称、描述（文件名会作为默认名称）</li>
      <li>可继续在线编辑调整代码</li>
      <li>点击「保存」完成创建</li>
    </ol>

    <h4>文件要求</h4>
    <ul>
      <li>文件类型：<code>.py</code>（Python 脚本）</li>
      <li>文件编码：<code>UTF-8</code></li>
      <li>文件大小：不超过 100KB</li>
    </ul>

    <h4>本地开发建议</h4>
    <p>推荐使用 VSCode 或 PyCharm 编写脚本：</p>
    <ul>
      <li>安装 Python 插件获得语法高亮和补全</li>
      <li>脚本变量参考本页面「变量参考」Tab</li>
      <li>上传后使用「测试」功能验证脚本正确性</li>
    </ul>

    <h4>示例脚本结构</h4>
<pre class="help-code"># ============================================
# 导出脚本 — JSON 格式
# ============================================
# 入参变量（系统自动注入）：
#   data       : list[dict]  — 数据记录
#   fields     : list[dict]  — 字段配置
#   page_name  : str         — 页面名称
#
# 输出变量：
#   result     : str | bytes — 导出内容（必须）
# ============================================

result = json.dumps(data, ensure_ascii=False, indent=2)</pre>
  </div>
</el-tab-pane>
```

- [ ] **Step 2: Verify Tab displays correctly**

Run: `npm run dev`
Manual test:
1. Navigate to 导出脚本管理
2. Click "使用说明" section
3. Verify "上传脚本" Tab appears
4. Click Tab and verify content displays

- [ ] **Step 3: Commit**

```bash
git add src/views/admin/ExportScriptManager.vue
git commit -m "docs: add upload script help tab to ExportScriptManager"
```

---

### Task 3: Add Upload Button to ValidationScriptManager

**Files:**
- Modify: `src/views/admin/ValidationScriptManager.vue`

- [ ] **Step 1: Add hidden file input element and upload button**

Add after the existing "新增" button in the card-header section (around line 10-13):

```vue
<el-button type="success" size="small" @click="triggerUpload">
  <el-icon><Upload /></el-icon>
  上传脚本
</el-button>
<input
  ref="fileInputRef"
  type="file"
  accept=".py"
  style="display: none"
  @change="handleFileUpload"
/>
```

- [ ] **Step 2: Import Upload icon**

Add `Upload` to the icon imports (around line 378):

```typescript
import { Plus, Upload, CircleCloseFilled, WarningFilled } from '@element-plus/icons-vue'
```

- [ ] **Step 3: Add fileInputRef**

Add ref declaration after `formRef` (around line 429):

```typescript
const fileInputRef = ref<HTMLInputElement>()
```

- [ ] **Step 4: Add triggerUpload function**

Add after `handleAdd` function (around line 482):

```typescript
function triggerUpload() {
  fileInputRef.value?.click()
}
```

- [ ] **Step 5: Add handleFileUpload function**

Add after `triggerUpload` function:

```typescript
function handleFileUpload(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return

  // Validate file type
  if (!file.name.endsWith('.py')) {
    ElMessage.error('仅支持 .py 文件')
    return
  }

  // Validate file size (100KB limit)
  if (file.size > 100 * 1024) {
    ElMessage.error('文件大小不能超过 100KB')
    return
  }

  // Read file content
  const reader = new FileReader()
  reader.onload = (e) => {
    const content = e.target?.result as string
    if (content) {
      // Switch to new mode with content filled
      currentScriptId.value = '__new__'
      formData.value = {
        name: file.name.replace('.py', ''),
        description: '',
        script: content,
      }
      testResult.value = null
      ElMessage.success('脚本已加载，请填写信息后保存')
    }
  }
  reader.onerror = () => {
    ElMessage.error('文件读取失败')
  }
  reader.readAsText(file, 'UTF-8')

  // Clear input to allow re-upload same file
  input.value = ''
}
```

- [ ] **Step 6: Verify changes work**

Run: `npm run dev`
Manual test:
1. Navigate to 校验脚本管理
2. Click "上传脚本" button
3. Select a .py file
4. Verify content fills into editor

- [ ] **Step 7: Commit**

```bash
git add src/views/admin/ValidationScriptManager.vue
git commit -m "feat: add script upload button to ValidationScriptManager"
```

---

### Task 4: Add Upload Help Tab to ValidationScriptManager

**Files:**
- Modify: `src/views/admin/ValidationScriptManager.vue`

- [ ] **Step 1: Add new Tab pane for upload instructions**

Add after the "完整示例" tab-pane (around line 353, before `</el-tabs>`):

```vue
<el-tab-pane label="上传脚本">
  <div class="help-content">
    <h4>操作步骤</h4>
    <ol>
      <li>点击「上传脚本」按钮，选择本地 .py 文件</li>
      <li>脚本内容自动填充到编辑器</li>
      <li>填写脚本名称、描述（文件名会作为默认名称）</li>
      <li>可继续在线编辑调整代码</li>
      <li>点击「保存」完成创建</li>
    </ol>

    <h4>文件要求</h4>
    <ul>
      <li>文件类型：<code>.py</code>（Python 脚本）</li>
      <li>文件编码：<code>UTF-8</code></li>
      <li>文件大小：不超过 100KB</li>
    </ul>

    <h4>本地开发建议</h4>
    <p>推荐使用 VSCode 或 PyCharm 编写脚本：</p>
    <ul>
      <li>安装 Python 插件获得语法高亮和补全</li>
      <li>脚本变量参考本页面「变量参考」Tab</li>
      <li>上传后使用「测试」功能验证脚本正确性</li>
    </ul>

    <h4>示例脚本结构</h4>
<pre class="help-code"># ============================================
# 校验脚本
# ============================================
# 入参变量（系统自动注入）：
#   record     : dict         — 当前提交的数据
#   action     : str          — 'create' 或 'update'
#   old_data   : dict | None  — 修改前的旧数据
#   fields     : list[dict]   — 字段配置
#   collection : str          — 当前集合名
#
# 校验输出：
#   add_error(msg)   — 添加错误（阻止保存）
#   add_warning(msg) — 添加警告（不阻止）
# ============================================

# 示例：必填校验
if not record.get('name'):
    add_error('名称不能为空')</pre>
  </div>
</el-tab-pane>
```

- [ ] **Step 2: Verify Tab displays correctly**

Run: `npm run dev`
Manual test:
1. Navigate to 校验脚本管理
2. Click "使用说明" section
3. Verify "上传脚本" Tab appears
4. Click Tab and verify content displays

- [ ] **Step 3: Commit**

```bash
git add src/views/admin/ValidationScriptManager.vue
git commit -m "docs: add upload script help tab to ValidationScriptManager"
```

---

### Task 5: Create Standalone Documentation

**Files:**
- Create: `docs/脚本上传使用手册.md`

- [ ] **Step 1: Create documentation file**

```markdown
# 脚本上传使用手册

## 概述

本系统支持通过上传 .py 文件将本地编写的导出脚本或校验脚本注入系统。开发人员可以在本地 IDE（如 VSCode、PyCharm）中编写和调试脚本，完成后上传到系统。

## 上传流程

### 1. 本地编写脚本

在本地 IDE 创建 `.py` 文件，编写导出或校验逻辑。

### 2. 上传文件

1. 登录系统（需要 admin 权限）
2. 进入「导出脚本管理」或「校验脚本管理」页面
3. 点击「上传脚本」按钮
4. 选择本地 `.py` 文件

### 3. 填写信息

上传后：
- 脚本内容自动填充到编辑器
- 文件名（不含 .py 后缀）作为默认脚本名称
- 可继续在线编辑调整代码

### 4. 保存脚本

填写脚本名称和描述后，点击「保存」完成创建。

### 5. 测试验证

使用「测试」功能验证脚本正确性，确保能正常执行。

## 文件要求

| 要求 | 说明 |
|------|------|
| 文件类型 | `.py`（Python 脚本文件） |
| 文件编码 | UTF-8 |
| 文件大小 | 不超过 100KB |

### 编码注意事项

如果上传后脚本内容出现乱码：
1. 确保本地文件保存为 UTF-8 编码
2. 在在线编辑器中手动修正乱码部分
3. 重新保存

## 本地开发建议

### VSCode 配置

1. 安装 Python 插件（Microsoft 官方）
2. 设置默认编码为 UTF-8：
   ```json
   {
     "files.encoding": "utf8"
   }
   ```
3. 使用代码格式化工具（如 Black）保持代码整洁

### PyCharm 配置

1. 设置文件编码：Settings → Editor → File Encodings → UTF-8
2. 启用 Python 代码补全和语法检查

### 调试技巧

由于系统沙箱环境限制（禁用 `import`、`open` 等函数），本地调试时建议：

1. 模拟入参变量：
   ```python
   # 本地调试时模拟数据
   data = [{'id': '1', 'name': '测试'}]
   fields = [{'fieldName': 'name', 'label': '名称'}]
   page_name = '测试页面'

   # 实际脚本代码
   result = json.dumps(data, ensure_ascii=False)
   print(result)
   ```

2. 仅使用预注入模块：
   - json, re, math, collections, datetime, timedelta
   - 导出脚本额外支持：csv, io, ET (xml.etree.ElementTree), minidom
   - 可选：pd (pandas), np (numpy)

## 脚本模板参考

### 导出脚本模板（JSON 格式）

```python
# ============================================
# 导出脚本 — JSON 格式
# ============================================
# 入参变量（系统自动注入）：
#   data       : list[dict]  — 数据记录
#   fields     : list[dict]  — 字段配置
#   page_name  : str         — 页面名称
#
# 输出变量：
#   result     : str | bytes — 导出内容（必须）
#   filename   : str         — 文件名（可选）
#   content_type : str       — MIME类型（可选）
# ============================================

result = json.dumps(data, ensure_ascii=False, indent=2)
```

### 导出脚本模板（CSV 格式）

```python
# ============================================
# 导出脚本 — CSV 格式
# ============================================

output = io.StringIO()
writer = csv.writer(output)

# 写入表头
headers = [f['label'] for f in fields]
writer.writerow(headers)

# 写入数据行
for row in data:
    writer.writerow([str(row.get(f['fieldName'], '')) for f in fields])

result = output.getvalue()
```

### 校验脚本模板

```python
# ============================================
# 校验脚本
# ============================================
# 入参变量（系统自动注入）：
#   record     : dict         — 当前提交的数据
#   action     : str          — 'create' 或 'update'
#   old_data   : dict | None  — 修改前的旧数据
#   fields     : list[dict]   — 字段配置
#   collection : str          — 当前集合名
#
# 校验输出：
#   add_error(msg)   — 添加错误（阻止保存）
#   add_warning(msg) — 添加警告（不阻止）
#
# 查询函数：
#   query(collection)               — 查询全部记录
#   query_one(collection, id)       — 按 ID 查询
#   find_by(collection, field, val) — 按字段值查找
#   get_relations(collection, id)   — 查询现有关联
#
# 关联函数：
#   set_relations(field, target_col, target_field, ids)
# ============================================

# 示例：必填校验
if not record.get('name'):
    add_error('名称不能为空')

# 示例：唯一性校验（仅新增时）
if action == 'create':
    existing = find_by(collection, 'name', record.get('name'))
    if existing:
        add_error('名称已存在')
```

## 常见问题

### Q1: 上传后脚本乱码怎么办？

确保本地文件使用 UTF-8 编码保存。VSCode 可通过右下角编码指示器切换。

### Q2: 保存时报"import statements are not allowed"？

系统沙箱禁止使用 `import` 语句。请使用预注入的模块（json, csv, io, re 等），它们无需导入即可直接使用。

### Q3: 脚本执行超时怎么办？

脚本执行超时为 60 秒（菜单级导出为 300 秒）。如果超时：
1. 检查是否有死循环
2. 优化数据处理逻辑
3. 减少数据量（分批处理）

### Q4: 上传的脚本可以继续编辑吗？

可以。上传后内容填充到编辑器，可继续在线编辑调整代码，然后保存。

### Q5: 如何验证上传的脚本是否正确？

使用页面内的「测试」按钮：
- 导出脚本：使用示例数据运行，预览输出结果
- 校验脚本：使用示例数据运行，检查错误/警告输出

## 相关文档

- 导出脚本详细说明：导出脚本管理页面 → 使用说明 Tab
- 校验脚本详细说明：校验脚本管理页面 → 使用说明 Tab
- 系统架构：CLAUDE.md
```

- [ ] **Step 2: Verify file created**

Run: `ls docs/脚本上传使用手册.md`
Expected: file exists

- [ ] **Step 3: Commit**

```bash
git add docs/脚本上传使用手册.md
git commit -m "docs: add script upload user manual"
```

---

### Task 6: Final Verification

**Files:**
- None

- [ ] **Step 1: Run frontend build**

Run: `npm run build`
Expected: Build succeeds without errors

- [ ] **Step 2: Manual end-to-end test**

1. Navigate to 导出脚本管理
2. Click 上传脚本
3. Select a test .py file
4. Verify content fills editor
5. Fill name/description
6. Click 保存
7. Verify script appears in list
8. Click 测试
9. Verify test runs successfully

Repeat for 校验脚本管理.

- [ ] **Step 3: Final commit (if any fixes needed)**

```bash
git status
# If any uncommitted changes:
git add -A
git commit -m "fix: resolve script upload issues"
```

---

## Self-Review Checklist

**1. Spec coverage:**
- [x] Upload button on both pages → Task 1, Task 3
- [x] File validation (.py, 100KB) → Task 1 Step 5, Task 3 Step 5
- [x] Content fills to editor → Task 1 Step 5, Task 3 Step 5
- [x] Help Tab on both pages → Task 2, Task 4
- [x] Standalone documentation → Task 5

**2. Placeholder scan:**
- [x] No "TBD", "TODO", "implement later"
- [x] All code blocks contain actual implementation
- [x] All commands specify exact paths and expected output

**3. Type consistency:**
- [x] `fileInputRef` used consistently in both pages
- [x] `handleFileUpload` signature consistent
- [x] FormData structure matches existing code