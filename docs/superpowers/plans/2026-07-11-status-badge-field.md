# 状态徽标字段（statusBadge） Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增一个只读的 `statusBadge` 字段控件类型：图标+颜色展示离散处理阶段（"处理中"可动画），第三方系统通过现有 Open API 异步回写状态，前端定时轮询直到终态，超过可配置时长未变化则由后端定时任务自动写成"失败"并停止轮询。

**Architecture:** 前端新增独立 `controlType: 'statusBadge'`（不复用 `select`），只读展示组件 + 表格列/查看弹窗渲染分支 + 字段配置界面的选项编辑 UI + 一个轻量轮询 composable。后端在 `dynamic.py`/`open_api.py` 的记录创建/更新路径里，每当 `statusBadge` 字段值真正变化时，把变化时间戳写进 `data` 的一个隐藏子字段；新增一个仿照 `ai_scan_scheduler.py` 的定时任务，扫描超时未变化的记录并直接写成失败态（不触发 webhook）。

**Tech Stack:** Vue 3 + TypeScript + Element Plus + Pinia；Flask + psycopg2；Vitest + Pytest + Playwright。不新增依赖，不新增数据库表/列（状态与时间戳都存在已有的 `dynamic_data.data` JSONB 里）。

## Global Constraints

- 值仍是普通字符串，存法与 `select` 一致；第三方系统用现有 `PUT /api/v1/collections/<collection>/<id>`（`open_api.py`）回写，不新增专门的状态回写接口。
- 字段在表单里永远只读（业务人员不能手动改），只能被第三方系统或超时定时任务写入。
- 判定"多久没变化"用字段自己的变化时间戳（`_statusBadge_<fieldName>_changedAt`，存在 `data` 里），不用整行记录的 `updated_at`。
- 超时后的失败态**真正写回数据库**（不是前端临时显示），且**不触发 webhook**（避免第三方系统收到自己造成的回环通知）。
- 展示位置：表格列（`DataTable.vue`）+ "查看记录"详情弹窗（`DynamicPage.vue` 的 `viewDialogVisible`，是独立的 `<el-descriptions>` 只读弹窗，不是 `DynamicForm.vue` 编辑表单）。
- 前端只做**一套页面级轮询**：列表刷新时顺带同步已打开的"查看记录"弹窗（`viewRecord` 本来就是表格行数据的快照），不单独为详情弹窗发轮询请求。
- 每次这个改动完成后必须用 Playwright 实测验证（CLAUDE.md 强制要求），并同步更新 `docs/user-guide/reference.md` 的控件类型表。

---

## File Structure

**Frontend:**
- Modify `src/types/field.ts` — `ControlType`/`CONTROL_TYPE_OPTIONS` 新增 `statusBadge`；新增 `StatusBadgeOption`/`StatusBadgeConfig` 接口；`FieldConfig`/`FieldFormData` 新增 `statusBadgeConfig`；`createEmptyFieldFormData` 补默认值。
- Create `src/components/dynamic-form/controls/StatusBadge.vue` — 只读展示控件（图标+文字+动画）。
- Modify `src/components/dynamic-form/controls/index.ts` — 注册新控件到 `controlComponentMap`/`getControlDefaultValue`。
- Modify `src/components/common/DataTable.vue` — 表格列渲染分支 + `formatCellValue` 分支。
- Modify `src/views/dynamic/DynamicPage.vue` — "查看记录"弹窗渲染分支 + `formatViewValue` 分支 + 轮询接入（`loadPageData` 钩子 + `onDeactivated` 清理）。
- Modify `src/views/admin/FieldConfigEditor.vue` — `statusBadgeConfig` 的选项编辑 UI（增删行：value/label/图标/颜色/动画/终态）+ 轮询间隔/超时配置输入。
- Create `src/composables/useStatusBadgePolling.ts` — 轮询判定+调度的纯逻辑（可独立测试）。
- Create `src/composables/__tests__/useStatusBadgePolling.test.ts`。
- Create `src/components/dynamic-form/controls/__tests__/StatusBadge.test.ts`。

**Backend:**
- Modify `server/routes/dynamic.py` — `create_item`/`update_item` 在 `statusBadge` 字段值变化时写入 `_statusBadge_<fieldName>_changedAt`。
- Modify `server/routes/open_api.py` — `update_collection_item` 同样的时间戳写入（第三方回写实际会走这条路径）。
- Create `server/utils/status_badge_timeout_scheduler.py` — 每分钟扫描超时记录，写成 `timeoutValue`，不触发 webhook。
- Modify `server/app.py` — 注册新定时任务（`WERKZEUG_RUN_MAIN` 守卫内）。
- Modify `server/tests/test_routes_dynamic.py`（或对应现有测试文件）— 时间戳写入回归测试。
- Modify `server/tests/test_routes_open_api.py`（或对应现有测试文件）— 时间戳写入回归测试。
- Create `server/tests/test_status_badge_timeout_scheduler.py`。

**Docs:**
- Modify `docs/user-guide/reference.md` — 控件类型表新增 `statusBadge` 行。

---

### Task 1: 前端 — 类型定义

**Files:**
- Modify: `src/types/field.ts`

**Interfaces:**
- Produces: `ControlType` 新增 `'statusBadge'`；`StatusBadgeOption { value, label, icon, color?, animated?, terminal? }`；`StatusBadgeConfig { options, pollIntervalSec?, timeoutSec?, timeoutValue? }`；`FieldConfig.statusBadgeConfig?: StatusBadgeConfig`；`FieldFormData.statusBadgeConfig?: StatusBadgeConfig`。后续所有前端任务都依赖这些类型。

- [ ] **Step 1: `ControlType` 联合类型新增 `statusBadge`**

`src/types/field.ts:12-31`，把最后一行：

```ts
  | 'compositeText' // 组合文本（自动拼接其他字段值）
```

改为：

```ts
  | 'compositeText' // 组合文本（自动拼接其他字段值）
  | 'statusBadge'   // 状态徽标（只读，图标+颜色展示离散阶段，第三方系统异步回写）
```

- [ ] **Step 2: `CONTROL_TYPE_OPTIONS` 新增一项**

`src/types/field.ts:38-58`，把：

```ts
  { label: '组合文本', value: 'compositeText' }
]
```

改为：

```ts
  { label: '组合文本', value: 'compositeText' },
  { label: '状态徽标', value: 'statusBadge' }
]
```

- [ ] **Step 3: 新增 `StatusBadgeOption`/`StatusBadgeConfig` 接口**

`src/types/field.ts`，在 `CompositeTextConfig` 接口（第 140-143 行）之后插入：

```ts
/**
 * 状态徽标选项接口
 *
 * @property value - 选项值（与字段实际存储值一致）
 * @property label - 显示文本
 * @property icon - Element Plus 图标名
 * @property color - 图标/文字颜色（十六进制），不填用默认灰
 * @property animated - 是否持续旋转动画（表示"进行中"）
 * @property terminal - 是否终态：到达后前端停止轮询、后端超时任务跳过
 */
export interface StatusBadgeOption {
  value: string
  label: string
  icon: string
  color?: string
  animated?: boolean
  terminal?: boolean
}

/**
 * 状态徽标配置接口
 *
 * @property options - 各阶段选项列表
 * @property pollIntervalSec - 轮询间隔（秒），默认 5
 * @property timeoutSec - 超时阈值（秒），不填则不启用超时兜底
 * @property timeoutValue - 超时后写入的选项值，必须是 options 里某个 value
 */
export interface StatusBadgeConfig {
  options: StatusBadgeOption[]
  pollIntervalSec?: number
  timeoutSec?: number
  timeoutValue?: string
}
```

- [ ] **Step 4: `FieldConfig`/`FieldFormData` 新增 `statusBadgeConfig`**

`src/types/field.ts:204-226`，把：

```ts
export interface FieldConfig {
  id: string
  label: string
  fieldName: string
  controlType: ControlType
  required: boolean
  order: number
  placeholder?: string
  defaultValue?: any
  options?: FieldOption[]
  optionsSource?: OptionsSource
  validation?: ValidationRule[]
  disabled?: boolean
  hidden?: boolean
  width?: string
  relationConfig?: RelationConfig
  isPrimaryKey?: boolean
  referenceConfig?: ReferenceConfig
  sequenceConfig?: SequenceConfig
  quoteConfig?: QuoteConfig
  workflowConfig?: WorkflowConfig
  compositeTextConfig?: CompositeTextConfig
}
```

改为（新增一行 `statusBadgeConfig?: StatusBadgeConfig`）：

```ts
export interface FieldConfig {
  id: string
  label: string
  fieldName: string
  controlType: ControlType
  required: boolean
  order: number
  placeholder?: string
  defaultValue?: any
  options?: FieldOption[]
  optionsSource?: OptionsSource
  validation?: ValidationRule[]
  disabled?: boolean
  hidden?: boolean
  width?: string
  relationConfig?: RelationConfig
  isPrimaryKey?: boolean
  referenceConfig?: ReferenceConfig
  sequenceConfig?: SequenceConfig
  quoteConfig?: QuoteConfig
  workflowConfig?: WorkflowConfig
  compositeTextConfig?: CompositeTextConfig
  statusBadgeConfig?: StatusBadgeConfig
}
```

