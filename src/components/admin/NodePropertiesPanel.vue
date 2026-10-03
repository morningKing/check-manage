<script setup lang="ts">
/**
 * 属性面板（spec §4.1/§4.2）：双模式。
 * - node 模式：编辑 name/prompt_template/model/timeout_sec/join_policy/priority，
 *   字段变更以 update:node 事件上抛，由编辑器写回 Vue Flow 节点 data。
 * - edge 模式：编辑条件边 condition {field, op, value}，以 update:edge(edgeId,
 *   condition) 上抛（条件整体替换，编辑器负责写回 data.condition 与 label）；
 *   另有删除该边（remove-edge）。
 */
import { computed } from 'vue'

export interface PanelSelection {
  type: 'node' | 'edge'
  data: Record<string, any>
}

const props = defineProps<{
  selected: PanelSelection | null
}>()
const emit = defineEmits<{
  (e: 'update:node', fields: Record<string, any>): void
  (e: 'update:edge', edgeId: string, condition: Record<string, any>): void
  (e: 'remove-edge', edgeId: string): void
}>()

const nodeData = computed(() => (props.selected?.type === 'node' ? props.selected.data : null))
const edgeData = computed(() => (props.selected?.type === 'edge' ? props.selected.data : null))

const isAgent = computed(() => nodeData.value?.kind === 'agent')
const isJoin = computed(() => nodeData.value?.kind === 'join')

const conditionField = computed(() => edgeData.value?.condition?.field ?? '')
const conditionOp = computed(() => edgeData.value?.condition?.op ?? 'contains')
const conditionValue = computed(() => edgeData.value?.condition?.value ?? '')

function update(key: string, value: any) {
  emit('update:node', { [key]: value })
}

/** 条件编辑：在现有 condition 基础上合并单字段后整体上抛 */
function updateCondition(patch: Record<string, any>) {
  if (!edgeData.value) return
  emit('update:edge', edgeData.value.id, {
    field: '', op: 'contains', value: '',
    ...(edgeData.value.condition ?? {}),
    ...patch,
  })
}

function removeEdge() {
  if (!edgeData.value) return
  emit('remove-edge', edgeData.value.id)
}
</script>

<template>
  <div v-if="nodeData" class="node-props">
    <h4>属性 — {{ nodeData.kind }}</h4>

    <el-form label-position="top" size="small">
      <el-form-item label="名称">
        <el-input :model-value="nodeData.label" @update:model-value="(v: string) => update('name', v)" />
      </el-form-item>

      <template v-if="isAgent">
        <el-form-item label="Prompt 模板">
          <el-input type="textarea" :rows="6" :model-value="nodeData.prompt_template"
                    @update:model-value="(v: string) => update('prompt_template', v)" />
        </el-form-item>
        <el-form-item label="模型">
          <el-input :model-value="nodeData.model" placeholder="留空用默认"
                    @update:model-value="(v: string) => update('model', v)" />
        </el-form-item>
        <el-form-item label="超时（秒）">
          <el-input-number :model-value="nodeData.timeout_sec" :min="1"
                           @update:model-value="(v: number | undefined) => update('timeout_sec', v)" />
        </el-form-item>
      </template>

      <template v-if="isJoin">
        <el-form-item label="Join 策略">
          <el-select :model-value="nodeData.join_policy ?? 'all_success'"
                     @update:model-value="(v: string) => update('join_policy', v)">
            <el-option value="all_success" label="全部成功" />
            <el-option value="any_success" label="任一成功" />
          </el-select>
        </el-form-item>
      </template>

      <el-form-item label="优先级">
        <el-input-number :model-value="nodeData.priority ?? 0"
                         @update:model-value="(v: number | undefined) => update('priority', v)" />
      </el-form-item>
    </el-form>
  </div>

  <div v-else-if="edgeData" class="node-props">
    <h4>条件边 — {{ edgeData.source }} → {{ edgeData.target }}</h4>

    <el-form label-position="top" size="small">
      <el-form-item label="条件字段 field">
        <el-input :model-value="conditionField" placeholder="如 text"
                  @update:model-value="(v: string) => updateCondition({ field: v })" />
      </el-form-item>
      <el-form-item label="条件 op">
        <el-select :model-value="conditionOp"
                   @update:model-value="(v: string) => updateCondition({ op: v })">
          <el-option value="contains" label="contains" />
          <el-option value="not_contains" label="not_contains" />
          <el-option value="==" label="==" />
          <el-option value=">" label=">" />
          <el-option value=">=" label=">=" />
          <el-option value="<" label="<" />
          <el-option value="<=" label="<=" />
        </el-select>
      </el-form-item>
      <el-form-item label="条件值 value">
        <el-input :model-value="conditionValue" placeholder="如 ok"
                  @update:model-value="(v: string) => updateCondition({ value: v })" />
      </el-form-item>
    </el-form>

    <el-button type="danger" size="small" @click="removeEdge">删除该边</el-button>
  </div>

  <div v-else class="node-props node-props--empty">
    点击节点或边查看属性
  </div>
</template>

<style scoped>
.node-props {
  width: 280px; padding: 12px; border-left: 1px solid #ebeef5; overflow-y: auto;
}
.node-props--empty { color: #909399; }
</style>
