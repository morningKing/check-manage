# 脚本上传功能设计

## 概述

为导出脚本和校验脚本管理页面增加文件上传功能，允许开发人员在本地 IDE 编写 Python 脚本后，通过上传 .py 文件将脚本内容注入系统。

## 背景

### 现有功能

| 功能 | 状态 |
|------|------|
| 导出脚本管理 | ExportScriptManager.vue — CRUD + 脚手架模板 + 使用说明 |
| 校验脚本管理 | ValidationScriptManager.vue — CRUD + 脚手架模板 + 使用说明 |
| 脚本测试 | 后端 `/exportScripts/<id>/test` 和 `/validationScripts/<id>/test` |
| 脚本执行沙箱 | `script_runner.py` — 禁用危险函数，预注入常用模块 |

### 问题

开发人员只能在线编辑脚本，无法利用本地 IDE（PyCharm/VSCode）的代码补全、调试等功能。本地编写完成后需要手动复制粘贴到在线编辑器。

## 需求

### 用户场景

开发人员在本地 IDE 编写并调试脚本，完成后上传 .py 文件到系统：
1. 选择 .py 文件上传
2. 内容自动填充到编辑器（可继续修改）
3. 填写脚本名称、描述
4. 确认保存

### 功能要求

- 支持单个 .py 文件上传
- 上传后可继续在线编辑
- 确认后保存为脚本记录
- 提供使用说明（页面内 + 项目文档）

### 非功能要求

- 文件类型限制：仅 `.py`
- 文件大小限制：100KB
- 编码：UTF-8
- 无需后端改动，纯前端实现

## 设计

### 方案选择

采用**纯前端处理方案**：

- 使用浏览器 `FileReader` API 读取 .py 文件内容
- 内容填充到 CodeMirror 编辑器
- 用户填写信息后通过现有 API 保存

**理由**：
1. 脚本是纯文本，前端读取完全可行
2. 上传后用户通常需要调整（改名称、适配业务）
3. 现有后端 `/test` 接口已能验证脚本正确性
4. 实现成本低，改动范围小

### 前端改动

#### 1. 上传按钮

**位置**：
- `src/views/admin/ExportScriptManager.vue`
- `src/views/admin/ValidationScriptManager.vue`

**UI**：
在"新增"按钮旁增加"上传脚本"按钮：
```
[新增] [上传脚本]
```

点击后触发隐藏的 `<input type="file">` 元素。

#### 2. 文件读取逻辑

```typescript
function handleUpload(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return

  // 校验文件类型
  if (!file.name.endsWith('.py')) {
    ElMessage.error('仅支持 .py 文件')
    return
  }

  // 校验文件大小
  if (file.size > 100 * 1024) {
    ElMessage.error('文件大小不能超过 100KB')
    return
  }

  // 读取文件内容
  const reader = new FileReader()
  reader.onload = (e) => {
    const content = e.target?.result as string
    if (content) {
      // 切换到新增模式，填充内容
      currentScriptId.value = '__new__'
      formData.value = {
        name: file.name.replace('.py', ''), // 默认用文件名作为脚本名
        description: '',
        outputFormat: 'json', // 导出脚本默认值
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

  // 清空 input，允许重复上传同一文件
  input.value = ''
}
```

#### 3. 隐藏文件输入元素

```html
<input
  ref="fileInputRef"
  type="file"
  accept=".py"
  style="display: none"
  @change="handleUpload"
/>
```

### 页面内使用说明

**新增 Tab**："上传脚本"

**内容**：
```
## 上传脚本

### 操作步骤

1. 点击"上传脚本"按钮，选择本地 .py 文件
2. 脚本内容自动填充到编辑器
3. 填写脚本名称、描述（文件名会作为默认名称）
4. 可继续在线编辑调整代码
5. 点击"保存"完成创建

### 文件要求

- 文件类型：.py（Python 脚本）
- 文件编码：UTF-8
- 文件大小：不超过 100KB

### 本地开发建议

推荐使用 VSCode 或 PyCharm 编写脚本：
- 安装 Python 插件获得语法高亮和补全
- 脚本变量参考页面内"变量参考" Tab
- 上传后使用"测试"功能验证脚本正确性

### 示例脚本结构

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
# ============================================

result = json.dumps(data, ensure_ascii=False, indent=2)
```
```

### 项目文档

**文件**：`docs/脚本上传使用手册.md`

**内容结构**：
1. 概述 — 功能介绍
2. 上传流程 — 详细步骤
3. 文件要求 — 格式、编码、大小
4. 本地开发建议 — IDE 配置、调试技巧
5. 脚本模板参考 — 导出/校验脚本模板
6. 常见问题 — 编码问题、语法错误

### 后端

无改动。使用现有 API：
- `POST /exportScripts` — 创建导出脚本
- `POST /validationScripts` — 创建校验脚本

## 实现清单

| 模块 | 文件 | 改动 |
|------|------|------|
| 前端 | ExportScriptManager.vue | 上传按钮 + 文件读取 + 使用说明 Tab |
| 前端 | ValidationScriptManager.vue | 上传按钮 + 文件读取 + 使用说明 Tab |
| 文档 | docs/脚本上传使用手册.md | 新建 |

## 测试要点

1. 选择 .py 文件 → 内容正确填充
2. 选择非 .py 文件 → 提示错误
3. 选择超大文件 → 提示错误
4. 上传后可继续编辑
5. 保存后脚本记录正确创建
6. 使用说明 Tab 内容完整显示

## 风险

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 编码问题 | 非 UTF-8 文件乱码 | FileReader 强制 UTF-8，乱码时用户可编辑修正 |
| 语法错误 | 上传脚本无法执行 | 使用现有"测试"功能验证 |
| 安全风险 | 恶意脚本 | 现有 script_runner.py 沙箱已限制危险函数 |

## 未来扩展

- 批量上传多个脚本
- 后端语法校验接口
- 从 GitHub/Gitee 导入脚本