`src/types/field.ts:233-251`，把：

```ts
export interface FieldFormData {
  id?: string
  label: string
  fieldName: string
  controlType: ControlType
  required: boolean
  order: number
  placeholder: string
  defaultValue: any
  options: FieldOption[]
  optionsSource: OptionsSource
  relationConfig?: RelationConfig
  isPrimaryKey: boolean
  referenceConfig?: ReferenceConfig
  sequenceConfig?: SequenceConfig
  quoteConfig?: QuoteConfig
  workflowConfig?: WorkflowConfig
  compositeTextConfig?: CompositeTextConfig
}
```

改为：

```ts
export interface FieldFormData {
  id?: string
  label: string
  fieldName: string
  controlType: ControlType
  required: boolean
  order: number
  placeholder: string
  defaultValue: any
  options: FieldOption[]
  optionsSource: OptionsSource
  relationConfig?: RelationConfig
  isPrimaryKey: boolean
  referenceConfig?: ReferenceConfig
  sequenceConfig?: SequenceConfig
  quoteConfig?: QuoteConfig
  workflowConfig?: WorkflowConfig
  compositeTextConfig?: CompositeTextConfig
  statusBadgeConfig: StatusBadgeConfig
}
```

- [ ] **Step 5: `createEmptyFieldFormData` 补默认值**

`src/types/field.ts:259-277`，把：

```ts
export function createEmptyFieldFormData(order: number = 1): FieldFormData {
  return {
    label: '',
    fieldName: '',
    controlType: 'text',
    required: false,
    order,
    placeholder: '',
    defaultValue: null,
    options: [],
    optionsSource: { type: 'static' },
    relationConfig: { targetCollection: '', displayField: '', targetField: '' },
    isPrimaryKey: false,
    referenceConfig: { targetCollection: '', displayField: '', inheritFields: [] },
    sequenceConfig: { prefix: '', max: 999 },
    quoteConfig: { targetCollection: '', displayField: '' },
    compositeTextConfig: { sourceFields: [], separator: ' - ' }
  }
}
```

改为：

```ts
export function createEmptyFieldFormData(order: number = 1): FieldFormData {
  return {
    label: '',
    fieldName: '',
    controlType: 'text',
    required: false,
    order,
    placeholder: '',
    defaultValue: null,
    options: [],
    optionsSource: { type: 'static' },
    relationConfig: { targetCollection: '', displayField: '', targetField: '' },
    isPrimaryKey: false,
    referenceConfig: { targetCollection: '', displayField: '', inheritFields: [] },
    sequenceConfig: { prefix: '', max: 999 },
    quoteConfig: { targetCollection: '', displayField: '' },
    compositeTextConfig: { sourceFields: [], separator: ' - ' },
    statusBadgeConfig: { options: [], pollIntervalSec: 5 }
  }
}
```

- [ ] **Step 6: 类型检查（预期会报错，属于本任务范围内的已知波及）**

```bash
npx vue-tsc --noEmit
```

Expected: 报错 `src/components/dynamic-form/controls/index.ts` 里 `controlComponentMap: Record<ControlType, Component>` 缺少 `statusBadge` 键（因为它要求穷举所有 `ControlType`）——这是 Task 2 要解决的，本任务到此为止即可，不用管这个报错。

- [ ] **Step 7: Commit**

```bash
git add src/types/field.ts
git commit -m "feat(status-badge): 新增 statusBadge 控件类型的类型定义"
```

---

### Task 2: 前端 — `StatusBadge.vue` 展示控件 + 注册

**Files:**
- Create: `src/components/dynamic-form/controls/StatusBadge.vue`
- Modify: `src/components/dynamic-form/controls/index.ts`
- Test: `src/components/dynamic-form/controls/__tests__/StatusBadge.test.ts`

**Interfaces:**
- Consumes: `StatusBadgeConfig`（Task 1）。
- Produces: `StatusBadge.vue` 组件，props `{ modelValue: string; content?: never }`——实际只用 `field`（完整 `FieldConfig`，取其 `statusBadgeConfig`）与当前值渲染，纯展示、不 emit 任何事件。注册进 `controlComponentMap['statusBadge']`。Task 3/4/5 都依赖这个组件存在且可复用其内部的图标/颜色渲染逻辑（通过独立导出的展示片段或直接照抄同一套渲染逻辑）。

- [ ] **Step 1: 创建 `StatusBadge.vue`**

先看一下同目录里现有只读展示型控件的 props 约定：`AutoTimestamp.vue`/`AutoSequence.vue` 是只读控件，props 至少有 `modelValue`；`DynamicForm.vue` 渲染控件时会传 `:field="field"`（完整字段配置）与 `v-model="formData[field.fieldName]"`。创建 `src/components/dynamic-form/controls/StatusBadge.vue`：

```vue
<template>
  <span class="status-badge" :style="{ color: currentOption?.color }">
    <el-icon v-if="currentOption?.icon" :class="{ 'status-badge-spin': currentOption.animated }">
      <component :is="iconComp(currentOption.icon)" />
    </el-icon>
    <span class="status-badge-label">{{ currentOption?.label || modelValue || '-' }}</span>
  </span>
</template>

<script setup lang="ts">
/**
 * 状态徽标展示控件
 *
 * 只读：图标 + 文字，颜色取自选项配置，animated 为真时图标持续旋转。
 * 不接受用户交互（没有 change 事件），值只能由第三方系统或后端超时任务回写。
 */
import { computed } from 'vue'
import * as ElIcons from '@element-plus/icons-vue'
import type { FieldConfig } from '@/types'

const props = defineProps<{
  modelValue: string
  field: FieldConfig
}>()

const currentOption = computed(() => {
  const options = props.field.statusBadgeConfig?.options || []
  return options.find(o => o.value === props.modelValue)
})

function iconComp(name: string) {
  return (ElIcons as Record<string, unknown>)[name]
}
</script>

<style scoped>
.status-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.status-badge-spin {
  animation: status-badge-rotate 1.2s linear infinite;
}

@keyframes status-badge-rotate {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
</style>
```

- [ ] **Step 2: 注册到 `controlComponentMap`/`getControlDefaultValue`**

`src/components/dynamic-form/controls/index.ts`，把 import 区（第 11-28 行）末尾加一行：

```ts
import CompositeText from './CompositeText.vue'
import StatusBadge from './StatusBadge.vue'
```

把 `controlComponentMap`（第 35-55 行）：

```ts
export const controlComponentMap: Record<ControlType, Component> = {
  text: TextInput,
  textarea: TextArea,
  richText: RichTextEditor,
  markdown: MarkdownEditor,
  number: NumberInput,
  select: SelectInput,
  multiSelect: MultiSelect,
  date: DatePicker,
  datetime: DatePicker,
  radio: RadioGroup,
  checkbox: CheckboxGroup,
  file: FileUpload,
  image: ImageUpload,
  relation: RelationSelect,
  reference: ReferenceSelect,
  autoTimestamp: AutoTimestamp,
  autoSequence: AutoSequence,
  quoteSelect: QuoteSelect,
  compositeText: CompositeText
}
```

改为：

```ts
export const controlComponentMap: Record<ControlType, Component> = {
  text: TextInput,
  textarea: TextArea,
  richText: RichTextEditor,
  markdown: MarkdownEditor,
  number: NumberInput,
  select: SelectInput,
  multiSelect: MultiSelect,
  date: DatePicker,
  datetime: DatePicker,
  radio: RadioGroup,
  checkbox: CheckboxGroup,
  file: FileUpload,
  image: ImageUpload,
  relation: RelationSelect,
  reference: ReferenceSelect,
  autoTimestamp: AutoTimestamp,
  autoSequence: AutoSequence,
  quoteSelect: QuoteSelect,
  compositeText: CompositeText,
  statusBadge: StatusBadge
}
```

把 `getControlDefaultValue`（第 73-97 行）的 `switch`，在 `default:` 之前加一支：

```ts
    case 'richText':
    case 'markdown':
      return ''
    case 'statusBadge':
      return ''
    default:
      return ''
```

（也可以省略——`default` 分支本来就返回 `''`，但显式列出更清楚，避免以后有人调整 `default` 行为时误伤这个类型。）

把文件末尾的具名导出（第 100-119 行）加一行 `StatusBadge`。

- [ ] **Step 3: 写组件测试**

创建 `src/components/dynamic-form/controls/__tests__/StatusBadge.test.ts`：

