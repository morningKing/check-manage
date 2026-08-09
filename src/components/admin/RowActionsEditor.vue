<!--
  行操作配置编辑器

  职责：编辑某个数据页的 RowActionConfig[]（page_configs.row_actions）——
  哪些按钮出现在行「⋯」菜单里、绑哪个执行器（Webhook 规则 / AI 扫描任务）、
  什么条件下可见、点击后弹不弹参数表单、执行结果怎么写回字段。

  真正的执行器（Webhook 规则 / AI 扫描任务）是全局资源，各有自己的管理页，
  这里只做「选哪一个」的薄绑定，不重复编辑执行器本身的配置。
-->
<template>
  <div class="row-actions-editor">
    <div class="ra-list">
      <el-button type="primary" plain size="small" @click="addAction">新增行操作</el-button>
      <el-empty v-if="!list.length" description="尚未配置行操作" :image-size="60" />
      <ul v-else class="ra-items">
        <li
          v-for="(a, i) in list"
          :key="a.id"
          :class="{ active: i === activeIndex }"
          @click="activeIndex = i"
        >
          <span class="ra-name">{{ a.label || '(未命名)' }}</span>
          <el-tag size="small" :type="a.actionType === 'webhook' ? 'primary' : 'success'">
            {{ a.actionType === 'webhook' ? 'Webhook' : 'AI 任务' }}
          </el-tag>
          <el-tag v-if="!a.enabled" size="small" type="info">已停用</el-tag>
          <span class="ra-ops">
            <el-button link size="small" @click.stop="moveUp(i)" :disabled="i === 0">上移</el-button>
            <el-button link size="small" @click.stop="moveDown(i)" :disabled="i === list.length - 1">下移</el-button>
            <el-button link size="small" type="danger" @click.stop="removeAction(i)">删除</el-button>
          </span>
        </li>
      </ul>
    </div>

    <div v-if="current" class="ra-detail">
      <el-divider content-position="left">基本</el-divider>
      <el-form label-width="120px" class="ra-form">
        <el-form-item label="按钮名称" required>
          <el-input v-model="current.label" placeholder="显示在行「⋯」菜单里的文案" maxlength="30" />
        </el-form-item>
        <el-form-item label="类型">
          <el-radio-group v-model="current.actionType">
            <el-radio label="webhook">Webhook</el-radio>
            <el-radio label="aiTask">AI 任务</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="current.enabled" />
        </el-form-item>

        <el-divider content-position="left">绑定</el-divider>
        <el-form-item v-if="current.actionType === 'webhook'" label="Webhook 规则">
          <el-select v-model="current.webhookRuleId" clearable filterable placeholder="选择规则" style="width: 100%">
            <el-option v-for="r in manualRules" :key="r.id" :label="r.name" :value="r.id" />
          </el-select>
          <div class="ra-hint">只列出触发事件为「手动」的规则。去「系统管理 → Webhook 管理」新建。</div>
        </el-form-item>
        <el-form-item v-else label="AI 扫描任务">
          <el-select v-model="current.scanTaskId" clearable filterable placeholder="选择任务" style="width: 100%">
            <el-option v-for="t in scanTasks" :key="t.id" :label="t.name" :value="t.id" />
          </el-select>
          <div class="ra-hint">去「系统管理 → AI 定时任务」新建；只想手动触发可把任务本身设为「未启用」。</div>
        </el-form-item>

        <el-divider content-position="left">显示条件</el-divider>
        <el-form-item label="可见角色">
          <el-select v-model="current.roles" multiple clearable placeholder="留空 = 所有角色" style="width: 100%">
            <el-option v-for="r in roleOptions" :key="r.id" :label="r.name" :value="r.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="仅当">
          <div class="ra-condition">
            <el-select v-model="conditionField" clearable placeholder="字段" style="width: 160px">
              <el-option v-for="f in fieldOptions" :key="f.value" :label="f.label" :value="f.value" />
            </el-select>
            <el-select
              v-if="conditionField"
              v-model="conditionOperator"
              style="width: 110px"
            >
              <el-option v-for="o in OPERATOR_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
            </el-select>
            <el-input
              v-if="conditionField && !NO_VALUE_OPERATORS.includes(conditionOperator)"
              v-model="conditionValueText"
              placeholder="值（属于/不属于用逗号分隔多个值）"
              style="width: 220px"
            />
          </div>
        </el-form-item>
        <el-form-item label="二次确认文案">
          <el-input v-model="current.confirmText" placeholder="留空 = 点击后不弹确认框，直接执行" />
        </el-form-item>

        <template v-if="current.actionType === 'webhook'">
          <el-divider content-position="left">执行结果</el-divider>
          <el-form-item label="状态字段">
            <el-select v-model="current.statusField" clearable placeholder="留空 = 不写状态" style="width: 100%">
              <el-option v-for="f in fieldOptions" :key="f.value" :label="f.label" :value="f.value" />
            </el-select>
          </el-form-item>
          <template v-if="current.statusField">
            <el-form-item label="执行中值">
              <el-input v-model="current.runningValue" placeholder="如：执行中" />
            </el-form-item>
            <el-form-item label="成功值">
              <el-input v-model="current.doneValue" placeholder="如：已完成" />
            </el-form-item>
            <el-form-item label="失败值">
              <el-input v-model="current.failedValue" placeholder="如：执行失败" />
            </el-form-item>
          </template>
          <el-form-item label="响应字段映射">
            <div class="ra-mapping">
              <div v-for="(m, i) in current.responseMapping || []" :key="i" class="ra-map-row">
                <el-input v-model="m.jsonKey" placeholder="响应 JSON 键" style="width: 160px" />
                <span class="ra-arrow">→</span>
                <el-select v-model="m.column" placeholder="目标字段" style="width: 160px">
                  <el-option v-for="f in fieldOptions" :key="f.value" :label="f.label" :value="f.value" />
                </el-select>
                <el-checkbox v-model="m.required">必填</el-checkbox>
                <el-button link type="danger" size="small" @click="removeMappingRow(i)">删除</el-button>
              </div>
              <el-button size="small" @click="addMappingRow">+ 添加映射</el-button>
            </div>
          </el-form-item>
        </template>
        <el-alert
          v-else
          type="info"
          :closable="false"
          show-icon
          title="AI 类动作的执行状态与结果回写，一律使用所绑「AI 扫描任务」里的状态字段配置，此处无需重复设置。"
          style="margin-bottom: 16px"
        />
      </el-form>

      <el-divider content-position="left">参数表单</el-divider>
      <div class="ra-hint" style="margin-bottom: 8px">点击按钮后先弹出的参数表单，留空 = 不弹表单、直接执行。</div>
      <div class="ra-params">
        <div v-for="(p, i) in current.paramFields || []" :key="p.id" class="ra-param-row">
          <el-input v-model="p.fieldName" placeholder="字段名" style="width: 130px" />
          <el-input v-model="p.label" placeholder="显示标签" style="width: 130px" />
          <el-select v-model="p.controlType" style="width: 130px">
            <el-option v-for="c in ALLOWED_PARAM_CONTROLS" :key="c.value" :label="c.label" :value="c.value" />
          </el-select>
          <el-checkbox v-model="p.required">必填</el-checkbox>
          <el-button link type="danger" size="small" @click="removeParamField(i)">删除</el-button>
        </div>
        <el-button size="small" @click="addParamField">+ 新增参数</el-button>
      </div>
    </div>
    <el-empty v-else-if="list.length" description="选择左侧的行操作进行编辑" :image-size="60" />
  </div>
