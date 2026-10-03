<script setup lang="ts">
/**
 * 节点属性面板（spec §4.1/§4.2）：选中节点 → 编辑 name/prompt_template/
 * model/timeout_sec/join_policy/priority。字段变更以 update:node 事件上抛，
 * 由编辑器写回 Vue Flow 节点 data。
 */
import { computed } from 'vue'

const props = defineProps<{
  node: Record<string, any> | null
}>()
const emit = defineEmits<{ (e: 'update:node', fields: Record<string, any>): void }>()

const isAgent = computed(() => props.node?.kind === 'agent')
const isJoin = computed(() => props.node?.kind === 'join')

function update(key: string, value: any) {
  emit('update:node', { [key]: value })
}
</script>

<template>
  <div v-if="node" class="node-props">
    <h4>属性 — {{ node.kind }}</h4>

    <el-form label-position="top" size="small">
      <el-form-item label="名称">
        <el-input :model-value="node.label" @update:model-value="(v: string) => update('name', v)" />
      </el-form-item>

      <template v-if="isAgent">
        <el-form-item label="Prompt 模板">
          <el-input type="textarea" :rows="6" :model-value="node.prompt_template"
                    @update:model-value="(v: string) => update('prompt_template', v)" />
        </el-form-item>
        <el-form-item label="模型">
          <el-input :model-value="node.model" placeholder="留空用默认"
                    @update:model-value="(v: string) => update('model', v)" />
        </el-form-item>
        <el-form-item label="超时（秒）">
          <el-input-number :model-value="node.timeout_sec" :min="1"
                           @update:model-value="(v: number | undefined) => update('timeout_sec', v)" />
        </el-form-item>
      </template>

      <template v-if="isJoin">
        <el-form-item label="Join 策略">
          <el-select :model-value="node.join_policy ?? 'all_success'"
                     @update:model-value="(v: string) => update('join_policy', v)">
            <el-option value="all_success" label="全部成功" />
            <el-option value="any_success" label="任一成功" />
          </el-select>
        </el-form-item>
      </template>

      <el-form-item label="优先级">
        <el-input-number :model-value="node.priority ?? 0"
                         @update:model-value="(v: number | undefined) => update('priority', v)" />
      </el-form-item>
    </el-form>
  </div>
  <div v-else class="node-props node-props--empty">
    点击节点查看属性
  </div>
</template>

<style scoped>
.node-props {
  width: 280px; padding: 12px; border-left: 1px solid #ebeef5; overflow-y: auto;
}
.node-props--empty { color: #909399; }
</style>