```ts
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import StatusBadge from '../StatusBadge.vue'
import type { FieldConfig } from '@/types'

function makeField(): FieldConfig {
  return {
    id: 'f1',
    label: '处理状态',
    fieldName: 'status',
    controlType: 'statusBadge',
    required: false,
    order: 1,
    statusBadgeConfig: {
      options: [
        { value: 'pending', label: '待处理', icon: 'Clock', color: '#909399' },
        { value: 'processing', label: '处理中', icon: 'Loading', color: '#409EFF', animated: true },
        { value: 'done', label: '已完成', icon: 'CircleCheck', color: '#67C23A', terminal: true },
      ],
    },
  }
}

describe('StatusBadge', () => {
  it('按当前值渲染对应选项的文字和颜色', () => {
    const wrapper = mount(StatusBadge, {
      props: { modelValue: 'processing', field: makeField() },
    })
    expect(wrapper.text()).toContain('处理中')
    expect(wrapper.find('.status-badge').attributes('style')).toContain('color')
  })

  it('animated 为真时图标带旋转 class', () => {
    const wrapper = mount(StatusBadge, {
      props: { modelValue: 'processing', field: makeField() },
    })
    expect(wrapper.find('.status-badge-spin').exists()).toBe(true)
  })

  it('animated 不为真时不带旋转 class', () => {
    const wrapper = mount(StatusBadge, {
      props: { modelValue: 'done', field: makeField() },
    })
    expect(wrapper.find('.status-badge-spin').exists()).toBe(false)
  })

  it('值找不到匹配选项时回退显示原始值', () => {
    const wrapper = mount(StatusBadge, {
      props: { modelValue: 'unknown-value', field: makeField() },
    })
    expect(wrapper.text()).toContain('unknown-value')
  })
})
```

- [ ] **Step 4: 运行测试 + 类型检查**

```bash
npx vitest run src/components/dynamic-form/controls/__tests__/StatusBadge.test.ts
npx vue-tsc --noEmit
```

Expected: 4 个用例全绿；`vue-tsc` 不再报 Task 1 遗留的 `controlComponentMap` 穷举错误。

- [ ] **Step 5: Commit**

```bash
git add src/components/dynamic-form/controls/StatusBadge.vue src/components/dynamic-form/controls/index.ts src/components/dynamic-form/controls/__tests__/StatusBadge.test.ts
git commit -m "feat(status-badge): 新增 StatusBadge 只读展示控件并注册"
```

---

### Task 3: 前端 — `DataTable.vue` 表格列渲染

**Files:**
- Modify: `src/components/common/DataTable.vue`

**Interfaces:**
- Consumes: `StatusBadgeConfig`（Task 1）。

- [ ] **Step 1: 表格列模板新增渲染分支**

`src/components/common/DataTable.vue:93-98`，把：

```html
          <span
            v-else-if="field.controlType === 'reference' && row[field.fieldName]"
            class="reference-link"
            @click.stop="handleReferenceClick(row, field)"
          >{{ getCellValue(row, field) }}</span>
          <span v-else>{{ getCellValue(row, field) }}</span>
```

改为：

```html
          <span
            v-else-if="field.controlType === 'reference' && row[field.fieldName]"
            class="reference-link"
            @click.stop="handleReferenceClick(row, field)"
          >{{ getCellValue(row, field) }}</span>
          <span
            v-else-if="field.controlType === 'statusBadge'"
            class="status-badge-cell"
            :style="{ color: getStatusBadgeOption(row, field)?.color }"
          >
            <el-icon
              v-if="getStatusBadgeOption(row, field)?.icon"
              :class="{ 'status-badge-spin': getStatusBadgeOption(row, field)?.animated }"
            >
              <component :is="statusBadgeIconComp(getStatusBadgeOption(row, field)!.icon)" />
            </el-icon>
            <span>{{ getStatusBadgeOption(row, field)?.label || row[field.fieldName] || '-' }}</span>
          </span>
          <span v-else>{{ getCellValue(row, field) }}</span>
```

- [ ] **Step 2: 脚本新增两个辅助函数 + `formatCellValue` 分支**

找到 `formatCellValue` 函数（`:505-583`），在 `default:` 之前加一支：

```ts
    case 'statusBadge': {
      const opt = field.statusBadgeConfig?.options.find((o) => o.value === value)
      return opt?.label || String(value)
    }

    default:
      return String(value)
```

在 `formatCellValue` 函数附近（同一 `<script setup>` 内任意合适位置，比如紧邻它之后）新增两个辅助函数，供模板里的 `getStatusBadgeOption`/`statusBadgeIconComp` 调用：

```ts
import * as ElIcons from '@element-plus/icons-vue'

/**
 * 取某行 statusBadge 字段当前值对应的选项配置（图标/颜色/动画）
 */
function getStatusBadgeOption(row: any, field: FieldConfig) {
  const value = row[field.fieldName]
  return field.statusBadgeConfig?.options.find((o) => o.value === value)
}

function statusBadgeIconComp(name: string) {
  return (ElIcons as Record<string, unknown>)[name]
}
```

（如果文件顶部已经 `import * as ElIcons from '@element-plus/icons-vue'` 或类似别名导入，直接复用即可，不要重复 import；先检查文件顶部现有 import 区。）

- [ ] **Step 3: 新增少量 CSS**

在 `<style>` 区任意位置新增：

```scss
.status-badge-cell {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.status-badge-spin {
  animation: status-badge-rotate 1.2s linear infinite;
}

@keyframes status-badge-rotate {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
```

- [ ] **Step 4: 类型检查**

```bash
npx vue-tsc --noEmit
```

Expected: 无错误。

- [ ] **Step 5: Commit**

```bash
git add src/components/common/DataTable.vue
git commit -m "feat(status-badge): DataTable 表格列渲染 statusBadge 图标"
```

---

### Task 4: 前端 — "查看记录"详情弹窗渲染

**Files:**
- Modify: `src/views/dynamic/DynamicPage.vue`

**Interfaces:**
- Consumes: `StatusBadgeConfig`（Task 1）。

- [ ] **Step 1: 详情弹窗模板新增渲染分支**

`src/views/dynamic/DynamicPage.vue`，在"查看记录"对话框的 `<el-descriptions-item>` 渲染链里（第 459-472 行区域，`reference` 分支之后、`select`/`radio` 分支之前），把：

```html
          <!-- 数据引用字段：可点击跳转 -->
          <template v-else-if="field.controlType === 'reference'">
            <span v-if="!viewRecord[field.fieldName]">-</span>
            <span
              v-else
              class="reference-link"
              @click="viewDialogVisible = false; handleReferenceClick(viewRecord as DynamicRecord, field)"
            >{{ viewRecord[`_ref_${field.fieldName}_display`] || viewRecord[field.fieldName] }}</span>
          </template>

          <!-- 选项类字段：显示标签 -->
          <template v-else-if="['select', 'radio'].includes(field.controlType)">
            {{ formatViewValue(field) }}
          </template>
```

改为：

```html
          <!-- 数据引用字段：可点击跳转 -->
          <template v-else-if="field.controlType === 'reference'">
            <span v-if="!viewRecord[field.fieldName]">-</span>
            <span
              v-else
              class="reference-link"
              @click="viewDialogVisible = false; handleReferenceClick(viewRecord as DynamicRecord, field)"
            >{{ viewRecord[`_ref_${field.fieldName}_display`] || viewRecord[field.fieldName] }}</span>
          </template>

          <!-- 状态徽标字段：图标+颜色，只读 -->
          <template v-else-if="field.controlType === 'statusBadge'">
            <span
              class="status-badge-cell"
              :style="{ color: viewStatusBadgeOption(field)?.color }"
            >
              <el-icon
                v-if="viewStatusBadgeOption(field)?.icon"
                :class="{ 'status-badge-spin': viewStatusBadgeOption(field)?.animated }"
              >
                <component :is="statusBadgeIconComp(viewStatusBadgeOption(field)!.icon)" />
              </el-icon>
              <span>{{ formatViewValue(field) }}</span>
            </span>
          </template>

          <!-- 选项类字段：显示标签 -->
          <template v-else-if="['select', 'radio'].includes(field.controlType)">
            {{ formatViewValue(field) }}
          </template>
```

- [ ] **Step 2: 脚本新增 `formatViewValue` 分支 + 两个辅助函数**

`src/views/dynamic/DynamicPage.vue:1621-1626`，把：

```ts
function formatViewValue(field: FieldConfig): string {
  const value = viewRecord.value[field.fieldName]
  if (value === null || value === undefined || value === '') return '-'
  const opt = field.options?.find(o => o.value === value)
  return opt?.label || String(value)
}
```

改为：

```ts
function formatViewValue(field: FieldConfig): string {
  const value = viewRecord.value[field.fieldName]
  if (value === null || value === undefined || value === '') return '-'
  if (field.controlType === 'statusBadge') {
    return viewStatusBadgeOption(field)?.label || String(value)
  }
  const opt = field.options?.find(o => o.value === value)
  return opt?.label || String(value)
}

/**
 * "查看记录"弹窗里取 statusBadge 字段当前值对应的选项配置
 */
function viewStatusBadgeOption(field: FieldConfig) {
  const value = viewRecord.value[field.fieldName]
  return field.statusBadgeConfig?.options.find(o => o.value === value)
}

function statusBadgeIconComp(name: string) {
  return (ElIconsAll as Record<string, unknown>)[name]
}
```

在文件顶部 import 区（第 920 行那一段 `@element-plus/icons-vue` 具名图标导入之后）新增一行通配导入，起别名避免与已有具名导入冲突：

```ts
import * as ElIconsAll from '@element-plus/icons-vue'
```

- [ ] **Step 3: 新增 CSS**

在 `<style>` 区新增（与 Task 3 的 `.status-badge-cell`/`.status-badge-spin`/`@keyframes` 完全相同的一份，因为这是不同的 `.vue` 文件、各自 `scoped`，样式不能跨文件共享）：