</template>

<script setup lang="ts">
/**
 * 行操作配置编辑器
 *
 * v-model 绑 RowActionConfig[]；内部维护一份本地深拷贝，任何变更（增删排序 /
 * 表单编辑 / 条件构造 / 参数表单 / 响应映射）都会汇总到同一份 list 上，
 * 由一个 deep watch 统一 emit('update:modelValue', ...) 回父组件，
 * 而不是在每个修改点分别手写 emit —— 这样字段级 v-model 编辑（如 current.label）
 * 也能自动同步，不用为每个输入框单独接线。
 */
import { ref, computed, watch, onMounted } from 'vue'
import { v4 as uuidv4 } from 'uuid'
import { useRoleStore } from '@/stores/role'
import { getWebhookRules } from '@/api/webhook'
import { getScanTasks } from '@/api/aiScanTask'
import { CONTROL_TYPE_OPTIONS } from '@/types/field'
import type { FieldConfig, ControlType } from '@/types/field'
import type { RowActionConfig, RowActionCondition } from '@/types/rowAction'
import type { WebhookRule } from '@/types/webhook'
import type { AiScanTask } from '@/types/aiScanTask'

const props = withDefaults(
  defineProps<{
    /** 可选是为了兼容 PageFormData.rowActions?: RowActionConfig[] 这种"尚未保存过就是 undefined"的场景 */
    modelValue?: RowActionConfig[]
    fields: FieldConfig[]
  }>(),
  { modelValue: () => [] }
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: RowActionConfig[]): void
}>()

