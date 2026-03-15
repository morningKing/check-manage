# Plan: 富文本字段控件实现

## 背景

在现有 16 种字段控件基础上，新增 `richText` 富文本编辑器控件，使用 `@vueup/vue-quill` 作为编辑器库。

## 技术选型

**@vueup/vue-quill** — Quill 的 Vue 3 封装版本
- 基于 Quill.js，国际流行的富文本编辑器
- Vue 3 Composition API 支持
- 支持工具栏自定义、主题切换
- 输出 HTML 格式，便于存储和展示

## 修改文件清单

### 1. 类型定义 — `src/types/field.ts`

**ControlType 新增**：
```typescript
export type ControlType =
  | ... // 现有类型
  | 'richText'
```

**CONTROL_TYPE_OPTIONS 新增**：
```typescript
{ label: '富文本', value: 'richText' }
```

### 2. 控件组件 — 新建 `src/components/dynamic-form/controls/RichTextEditor.vue`

功能：
- 使用 `@vueup/vue-quill` 编辑器
- 支持常用工具栏（加粗、斜体、下划线、标题、列表、链接、图片）
- v-model 双向绑定，存储 HTML 字符串
- 支持 `field.placeholder`、`field.disabled`
- 最小高度 200px，最大高度 400px，内容超出自动滚动

### 3. 控件注册 — `src/components/dynamic-form/controls/index.ts`

```typescript
import RichTextEditor from './RichTextEditor.vue'

export const controlComponentMap = {
  // ...现有
  richText: RichTextEditor,
}

export function getControlDefaultValue(controlType) {
  switch (controlType) {
    // ...
    case 'richText':
      return ''  // HTML 字符串
  }
}
```

### 4. 数据表格显示 — `src/components/common/DataTable.vue`

**formatCellValue 新增 case**：
```typescript
case 'richText':
  // 移除 HTML 标签，显示纯文本预览（最多 50 字符）
  const plain = value?.replace(/<[^>]*>/g, '') || ''
  return plain.length > 50 ? plain.slice(0, 50) + '...' : plain || '-'
```

**getColumnWidth 新增 case**：
```typescript
case 'richText': return '200'
```

**列筛选支持**：
- `isFilterable` 中添加 `'richText'`
- 筛选时匹配纯文本内容（移除 HTML 后匹配）

### 5. 查看详情对话框 — `src/views/dynamic/DynamicPage.vue`

**新增模板分支**：
```vue
<template v-else-if="field.controlType === 'richText'">
  <div class="view-richtext" v-html="viewRecord[field.fieldName] || '-'" />
</template>
```

**样式**：
```css
.view-richtext {
  max-height: 300px;
  overflow-y: auto;
  padding: 8px;
  background: var(--el-fill-color-light);
  border-radius: 4px;
}
```

### 6. Excel 导入导出 — `src/utils/excel.ts`

**EXPORTABLE_TYPES 新增**：
```typescript
'richText'
```

**valueToLabel 新增 case**：
```typescript
if (field.controlType === 'richText') {
  // 导出时移除 HTML 标签，只保留纯文本
  return value?.replace(/<[^>]*>/g, '') || ''
}
```

**labelToValue 新增 case**：
```typescript
if (field.controlType === 'richText') {
  // 导入时直接作为纯文本（不转换为 HTML）
  return label
}
```

**导入模板说明**：
```typescript
richText: '富文本（纯文本导入，不保留格式）'
```

### 7. 后端处理

无需修改。富文本以 HTML 字符串形式存储在 JSONB 字段中，后端透明处理。

### 8. 依赖安装

```bash
npm install @vueup/vue-quill
```

## 不修改的部分

- 数据库结构 — JSONB 动态存储，无需改动
- 后端 API — 透明处理，无需改动
- 权限控制 — 无需特殊处理
- 关联/引用逻辑 — 不涉及

## 验证步骤

1. `npm install @vueup/vue-quill`
2. `npx vitest run` — 现有测试不受影响
3. `npm run build` — TypeScript 编译通过
4. 手动测试：
   - 创建页面配置，添加富文本字段
   - 新增记录，使用富文本编辑器
   - 列表查看富文本预览
   - 详情查看富文本完整内容
   - 导出 Excel 验证纯文本导出
   - 导入验证（纯文本导入）

## 风险与注意事项

1. **XSS 防护**：富文本内容使用 `v-html` 渲染，需确保：
   - 仅在查看时渲染，编辑时使用安全编辑器
   - 后续可考虑添加 HTML 净化（DOMPurify）

2. **存储大小**：富文本 HTML 可能较大，建议：
   - 单字段建议限制在 64KB 以内
   - 大文本场景考虑使用 file 控件

3. **导入限制**：导入仅支持纯文本，无法保留格式。如需完整迁移，建议使用 JSON 导入功能。