```scss
.status-badge-cell {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.status-badge-spin {
  animation: status-badge-rotate 1.2s linear infinite;
}

@keyframes status-badge-rotate {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
```

- [ ] **Step 4: 类型检查**

```bash
npx vue-tsc --noEmit
```

Expected: 无错误。

- [ ] **Step 5: Commit**

```bash
git add src/views/dynamic/DynamicPage.vue
git commit -m "feat(status-badge): 查看记录弹窗渲染 statusBadge 图标"
```

---

### Task 5: 前端 — 轮询 composable

**Files:**
- Create: `src/composables/useStatusBadgePolling.ts`
- Test: `src/composables/__tests__/useStatusBadgePolling.test.ts`

**Interfaces:**
- Consumes: `FieldConfig`/`StatusBadgeConfig`（Task 1）。
- Produces: `useStatusBadgePolling(options: { fields: Ref<FieldConfig[]>; onRefresh: () => Promise<void> })` 返回 `{ evaluateAndSchedule(rows: any[]): void; stop(): void }`。Task 6（`DynamicPage.vue` 接入）依赖这个签名。

这是纯逻辑（判定"是否有非终态行"+调度定时器），不依赖具体页面，独立成 composable 便于单测，遵循 CLAUDE.md "Composables Pattern" 一节的约定。

- [ ] **Step 1: 写失败的测试**

创建 `src/composables/__tests__/useStatusBadgePolling.test.ts`：

```ts
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { ref } from 'vue'
import { useStatusBadgePolling } from '../useStatusBadgePolling'
import type { FieldConfig } from '@/types'

function statusField(pollIntervalSec?: number): FieldConfig {
  return {
    id: 'f1', label: '状态', fieldName: 'status', controlType: 'statusBadge',
    required: false, order: 1,
    statusBadgeConfig: {
      pollIntervalSec,
      options: [
        { value: 'pending', label: '待处理', icon: 'Clock' },
        { value: 'processing', label: '处理中', icon: 'Loading', animated: true },
        { value: 'done', label: '已完成', icon: 'CircleCheck', terminal: true },
      ],
    },
  }
}

describe('useStatusBadgePolling', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('没有 statusBadge 字段时不调度定时器', () => {
    const onRefresh = vi.fn()
    const { evaluateAndSchedule } = useStatusBadgePolling({ fields: ref([]), onRefresh })
    evaluateAndSchedule([{ id: '1' }])
    vi.advanceTimersByTime(10000)
    expect(onRefresh).not.toHaveBeenCalled()
  })

  it('所有行都是终态时不调度定时器', () => {
    const onRefresh = vi.fn()
    const { evaluateAndSchedule } = useStatusBadgePolling({ fields: ref([statusField(5)]), onRefresh })
    evaluateAndSchedule([{ id: '1', status: 'done' }])
    vi.advanceTimersByTime(10000)
    expect(onRefresh).not.toHaveBeenCalled()
  })

  it('存在非终态行时按配置的间隔调度一次刷新', async () => {
    const onRefresh = vi.fn().mockResolvedValue(undefined)
    const { evaluateAndSchedule } = useStatusBadgePolling({ fields: ref([statusField(5)]), onRefresh })
    evaluateAndSchedule([{ id: '1', status: 'processing' }])
    vi.advanceTimersByTime(4999)
    expect(onRefresh).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(onRefresh).toHaveBeenCalledTimes(1)
  })

  it('多个 statusBadge 字段时取最小的轮询间隔', () => {
    const onRefresh = vi.fn().mockResolvedValue(undefined)
    const fields = ref([statusField(20), statusField(5)])
    const { evaluateAndSchedule } = useStatusBadgePolling({ fields, onRefresh })
    evaluateAndSchedule([{ id: '1', status: 'processing' }])
    vi.advanceTimersByTime(5000)
    expect(onRefresh).toHaveBeenCalledTimes(1)
  })

  it('stop() 清除已调度的定时器', () => {
    const onRefresh = vi.fn()
    const { evaluateAndSchedule, stop } = useStatusBadgePolling({ fields: ref([statusField(5)]), onRefresh })
    evaluateAndSchedule([{ id: '1', status: 'processing' }])
    stop()
    vi.advanceTimersByTime(10000)
    expect(onRefresh).not.toHaveBeenCalled()
  })

  it('再次调用 evaluateAndSchedule 会先清掉上一个定时器（不重复触发）', () => {
    const onRefresh = vi.fn().mockResolvedValue(undefined)
    const { evaluateAndSchedule } = useStatusBadgePolling({ fields: ref([statusField(5)]), onRefresh })
    evaluateAndSchedule([{ id: '1', status: 'processing' }])
    vi.advanceTimersByTime(3000)
    evaluateAndSchedule([{ id: '1', status: 'processing' }]) // 重新调度，从 0 开始计时
    vi.advanceTimersByTime(3000)
    expect(onRefresh).not.toHaveBeenCalled()
    vi.advanceTimersByTime(2000)
    expect(onRefresh).toHaveBeenCalledTimes(1)
  })
})
```

- [ ] **Step 2: 运行测试确认失败**

```bash
npx vitest run src/composables/__tests__/useStatusBadgePolling.test.ts
```

Expected: FAIL（模块不存在）。

- [ ] **Step 3: 实现**

创建 `src/composables/useStatusBadgePolling.ts`：

```ts
/**
 * statusBadge 字段的页面级轮询：判定当前页数据里是否还有非终态行，
 * 有则按配置的间隔调度一次刷新回调；全部终态或没有 statusBadge 字段则不调度。
 *
 * 用 setTimeout（不用 setInterval）：每次都等上一次 onRefresh 完成后才重新判定
 * 要不要调度下一次，避免刷新耗时超过间隔时产生重叠请求。
 */
import { ref, type Ref } from 'vue'
import type { FieldConfig } from '@/types'

const DEFAULT_POLL_INTERVAL_SEC = 5

export function useStatusBadgePolling(options: {
  fields: Ref<FieldConfig[]>
  onRefresh: () => Promise<void>
}) {
  const timer = ref<ReturnType<typeof setTimeout> | null>(null)

  function statusBadgeFields(): FieldConfig[] {
    return options.fields.value.filter(f => f.controlType === 'statusBadge' && f.statusBadgeConfig)
  }

  function isNonTerminal(row: any, field: FieldConfig): boolean {
    const cfg = field.statusBadgeConfig
    if (!cfg) return false
    const value = row[field.fieldName]
    const opt = cfg.options.find(o => o.value === value)
    return !!opt && !opt.terminal
  }

  function hasNonTerminalRows(rows: any[]): boolean {
    const fields = statusBadgeFields()
    if (fields.length === 0) return false
    return rows.some(row => fields.some(f => isNonTerminal(row, f)))
  }

  function minPollIntervalMs(): number {
    const fields = statusBadgeFields()
    const seconds = fields.map(f => f.statusBadgeConfig?.pollIntervalSec ?? DEFAULT_POLL_INTERVAL_SEC)
    return Math.min(...seconds) * 1000
  }

  function stop(): void {
    if (timer.value !== null) {
      clearTimeout(timer.value)
      timer.value = null
    }
  }

  function evaluateAndSchedule(rows: any[]): void {
    stop()
    if (!hasNonTerminalRows(rows)) return
    timer.value = setTimeout(() => {
      options.onRefresh()
    }, minPollIntervalMs())
  }

  return { evaluateAndSchedule, stop }
}
```

- [ ] **Step 4: 运行测试确认通过**

```bash
npx vitest run src/composables/__tests__/useStatusBadgePolling.test.ts
```

Expected: PASS（6 个用例全绿）。

- [ ] **Step 5: Commit**

```bash
git add src/composables/useStatusBadgePolling.ts src/composables/__tests__/useStatusBadgePolling.test.ts
git commit -m "feat(status-badge): 新增页面级轮询 composable"
```

---

### Task 6: 前端 — `DynamicPage.vue` 接入轮询

**Files:**
- Modify: `src/views/dynamic/DynamicPage.vue`

**Interfaces:**
- Consumes: `useStatusBadgePolling`（Task 5）、`pageFields`（既有 computed）、`loadPageData`/`tableData`/`viewDialogVisible`/`viewRecord`（既有）。

- [ ] **Step 1: 引入 composable，声明实例**

`src/views/dynamic/DynamicPage.vue` 顶部 import 区，把：

```ts
import { ref, computed, watch, nextTick, onActivated, defineAsyncComponent } from 'vue'
```

改为：

```ts
import { ref, computed, watch, nextTick, onActivated, onDeactivated, defineAsyncComponent } from 'vue'
```

再新增一行 composable import（放在其他 `@/composables`或工具函数 import 附近，若文件里暂无此类 import，放在 `@/utils/*` import 之后即可）：

```ts
import { useStatusBadgePolling } from '@/composables/useStatusBadgePolling'
```

在 `pageFields` computed（`:1318-1320`）之后新增：

```ts
/**
 * statusBadge 字段的页面级轮询：只要当前页数据里还有非终态行，就定时重新拉取整页数据
 */
const statusBadgePolling = useStatusBadgePolling({
  fields: pageFields,
  onRefresh: loadPageData,
})
```