/** 与后端 server/utils/row_action_validate.py::_FORBIDDEN_PARAM_CONTROLS 保持一致：
 *  这些控件要落库才有语义，放在「点击时弹的参数表单」里没有意义。 */
const FORBIDDEN_PARAM_CONTROLS: ControlType[] = ['relation', 'reference', 'quoteSelect', 'autoSequence']
const ALLOWED_PARAM_CONTROLS = CONTROL_TYPE_OPTIONS.filter(
  (o) => !FORBIDDEN_PARAM_CONTROLS.includes(o.value)
)

const OPERATOR_OPTIONS: { label: string; value: RowActionCondition['operator'] }[] = [
  { label: '等于', value: 'eq' },
  { label: '不等于', value: 'ne' },
  { label: '属于', value: 'in' },
  { label: '不属于', value: 'notIn' },
  { label: '为空', value: 'empty' },
  { label: '不为空', value: 'notEmpty' },
]
const NO_VALUE_OPERATORS = ['empty', 'notEmpty']

function cloneList(v: RowActionConfig[] | undefined | null): RowActionConfig[] {
  return v ? JSON.parse(JSON.stringify(v)) : []
}

// ---- 本地状态：list 是 props.modelValue 的深拷贝 ----
const list = ref<RowActionConfig[]>(cloneList(props.modelValue))
const activeIndex = ref<number>(list.value.length ? 0 : -1)
const current = computed<RowActionConfig | undefined>(() =>
  activeIndex.value >= 0 ? list.value[activeIndex.value] : undefined
)

// 本组件自己 emit 出去的变更，会让父组件把新数组写回 props.modelValue，
// 从而触发下面这个 props watch；用这个标记跳过"回声"，避免无限互相同步。
let suppressNextPropSync = false

watch(
  list,
  (v) => {
    suppressNextPropSync = true
    emit('update:modelValue', cloneList(v))
  },
  { deep: true }
)

watch(
  () => props.modelValue,
  (v) => {
    if (suppressNextPropSync) {
      suppressNextPropSync = false
      return
    }
    // 外部真正替换了 modelValue（如切换了正在编辑的页面），重新同步本地副本
    list.value = cloneList(v)
    activeIndex.value = list.value.length ? 0 : -1
  }
)

// ---- 下拉候选 ----
const fieldOptions = computed(() =>
  props.fields.map((f) => ({ label: `${f.label}（${f.fieldName}）`, value: f.fieldName }))
)