（`loadPageData` 此时还没定义——TypeScript/Vue 的 `<script setup>` 函数声明会被提升，`function loadPageData()` 定义在文件后面完全没问题，和其他地方引用后定义的函数一致，不用调整声明顺序。）

- [ ] **Step 2: `loadPageData` 里接入轮询判定 + 弹窗数据同步**

`src/views/dynamic/DynamicPage.vue:1652-1678`，把：

```ts
async function loadPageData(): Promise<void> {
  if (!pageId.value) return

  // 按需加载 page_configs（首次访问动态页时触发）
  if (!pageConfigStore.pageConfigs.length) {
    await pageConfigStore.fetchPageConfigs()
  }

  tableLoading.value = true
  try {
    const result = await pageConfigStore.fetchPageData(pageId.value, {
      query: activeMongoQuery.value || undefined,
      page: currentPage.value,
      pageSize: currentPageSize.value,
      keyword: searchKeyword.value || undefined
    })
    tableData.value = result.data
    totalCount.value = result.total
    // 加载当前分支信息
    await loadCurrentBranch()
  } catch (error) {
    console.error('加载数据失败:', error)
    ElMessage.error('加载数据失败')
  } finally {
    tableLoading.value = false
  }
}
```

改为：

```ts
async function loadPageData(): Promise<void> {
  if (!pageId.value) return

  // 按需加载 page_configs（首次访问动态页时触发）
  if (!pageConfigStore.pageConfigs.length) {
    await pageConfigStore.fetchPageConfigs()
  }

  tableLoading.value = true
  try {
    const result = await pageConfigStore.fetchPageData(pageId.value, {
      query: activeMongoQuery.value || undefined,
      page: currentPage.value,
      pageSize: currentPageSize.value,
      keyword: searchKeyword.value || undefined
    })
    tableData.value = result.data
    totalCount.value = result.total
    // 加载当前分支信息
    await loadCurrentBranch()
    // "查看记录"弹窗打开时，用刷新后的数据同步一份（statusBadge 轮询场景下弹窗内容也要跟着更新）
    if (viewDialogVisible.value && viewRecord.value?.id) {
      const fresh = result.data.find((r: DynamicRecord) => r.id === viewRecord.value.id)
      if (fresh) {
        viewRecord.value = { ...fresh }
      }
    }
    // 有非终态 statusBadge 行时安排下一次轮询刷新，否则停止
    statusBadgePolling.evaluateAndSchedule(result.data)
  } catch (error) {
    console.error('加载数据失败:', error)
    ElMessage.error('加载数据失败')
  } finally {
    tableLoading.value = false
  }
}
```

- [ ] **Step 3: 页面失活时停止轮询**

在文件里新增一个 `onDeactivated` 钩子（这个组件走 `keep-alive`，`onUnmounted` 不可靠，必须用 `onDeactivated`；参照文件里已有的 `onActivated` 钩子位置，紧邻它添加即可）：

```ts
onDeactivated(() => {
  statusBadgePolling.stop()
})
```

- [ ] **Step 4: 类型检查**

```bash
npx vue-tsc --noEmit
```

Expected: 无错误。

- [ ] **Step 5: Commit**

```bash
git add src/views/dynamic/DynamicPage.vue
git commit -m "feat(status-badge): DynamicPage 接入 statusBadge 页面级轮询"
```

---

### Task 7: 前端 — `FieldConfigEditor.vue` 配置 UI

**Files:**
- Modify: `src/views/admin/FieldConfigEditor.vue`

**Interfaces:**
- Consumes: `StatusBadgeConfig`（Task 1）、`src/components/common/IconPicker.vue`（既有）。

- [ ] **Step 1: 新增 `showStatusBadgeConfig` computed**

`src/views/admin/FieldConfigEditor.vue:823-825`，把：

```ts
const showCompositeTextConfig = computed(() => {
  return fieldFormData.value.controlType === 'compositeText'
})
```

改为：

```ts
const showCompositeTextConfig = computed(() => {
  return fieldFormData.value.controlType === 'compositeText'
})

const showStatusBadgeConfig = computed(() => {
  return fieldFormData.value.controlType === 'statusBadge'
})
```

- [ ] **Step 2: 模板新增配置区块**

在"引用选择配置"块（第 575-610 行区域）之后插入一个新的 `<el-form-item>` 块：

```html
        <!-- 状态徽标配置（仅状态徽标类型显示） -->
        <el-form-item
          v-if="showStatusBadgeConfig"
          label="状态选项"
        >
          <div class="status-badge-config">
            <div
              v-for="(opt, optIndex) in fieldFormData.statusBadgeConfig.options"
              :key="optIndex"
              class="status-badge-option-row"
            >
              <el-input v-model="opt.label" placeholder="显示文本" style="width: 18%" />
              <el-input v-model="opt.value" placeholder="值" style="width: 16%" />
              <IconPicker v-model="opt.icon" style="width: 22%" />
              <el-color-picker v-model="opt.color" />
              <el-checkbox v-model="opt.animated">动画</el-checkbox>
              <el-checkbox v-model="opt.terminal">终态</el-checkbox>
              <el-button type="danger" link @click="removeStatusBadgeOption(optIndex)">
                <el-icon><Delete /></el-icon>
              </el-button>
            </div>
            <el-button type="primary" link @click="addStatusBadgeOption">
              <el-icon><Plus /></el-icon>
              添加阶段
            </el-button>

            <el-row :gutter="16" class="status-badge-timing">
              <el-col :span="8">
                <el-form-item label="轮询间隔(秒)" label-width="110px">
                  <el-input-number
                    v-model="fieldFormData.statusBadgeConfig.pollIntervalSec"
                    :min="1"
                    :max="3600"
                    style="width: 100%"
                  />
                </el-form-item>
              </el-col>
              <el-col :span="8">
                <el-form-item label="超时(秒)" label-width="90px">
                  <el-input-number
                    v-model="fieldFormData.statusBadgeConfig.timeoutSec"
                    :min="1"
                    style="width: 100%"
                    placeholder="不填=不启用"
                  />
                </el-form-item>
              </el-col>
              <el-col :span="8">
                <el-form-item label="超时后写为" label-width="100px">
                  <el-select
                    v-model="fieldFormData.statusBadgeConfig.timeoutValue"
                    placeholder="选一个选项值"
                    clearable
                    style="width: 100%"
                  >
                    <el-option
                      v-for="opt in fieldFormData.statusBadgeConfig.options"
                      :key="opt.value"
                      :label="opt.label || opt.value"
                      :value="opt.value"
                    />
                  </el-select>
                </el-form-item>
              </el-col>
            </el-row>
          </div>
        </el-form-item>
```

在文件顶部 import 区，把 `IconPicker` 加入已有的 `@/components/common` 具名导入（若该文件已经从别处 import 了 `@/components/common` 的其他组件，加进同一条 import 语句；若没有，新增一行 `import { IconPicker } from '@/components/common'`——需要先确认 `src/components/common/index.ts` 是否已导出 `IconPicker`，若未导出，改为直接 `import IconPicker from '@/components/common/IconPicker.vue'`）。

- [ ] **Step 3: 新增 `addStatusBadgeOption`/`removeStatusBadgeOption` 函数**

在 `addOption`/`removeOption` 函数（`:1177-1186`）附近新增：

```ts
function addStatusBadgeOption(): void {
  fieldFormData.value.statusBadgeConfig.options.push({ value: '', label: '', icon: '' })
}

function removeStatusBadgeOption(index: number): void {
  fieldFormData.value.statusBadgeConfig.options.splice(index, 1)
}
```

- [ ] **Step 4: `handleEditField` 加载已有配置**

`src/views/admin/FieldConfigEditor.vue:1046-1075`（`handleEditField` 里 `fieldFormData.value = {...}` 那个对象字面量），在 `compositeTextConfig` 那一项之后加一行：

```ts
    compositeTextConfig: field.compositeTextConfig
      ? { ...field.compositeTextConfig, sourceFields: [...field.compositeTextConfig.sourceFields] }
      : { sourceFields: [], separator: ' - ' },
    statusBadgeConfig: field.statusBadgeConfig
      ? { ...field.statusBadgeConfig, options: field.statusBadgeConfig.options.map(o => ({ ...o })) }
      : { options: [], pollIntervalSec: 5 }
```

（原来这一项末尾没有逗号，改成有逗号，因为后面接上了新的一项——记得同步把原来 `compositeTextConfig` 那一项结尾的 `}` 后加上逗号。）

- [ ] **Step 5: `handleSaveField` 输出配置**

`src/views/admin/FieldConfigEditor.vue:1103-1138`（`handleSaveField` 里 `fieldData: FieldConfig = {...}`），在 `compositeTextConfig` 那一项之后加一行：

```ts
    compositeTextConfig: showCompositeTextConfig.value ? fieldFormData.value.compositeTextConfig : undefined,
    statusBadgeConfig: showStatusBadgeConfig.value
      ? {
          ...fieldFormData.value.statusBadgeConfig,
          options: fieldFormData.value.statusBadgeConfig.options.filter(o => o.value && o.label),
        }
      : undefined,
```