const roleStore = useRoleStore()
const roleOptions = computed(() => roleStore.options)

const webhookRules = ref<WebhookRule[]>([])
/** 只列出手动触发的规则——其它事件类型的规则语义上不该被按钮点名调用 */
const manualRules = computed(() => webhookRules.value.filter((r) => r.triggerEvent === 'manual'))

const scanTasks = ref<AiScanTask[]>([])

onMounted(async () => {
  if (roleStore.options.length === 0) {
    roleStore.loadOptions().catch(() => {
      // 角色候选加载失败不影响本编辑器主体功能，静默降级
    })
  }
  try {
    webhookRules.value = await getWebhookRules()
  } catch {
    // 非 2xx 响应已由全局请求拦截器统一弹出中文错误提示，这里不重复弹
  }
  try {
    scanTasks.value = await getScanTasks()
  } catch {
    // 同上
  }
})

// ---- 列表操作 ----
function addAction() {
  const action: RowActionConfig = {
    id: `ra-${uuidv4().slice(0, 8)}`,
    label: '',
    actionType: 'webhook',
    enabled: true,
  }
  list.value.push(action)
  activeIndex.value = list.value.length - 1
}

function removeAction(i: number) {
  list.value.splice(i, 1)
  if (!list.value.length) {
    activeIndex.value = -1
  } else if (activeIndex.value >= list.value.length) {
    activeIndex.value = list.value.length - 1
  }
}

function moveUp(i: number) {
  if (i <= 0 || i >= list.value.length) return
  const arr = list.value
  ;[arr[i - 1], arr[i]] = [arr[i], arr[i - 1]]
  if (activeIndex.value === i) activeIndex.value = i - 1
  else if (activeIndex.value === i - 1) activeIndex.value = i
}

function moveDown(i: number) {
  moveUp(i + 1)
}

// ---- 显示条件：读写 current.visibleWhen ----
const conditionField = computed<string>({
  get: () => current.value?.visibleWhen?.field ?? '',
  set: (v) => {
    const item = current.value
    if (!item) return
    if (!v) {
      item.visibleWhen = undefined
      return
    }
    item.visibleWhen = {
      field: v,
      operator: item.visibleWhen?.operator ?? 'eq',
      value: item.visibleWhen?.value,
    }
  },
})

const conditionOperator = computed<RowActionCondition['operator']>({
  get: () => current.value?.visibleWhen?.operator ?? 'eq',
  set: (v) => {
    const vw = current.value?.visibleWhen
    if (!vw) return
    const prevValue = vw.value
    vw.operator = v
    if (NO_VALUE_OPERATORS.includes(v)) {
      // empty/notEmpty 不需要值
      vw.value = undefined
    } else if (v === 'in' || v === 'notIn') {
      // eq/ne 用的是标量字符串；切到 in/notIn 后求值器要求数组（见
      // src/utils/rowActionCondition.ts 与后端同款求值器：非数组直接判负），
      // 不转换的话条件会静默失效。已经是数组（比如从另一个 in/notIn 切过来）则保持不变。
      if (typeof prevValue === 'string') {
        vw.value = prevValue
          ? prevValue.split(',').map((s) => s.trim()).filter(Boolean)
          : []
      }
    } else {
      // 切回 eq/ne：数组 -> 标量。多个已选值收窄成单值时语义不明确
      // （逗号拼接成一个字符串不对应任何真实字段值，等于让条件必然为假），
      // 取第一个已选值更符合"多选收窄成单选"的直觉，用户可以再手动确认/修改。
      if (Array.isArray(prevValue)) {
        vw.value = prevValue[0] ?? ''
      }
    }
  },
})

const conditionValueText = computed<string>({
  get: () => {
    const val = current.value?.visibleWhen?.value
    if (Array.isArray(val)) return val.join(',')
    return val ?? ''
  },
  set: (v) => {
    const vw = current.value?.visibleWhen
    if (!vw) return
    if (vw.operator === 'in' || vw.operator === 'notIn') {
      vw.value = v.split(',').map((s) => s.trim()).filter(Boolean)
    } else {
      vw.value = v
    }
  },
})