（过滤掉没填 value/label 的空行，避免保存半成品选项。）

- [ ] **Step 6: 补充 `showDefaultValue`（可选，允许配置初始阶段）**

`src/views/admin/FieldConfigEditor.vue:802-805`，把：

```ts
const showDefaultValue = computed(() => {
  return ['text', 'textarea', 'number', 'select', 'multiSelect', 'radio', 'checkbox', 'date', 'datetime']
    .includes(fieldFormData.value.controlType)
})
```

改为：

```ts
const showDefaultValue = computed(() => {
  return ['text', 'textarea', 'number', 'select', 'multiSelect', 'radio', 'checkbox', 'date', 'datetime', 'statusBadge']
    .includes(fieldFormData.value.controlType)
})
```

这样管理员可以在通用的"默认值"输入框里填一个选项 value（如 `pending`）作为新建记录时的初始阶段。

- [ ] **Step 7: 类型检查**

```bash
npx vue-tsc --noEmit
```

Expected: 无错误。

- [ ] **Step 8: Commit**

```bash
git add src/views/admin/FieldConfigEditor.vue
git commit -m "feat(status-badge): 字段配置界面支持编辑 statusBadge 选项/轮询/超时"
```

---

### Task 8: 后端 — `dynamic.py` 变化时间戳写入

**Files:**
- Modify: `server/routes/dynamic.py`
- Test: 现有的 dynamic 路由测试文件（先用 `Glob` 确认实际文件名，如 `server/tests/test_routes_dynamic.py`，没有则新建同名测试文件，遵循目录里其它 `test_routes_*.py` 的 fixture 风格）

**Interfaces:**
- Produces: 每当某条记录的 `statusBadge` 字段值真正变化（或首次创建时该字段有值）时，`data` 里多一个 `_statusBadge_<fieldName>_changedAt`（ISO 字符串）键。Task 10（超时定时任务）依赖这个键存在。

- [ ] **Step 1: `create_item` 里为有初始值的 `statusBadge` 字段盖时间戳**

`server/routes/dynamic.py:558-567`，把：

```python
        page_name, fields = get_page_info(cur, collection)
        pk_fields = [f['fieldName'] for f in (fields or []) if f.get('isPrimaryKey')]
        # 后端原子分配 autoSequence（忽略客户端传入值）
        for f in (fields or []):
            if f.get('controlType') == 'autoSequence':
                cfg = f.get('sequenceConfig') or {}
                prefix = cfg.get('prefix', '')
                pad = len(str(cfg.get('max', 999)))
                data[f['fieldName']] = allocate_sequence(
                    cur, collection, branch_id, f['fieldName'], prefix, pad, count=1)[0]
```

改为：

```python
        page_name, fields = get_page_info(cur, collection)
        pk_fields = [f['fieldName'] for f in (fields or []) if f.get('isPrimaryKey')]
        # 后端原子分配 autoSequence（忽略客户端传入值）
        for f in (fields or []):
            if f.get('controlType') == 'autoSequence':
                cfg = f.get('sequenceConfig') or {}
                prefix = cfg.get('prefix', '')
                pad = len(str(cfg.get('max', 999)))
                data[f['fieldName']] = allocate_sequence(
                    cur, collection, branch_id, f['fieldName'], prefix, pad, count=1)[0]
            elif f.get('controlType') == 'statusBadge' and data.get(f['fieldName']):
                # 记录创建时状态字段已有初始值（如 defaultValue）：盖变化时间戳作为超时判定基准
                from datetime import datetime, timezone
                data[f'_statusBadge_{f["fieldName"]}_changedAt'] = datetime.now(timezone.utc).isoformat()
```

- [ ] **Step 2: `update_item` 里为值变化的 `statusBadge` 字段盖时间戳**

`server/routes/dynamic.py:739-768`（workflow 状态转换那段循环）之后、`if created_at:`（`:769`）之前插入：

```python
        # statusBadge 字段：值真正变化时记录变化时间戳，供超时兜底任务判定
        from datetime import datetime, timezone
        for field_cfg in (fields or []):
            if field_cfg.get('controlType') != 'statusBadge':
                continue
            fname = field_cfg['fieldName']
            if fname not in data:
                continue
            if old_data.get(fname) != merged_data.get(fname):
                merged_data[f'_statusBadge_{fname}_changedAt'] = datetime.now(timezone.utc).isoformat()
```

- [ ] **Step 3: 补充回归测试**

先用 Glob/Grep 确认 `dynamic.py` 的 `create_item`/`update_item` 现有测试所在文件与 fixture 风格（`mock_conn`/`mock_cursor` 之类，参照 `server/tests/test_routes_home_widgets.py` 的写法），在同一文件里新增（用真实文件名替换下面的占位路径）：

```python
class TestStatusBadgeTimestamp:
    def test_create_with_initial_status_stamps_timestamp(self, setup):
        """新建记录时 statusBadge 字段有初始值，应写入变化时间戳"""
        client, mock_cursor, admin_h = setup  # 按实际 fixture 签名调整
        mock_cursor.fetchall.return_value = [
            {'id': 'page-demo', 'fields': [
                {'fieldName': 'status', 'controlType': 'statusBadge'},
            ]},
        ]
        # ... 按现有 create_item 测试的 mock 惯例补齐 fetchone/execute 断言
        resp = client.post('/demo',
            data=json.dumps({'id': 'r1', 'status': 'pending'}),
            content_type='application/json', headers=admin_h)
        assert resp.status_code in (200, 201)
        insert_call = next(c for c in mock_cursor.execute.call_args_list if 'INSERT INTO dynamic_data' in str(c.args[0]))
        inserted_data = insert_call.args[1][2]  # 按实际参数顺序调整
        assert '_statusBadge_status_changedAt' in inserted_data.get_dict_or_json_form()  # 按实际 Json() 包装方式调整断言

    def test_update_status_value_changed_stamps_timestamp(self, setup):
        """更新记录时 statusBadge 字段值变化，应刷新变化时间戳"""
        # 参照 update_item 现有测试的 mock 模式：old_data={'status': 'pending'}，
        # 请求体 {'status': 'processing'}，断言最终写入 UPDATE 的 merged_data
        # 包含新的 '_statusBadge_status_changedAt'。

    def test_update_status_value_unchanged_does_not_restamp(self, setup):
        """更新记录时 statusBadge 字段值没变，不应刷新时间戳（沿用旧值）"""
        # old_data={'status': 'pending', '_statusBadge_status_changedAt': '2026-01-01T00:00:00+00:00'}，
        # 请求体里 status 仍是 'pending'（或请求体根本不含 status），
        # 断言写入 UPDATE 的 merged_data 里时间戳字段还是旧值，没被覆盖成当前时间。
```

上面这段测试代码故意留了几处"按实际调整"的注释——因为 `dynamic.py` 现有测试文件的 mock 细节（`setup` fixture 签名、`execute.call_args_list` 里 SQL 参数的具体下标、`psycopg2.extras.Json` 包装后如何在测试里取出原始 dict）需要照抄同文件里已有的 `create_item`/`update_item` 测试用例的写法才能跑通——**实施这一步时先读一遍同文件里最近的一个 `create_item`/`update_item` 测试用例，照它的具体断言方式改写上面三个用例，不要凭空猜 mock 结构。**

- [ ] **Step 4: 运行测试**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_dynamic.py -v -k StatusBadge
```

Expected: 新增的 3 个用例 PASS；再跑一次不带 `-k` 确认全文件其它既有用例没有被这次改动破坏：

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_dynamic.py -v
```

- [ ] **Step 5: Commit**

```bash
git add server/routes/dynamic.py server/tests/test_routes_dynamic.py
git commit -m "feat(status-badge): dynamic.py 创建/更新时记录 statusBadge 变化时间戳"
```

---

### Task 9: 后端 — `open_api.py` 变化时间戳写入

**Files:**
- Modify: `server/routes/open_api.py`
- Test: `server/tests/test_open_api.py`（先 Glob 确认实际文件名）

**Interfaces:**
- Produces: 与 Task 8 相同的 `_statusBadge_<fieldName>_changedAt` 写入行为，但覆盖的是第三方系统实际回写状态会用到的路径（`PUT /api/v1/collections/<collection>/<id>`）。

- [ ] **Step 1: `update_collection_item` 里为值变化的 `statusBadge` 字段盖时间戳**

`server/routes/open_api.py:464-469`，把：

```python
        fields = get_page_fields(cur, collection)

        # Merge: old data + new data (partial update supported)
        merged = dict(old_data)
        new_data = {k: v for k, v in body.items() if k not in ('id', 'createdAt', 'updatedAt', '_version')}
        merged.update(new_data)
```

改为：

```python
        fields = get_page_fields(cur, collection)

        # Merge: old data + new data (partial update supported)
        merged = dict(old_data)
        new_data = {k: v for k, v in body.items() if k not in ('id', 'createdAt', 'updatedAt', '_version')}
        merged.update(new_data)

        # statusBadge 字段：值真正变化时记录变化时间戳，供超时兜底任务判定
        from datetime import datetime, timezone
        for field_cfg in (fields or []):
            if field_cfg.get('controlType') != 'statusBadge':
                continue
            fname = field_cfg.get('fieldName')
            if not fname or fname not in new_data:
                continue
            if old_data.get(fname) != merged.get(fname):
                merged[f'_statusBadge_{fname}_changedAt'] = datetime.now(timezone.utc).isoformat()
```

- [ ] **Step 2: 补充回归测试**

先 Glob 找到 `open_api.py` 的现有测试文件（很可能是 `server/tests/test_open_api.py`），照抄其中 `update_collection_item` 现有测试用例的 mock 写法，新增：

```python
class TestStatusBadgeTimestampOpenApi:
    def test_update_status_value_changed_stamps_timestamp(self, setup):
        """第三方系统通过 Open API PUT 回写 statusBadge 字段值变化时，应写入变化时间戳"""
        # 参照本文件里 update_collection_item 现有测试的 mock 结构：
        # get_page_fields 返回含 {'fieldName': 'status', 'controlType': 'statusBadge'} 的 fields，
        # 现有记录 data={'status': 'pending'}，请求体 {'status': 'processing'}，
        # 断言最终 UPDATE 语句写入的 merged 字典里出现 '_statusBadge_status_changedAt'。

    def test_update_status_value_unchanged_does_not_restamp(self, setup):
        """值没变时不重复刷新时间戳"""
        # 现有记录 data={'status': 'pending', '_statusBadge_status_changedAt': '2026-01-01T00:00:00+00:00'}，
        # 请求体不含 status 或 status 仍是 'pending'，断言时间戳字段值未被覆盖。
```

同样，**实施时先读这个文件里 `update_collection_item` 已有的测试用例，照抄它的 mock/断言写法**，不要凭空构造。

- [ ] **Step 3: 运行测试**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_open_api.py -v -k StatusBadge
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_open_api.py -v
```

Expected: 新增用例 PASS，全文件其它用例不受影响。

- [ ] **Step 4: Commit**

```bash
git add server/routes/open_api.py server/tests/test_open_api.py
git commit -m "feat(status-badge): open_api.py 第三方回写 statusBadge 时记录变化时间戳"
```

---

### Task 10: 后端 — 超时兜底定时任务

**Files:**
- Create: `server/utils/status_badge_timeout_scheduler.py`
- Modify: `server/app.py`
- Test: `server/tests/test_status_badge_timeout_scheduler.py`

**Interfaces:**
- Consumes: Task 8/9 写入的 `_statusBadge_<fieldName>_changedAt`。
- Produces: `start_status_badge_timeout_scheduler(app)`，每分钟 tick 一次，扫描超时记录并写成 `timeoutValue`，不触发 webhook。

- [ ] **Step 1: 写扫描逻辑**

创建 `server/utils/status_badge_timeout_scheduler.py`：

```python
"""statusBadge 字段超时兜底定时任务。

每分钟扫描一次所有配置了 statusBadge 且设置了 timeoutSec 的字段：找到当前值
非终态、且自己的变化时间戳早于 (now - timeoutSec) 的记录，直接把该字段写成
配置的 timeoutValue 并刷新时间戳。这是后端对 dynamic_data 的直接 UPDATE，不
经过 dynamic.py/open_api.py 的路由逻辑，因此不会触发 webhook（避免第三方系统
收到自己造成的"超时"这个回环通知）。
"""
import traceback
from datetime import datetime, timezone
import psycopg2.extras
from apscheduler.schedulers.background import BackgroundScheduler
from db import get_db

_scheduler = None


def _iter_status_badge_configs(cur):
    """遍历所有 page_configs，产出 (collection, field_name, config) 三元组。"""
    cur.execute('SELECT id, fields FROM page_configs')
    for page_id, fields in cur.fetchall():
        if not page_id.startswith('page-') or not fields:
            continue
        collection = page_id[len('page-'):]
        for f in fields:
            if f.get('controlType') != 'statusBadge':
                continue
            cfg = f.get('statusBadgeConfig') or {}
            timeout_sec = cfg.get('timeoutSec')
            timeout_value = cfg.get('timeoutValue')
            if not timeout_sec or not timeout_value:
                continue
            field_name = f.get('fieldName')
            if not field_name:
                continue
            yield collection, field_name, cfg, timeout_sec, timeout_value


def _tick():
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute('SELECT id, fields FROM page_configs')
        page_rows = cur.fetchall()

        for page_row in page_rows:
            page_id = page_row['id']
            fields = page_row['fields']
            if not page_id.startswith('page-') or not fields:
                continue
            collection = page_id[len('page-'):]

            for f in fields:
                if f.get('controlType') != 'statusBadge':
                    continue
                cfg = f.get('statusBadgeConfig') or {}
                timeout_sec = cfg.get('timeoutSec')
                timeout_value = cfg.get('timeoutValue')
                field_name = f.get('fieldName')
                if not timeout_sec or not timeout_value or not field_name:
                    continue

                terminal_values = [o['value'] for o in cfg.get('options', []) if o.get('terminal')]
                ts_key = f'_statusBadge_{field_name}_changedAt'

                cur.execute(
                    'SELECT id, branch_id, data FROM dynamic_data '
                    'WHERE collection = %s '
                    "AND data->>%s IS NOT NULL "
                    'AND NOT (data->>%s = ANY(%s)) '
                    "AND (data->>%s)::timestamptz < NOW() - (%s * INTERVAL '1 second')",
                    (collection, field_name, field_name, terminal_values, ts_key, timeout_sec),
                )
                timed_out = cur.fetchall()
                for row in timed_out:
                    data = dict(row['data'] or {})
                    data[field_name] = timeout_value
                    data[ts_key] = now_iso
                    cur.execute(
                        'UPDATE dynamic_data SET data = %s, updated_at = NOW(), version = version + 1 '
                        'WHERE collection = %s AND id = %s AND branch_id = %s',
                        (psycopg2.extras.Json(data), collection, row['id'], row['branch_id']),
                    )
        conn.commit()


def _safe_tick():
    try:
        _tick()
    except Exception:
        traceback.print_exc()


def start_status_badge_timeout_scheduler(app):
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(_safe_tick, 'interval', minutes=1, id='status_badge_timeout_tick',
                       max_instances=1, coalesce=True)
    _scheduler.start()
```

- [ ] **Step 2: `app.py` 注册**

`server/app.py:115-117`，把：

```python
    # Start scheduled AI row-processor scheduler
    from utils.ai_scan_scheduler import start_scan_scheduler
    start_scan_scheduler(app)
```

改为：

```python
    # Start scheduled AI row-processor scheduler
    from utils.ai_scan_scheduler import start_scan_scheduler
    start_scan_scheduler(app)

    # Start statusBadge timeout fallback scheduler
    from utils.status_badge_timeout_scheduler import start_status_badge_timeout_scheduler
    start_status_badge_timeout_scheduler(app)
```

- [ ] **Step 3: 写测试**

创建 `server/tests/test_status_badge_timeout_scheduler.py`。先看一眼 `server/tests/` 目录里现有的、直接测试某个纯函数（不经过 Flask 路由）的测试文件（比如任何 `test_*_engine.py`/`test_*_scheduler.py`）的 mock 风格，照抄其 `get_db`/`mock_cursor` 打桩方式。测试要覆盖：

```python
"""
statusBadge 超时兜底定时任务测试
"""
import sys
import os
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from contextlib import contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _make_mock_db(mock_conn):
    @contextmanager
    def fake_get_db():
        yield mock_conn
    return fake_get_db