// ---- actionType 切到 aiTask 时，清空 webhook 专属的执行结果配置 ----
// 用 id 判断是否为"同一个动作的类型被改了"，与"只是切换了选中的另一个动作"区分开，
// 否则单纯点击列表切换选中项也会因为两个动作 actionType 不同而被误判成"类型切换"。
//
// lastActionId 的刷新专门用一个只依赖 activeIndex 的 watch 来做，不能放在
// actionType 的 watch 里顺带更新——那样会有个漏洞：如果连续选中的两个动作
// actionType 恰好相同（比如都是 webhook），watch(actionType) 的值前后不变，
// 根本不会触发，lastActionId 就没能跟着刷新到新选中的动作；等用户接下来真的把
// 这个新选中动作的类型改掉时，代码会拿一个"上上个动作"的 stale id 去比较，
// 误判成"只是切换了选中项"从而漏清空。（code review 发现 1 的回归用例见测试文件。）
let lastActionId: string | undefined
watch(
  activeIndex,
  () => {
    lastActionId = current.value?.id
  },
  { immediate: true }
)

watch(
  () => current.value?.actionType,
  (newType) => {
    const item = current.value
    if (!item) return
    if (item.id !== lastActionId) return
    if (newType === 'aiTask') {
      item.statusField = undefined
      item.runningValue = undefined
      item.doneValue = undefined
      item.failedValue = undefined
      item.responseMapping = undefined
    }
  }
)

// ---- 响应字段映射（仅 webhook） ----
function addMappingRow() {
  const item = current.value
  if (!item) return
  if (!item.responseMapping) item.responseMapping = []
  item.responseMapping.push({ jsonKey: '', column: '', required: false })
}

function removeMappingRow(i: number) {
  current.value?.responseMapping?.splice(i, 1)
}

// ---- 参数表单 ----
function addParamField() {
  const item = current.value
  if (!item) return
  if (!item.paramFields) item.paramFields = []
  item.paramFields.push({
    id: `pf-${uuidv4().slice(0, 8)}`,
    label: '',
    fieldName: '',
    controlType: 'text',
    required: false,
    order: item.paramFields.length,
  })
}

function removeParamField(i: number) {
  current.value?.paramFields?.splice(i, 1)
}

defineExpose({
  list,
  activeIndex,
  current,
  fieldOptions,
  addAction,
  removeAction,
  moveUp,
  moveDown,
  addMappingRow,
  removeMappingRow,
  addParamField,
  removeParamField,
})
</script>

<style scoped lang="scss">
.row-actions-editor {
  display: flex;
  gap: 16px;
  align-items: flex-start;
}

.ra-list {
  width: 240px;
  flex-shrink: 0;

  .el-button {
    margin-bottom: 8px;
    width: 100%;
  }
}

.ra-items {
  list-style: none;
  margin: 0;
  padding: 0;
  border: 1px solid var(--el-border-color);
  border-radius: 4px;
  overflow: hidden;

  li {
    padding: 8px 10px;
    border-bottom: 1px solid var(--el-border-color-lighter);
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;

    &:last-child {
      border-bottom: none;
    }

    &:hover {
      background: var(--el-fill-color-light);
    }

    &.active {
      background: var(--el-color-primary-light-9);
    }

    .ra-name {
      flex: 1;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .ra-ops {
      display: flex;
      flex-basis: 100%;
      justify-content: flex-end;
    }
  }
}

.ra-detail {
  flex: 1;
  min-width: 0;
}

.ra-hint {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  margin-top: 4px;
}

.ra-condition {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.ra-map-row,
.ra-param-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.ra-arrow {
  color: var(--el-text-color-secondary);
}
</style>