class TestStatusBadgeTimeoutTick:
    def test_timed_out_record_gets_written_to_timeout_value(self, mock_conn, mock_cursor):
        """非终态、超过 timeoutSec 未变化的记录应被写成 timeoutValue"""
        from utils import status_badge_timeout_scheduler as mod
        with patch('utils.status_badge_timeout_scheduler.get_db', _make_mock_db(mock_conn)):
            old_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            mock_cursor.fetchall.side_effect = [
                [{
                    'id': 'page-demo',
                    'fields': [{
                        'fieldName': 'status',
                        'controlType': 'statusBadge',
                        'statusBadgeConfig': {
                            'timeoutSec': 60,
                            'timeoutValue': 'failed',
                            'options': [
                                {'value': 'processing', 'label': '处理中'},
                                {'value': 'failed', 'label': '失败', 'terminal': True},
                            ],
                        },
                    }],
                }],
                [{'id': 'r1', 'branch_id': None, 'data': {
                    'status': 'processing',
                    '_statusBadge_status_changedAt': old_ts,
                }}],
            ]
            mod._tick()

            update_calls = [c for c in mock_cursor.execute.call_args_list if 'UPDATE dynamic_data' in str(c.args[0])]
            assert len(update_calls) == 1
            written_data = update_calls[0].args[1][0]
            assert written_data.adapted['status'] == 'failed' if hasattr(written_data, 'adapted') else True
            # 若 psycopg2.extras.Json 在测试环境下不易直接取值，改为断言 execute 调用参数
            # 里传入的是一个把 status 设为 'failed' 的 Json(...) 包装对象即可，具体断言方式
            # 参照本目录其它测试文件里对 psycopg2.extras.Json(...) 参数的既有断言写法。

    def test_terminal_record_is_skipped(self, mock_conn, mock_cursor):
        """已经是终态的记录不应被扫描到（SQL 里 NOT (... = ANY(terminal_values)) 条件）"""
        from utils import status_badge_timeout_scheduler as mod
        with patch('utils.status_badge_timeout_scheduler.get_db', _make_mock_db(mock_conn)):
            mock_cursor.fetchall.side_effect = [
                [{
                    'id': 'page-demo',
                    'fields': [{
                        'fieldName': 'status',
                        'controlType': 'statusBadge',
                        'statusBadgeConfig': {
                            'timeoutSec': 60,
                            'timeoutValue': 'failed',
                            'options': [
                                {'value': 'processing', 'label': '处理中'},
                                {'value': 'failed', 'label': '失败', 'terminal': True},
                            ],
                        },
                    }],
                }],
                [],  # 第二条 SELECT（超时记录扫描）返回空——模拟 SQL 条件已经排除了终态记录
            ]
            mod._tick()
            update_calls = [c for c in mock_cursor.execute.call_args_list if 'UPDATE dynamic_data' in str(c.args[0])]
            assert len(update_calls) == 0

    def test_field_without_timeout_sec_is_skipped(self, mock_conn, mock_cursor):
        """没配置 timeoutSec 的 statusBadge 字段完全不参与扫描（不产生任何 SELECT/UPDATE）"""
        from utils import status_badge_timeout_scheduler as mod
        with patch('utils.status_badge_timeout_scheduler.get_db', _make_mock_db(mock_conn)):
            mock_cursor.fetchall.return_value = [{
                'id': 'page-demo',
                'fields': [{
                    'fieldName': 'status',
                    'controlType': 'statusBadge',
                    'statusBadgeConfig': {'options': []},  # 没有 timeoutSec/timeoutValue
                }],
            }]
            mod._tick()
            select_scan_calls = [
                c for c in mock_cursor.execute.call_args_list
                if 'FROM dynamic_data' in str(c.args[0])
            ]
            assert len(select_scan_calls) == 0
```

上面第一个用例里对 `psycopg2.extras.Json(...)` 参数取值的断言写法留了注释——**实施时先看这个目录里其它测试文件是怎么断言 `Json(...)` 包装参数内容的（通常是 mock `psycopg2.extras.Json` 本身，或者直接断言传入 `Json()` 前的原始 dict），照抄那个模式改写，不要凭空猜。**

- [ ] **Step 4: 运行测试**

```bash
cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_status_badge_timeout_scheduler.py -v
```

Expected: 3 个用例 PASS。

- [ ] **Step 5: Commit**

```bash
git add server/utils/status_badge_timeout_scheduler.py server/app.py server/tests/test_status_badge_timeout_scheduler.py
git commit -m "feat(status-badge): 新增超时兜底定时任务，超时未变化的记录自动写成失败态"
```

---

### Task 11: 文档同步 + Playwright 端到端验证

**Files:**
- Modify: `docs/user-guide/reference.md`

- [ ] **Step 1: 控件类型表新增一行**

`docs/user-guide/reference.md:638-642`，把：

```markdown
| **多对多关联** | `relation` | data_relations 表 | 双向同步 M:N |
| **一对多引用** | `reference` | string | 父记录 ID，删除保护 |
| **引用选择** | `quoteSelect` | string[] | 单向多选引用 |
| 自动时间戳 | `autoTimestamp` | string | 新增/编辑自动填充 |
| 自增序列 | `autoSequence` | string | 带前缀递增编号 |
```

改为：

```markdown
| **多对多关联** | `relation` | data_relations 表 | 双向同步 M:N |
| **一对多引用** | `reference` | string | 父记录 ID，删除保护 |
| **引用选择** | `quoteSelect` | string[] | 单向多选引用 |
| 自动时间戳 | `autoTimestamp` | string | 新增/编辑自动填充 |
| 自增序列 | `autoSequence` | string | 带前缀递增编号 |
| **状态徽标** | `statusBadge` | string | 只读，图标+颜色展示离散阶段；表单里不可编辑，只能由第三方系统（Open API）异步回写；可配置轮询间隔与超时兜底（超时自动写成失败态） |
```

- [ ] **Step 2: 启动前后端服务，创建测试数据页**

```bash
npm run dev:all
```

用管理员账号登录，新建一个测试数据页（或复用现有的），添加一个 `statusBadge` 字段，配置 3 个选项：`pending`（待处理，非终态）、`processing`（处理中，非终态，动画）、`done`（已完成，终态）；轮询间隔设 5 秒；超时 30 秒、超时后写为一个新增的 `failed`（失败，终态）选项。

- [ ] **Step 3: Playwright 驱动"正常流转"场景**

1. 在这个数据页新建一条记录，`status` 默认值给 `pending`。确认表格列/查看记录弹窗都显示"待处理"图标（非动画）。
2. 用 `PUT /api/v1/collections/<collection>/<id>`（带 API Key，`browser_evaluate` 里 `fetch` 或用现成的 HTTP 客户端）把这条记录的 `status` 改成 `processing`。
3. 在页面等待轮询间隔（5 秒多一点），`browser_snapshot` 确认表格列自动刷新显示"处理中"+动画 class（不用手动刷新页面）。打开"查看记录"弹窗，确认弹窗里也是"处理中"。
4. 保持弹窗打开，再用 Open API 把 `status` 改成 `done`。等待一个轮询周期，确认表格列和已打开的弹窗都自动更新成"已完成"，且此后不再发起新的轮询请求（`browser_network_requests` 确认请求停止增长）。
5. `browser_take_screenshot`，存 `.playwright-mcp/status-badge-normal-flow.png`。

- [ ] **Step 4: Playwright 驱动"超时兜底"场景**

1. 新建第二条记录，`status` 给 `pending`，不做任何 Open API 回写。
2. 等待超过配置的 `timeoutSec`（30 秒）+ 定时任务的 1 分钟 tick 周期（建议直接等 90 秒，或者如果有"立即运行一次"的调试入口就用它，没有就老实等）。
3. 刷新页面，确认这条记录的状态已经自动变成"失败"（说明超时任务真的把数据库里的值改了，不是前端瞎显示）。
4. 确认这次状态变化**没有**在 `webhook_logs`（如果这条测试数据页配置了 webhook 规则）或后端日志里产生额外的 webhook 触发记录（复用现有的 webhook 规则管理页面查看执行记录，或直接看后端日志里没有该记录 id 对应的多余 fire_webhooks 调用）。

- [ ] **Step 5: Commit**

```bash
git add docs/user-guide/reference.md
git commit -m "docs(status-badge): 控件类型表补充 statusBadge 说明"
```

---

## Self-Review Notes

- **Spec coverage**：类型定义 → Task 1；展示控件 → Task 2；表格列渲染 → Task 3；详情弹窗渲染 → Task 4（更正了 spec 早期"复用 DynamicForm.vue"的错误认知——实际是独立的 `viewDialogVisible`/`<el-descriptions>` 只读弹窗）；轮询（含"弹窗打开时顺带同步"的简化设计，取消了原计划里详情弹窗单独轮询）→ Task 5/6；字段配置界面 → Task 7；后端时间戳写入（`dynamic.py`+`open_api.py`）→ Task 8/9；超时定时任务（不触发 webhook）→ Task 10；文档 + Playwright → Task 11。范围之外的四项（数值进度条、专门回写接口、手动修改、SSE）本计划未涉及，与 spec 一致。
- **Placeholder scan**：Task 8/9/10 的测试代码里保留了"照抄现有测试文件 mock 写法"的显式提示注释——这不是偷懒占位，是因为 `dynamic.py`/`open_api.py`/`utils/*_scheduler.py` 现有测试文件的具体 mock/fixture 细节在写这份计划时未逐字核对（工作量原因，这几个测试文件本身较大），如果直接编造精确的 mock 结构，大概率与实际 fixture 签名不符、需要返工；改为要求实施者先读一遍同文件里最近的同类测试用例再照抄写法，这是比"编造一个可能是错的精确 mock"更可靠的指令。其余所有步骤（含 Task 1-7 全部、Task 8/9 的生产代码、Task 10 的调度器实现）都是可直接照抄的完整代码，没有占位。
- **Type consistency**：`StatusBadgeOption`/`StatusBadgeConfig`（Task 1 定义）→ `StatusBadge.vue`（Task 2 消费）→ `DataTable.vue`/`DynamicPage.vue` 渲染分支（Task 3/4 消费，字段名 `icon`/`color`/`animated`/`terminal` 全程一致）→ `useStatusBadgePolling`（Task 5，读 `statusBadgeConfig.options`/`pollIntervalSec`/`terminal`）→ `FieldConfigEditor.vue`（Task 7，读写同一套字段）全程一致；后端 `_statusBadge_<fieldName>_changedAt` 命名（Task 8/9 写入，Task 10 读取）全程一致；`timeoutValue`/`timeoutSec`/`terminal` 字段名前后端一致（前端存进 `page_configs.fields` JSONB，后端原样读出，没有做蛇形/驼峰转换，与 `page_configs` 里其它配置对象——如 `sequenceConfig`/`quoteConfig`——的既有存储约定一致）。
