<script setup lang="ts">
/** 内置 MCP 工具治理抽屉：分组清单 + 角色×工具矩阵开关（spec §7.1）。
 *  改动即时生效（≤3s）；保存=全量 PUT；组标题行 4 个角色微开关批量切整组。 */
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getMcpTools, putMcpToolRules,
  type McpGovernance, type McpToolRuleInput,
} from '@/api/aiMcpServers'

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void }>()

const loading = ref(false)
const saving = ref(false)
/** 本地可编辑副本：groups → tools[i].rules[role]（直接改，保存时扁平化） */
const editable = ref<McpGovernance | null>(null)
/** 基线快照须为响应式：保存后赋值要触发 dirty 重算（普通变量不会）。 */
const baseline = ref('')

const dirty = computed(() =>
  !!editable.value && JSON.stringify(editable.value.groups) !== baseline.value)

watch(() => props.modelValue, async (open) => {
  if (!open || editable.value) return
  loading.value = true
  try {
    editable.value = await getMcpTools()
    baseline.value = JSON.stringify(editable.value.groups)
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.error || '加载工具清单失败')
  } finally { loading.value = false }
}, { immediate: true })

function setToolRole(tool: { rules: Record<string, boolean> }, role: string, v: boolean) {
  tool.rules[role] = v
}

/** 组×角色批量：目标值取「该组该角色是否未全开」，整组一起切。 */
function toggleGroupRole(group: McpGovernance['groups'][number], role: string) {
  const target = !group.tools.every(t => t.rules[role])
  for (const t of group.tools) t.rules[role] = target
}

function groupAllOn(group: McpGovernance['groups'][number], role: string) {
  return group.tools.length > 0 && group.tools.every(t => t.rules[role])
}

function flatten(): McpToolRuleInput[] {
  const out: McpToolRuleInput[] = []
  for (const g of editable.value?.groups || [])
    for (const t of g.tools)
      for (const r of editable.value?.roles || [])
        out.push({ role: r.role, tool_name: t.name, allowed: !!t.rules[r.role] })
  return out
}

async function save() {
  if (!editable.value) return
  saving.value = true
  try {
    const res = await putMcpToolRules(flatten())
    ElMessage.success(`已保存（${res.updated} 项变更，≤3s 内对新调用生效）`)
    baseline.value = JSON.stringify(editable.value.groups)
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.error || '保存失败')
  } finally { saving.value = false }
}

async function beforeClose(done: () => void) {
  if (!dirty.value) { done(); return }
  try {
    await ElMessageBox.confirm('有未保存的改动，确认放弃？', '未保存改动',
      { confirmButtonText: '放弃', cancelButtonText: '继续编辑', type: 'warning' })
    done()
  } catch { /* 继续编辑 */ }
}
</script>

<template>
  <el-drawer :model-value="props.modelValue" title="工具治理（角色 × 工具）" size="760px"
             :before-close="beforeClose" @update:model-value="emit('update:modelValue', $event)">
    <div v-loading="loading">
      <p class="hint">
        开关按角色控制工具可用性，保存后<b>即时生效</b>（≤3s 内对新调用拒绝）；
        已打开会话的工具列表要等其重连后刷新展示。
      </p>
      <el-alert v-if="editable && !editable.groups.length" type="info" :closable="false"
                title="暂无工具清单（mcp-server 未同步快照）——请确认 3003 已随新版本重启。" />
      <template v-for="g in editable?.groups || []" :key="g.id">
        <div class="grp">
          <div class="grp__head">
            <span class="grp__name">{{ g.name }}</span>
            <div class="grp__batch">
              <template v-for="r in editable!.roles" :key="r.role">
                <el-switch size="small" :title="`整组 × ${r.label}`"
                           :model-value="groupAllOn(g, r.role)"
                           @change="toggleGroupRole(g, r.role)" />
                <span class="grp__batch-label">{{ r.label }}</span>
              </template>
            </div>
          </div>
          <div v-for="t in g.tools" :key="t.name" class="tool-row">
            <div class="tool-row__name">
              <span class="mono">{{ t.name }}</span>
              <div class="tool-desc">{{ t.description }}</div>
            </div>
            <el-tag :type="t.access === 'write' ? 'danger' : 'info'" size="small" class="tool-row__access">
              {{ t.access === 'write' ? '写' : '读' }}
            </el-tag>
            <div class="tool-row__switches">
              <label v-for="r in editable!.roles" :key="r.role" class="tool-row__cell">
                <el-switch :model-value="!!t.rules[r.role]"
                           @change="(v: string | number | boolean) => setToolRole(t, r.role, !!v)" />
                <span class="tool-row__cell-label">{{ r.label }}</span>
              </label>
            </div>
          </div>
        </div>
      </template>
      <div v-if="editable?.groups.length" class="footer">
        <span class="footer__dirty">{{ dirty ? '有未保存改动' : '' }}</span>
        <el-button :disabled="!dirty" :loading="saving" type="primary" @click="save">
          保存（全量提交）
        </el-button>
      </div>
    </div>
  </el-drawer>
</template>

<style scoped>
.hint { margin: 0 0 12px; font-size: 13px; color: var(--el-text-color-secondary); }
.grp { margin-bottom: 18px; }
.grp__head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; flex-wrap: wrap; }
.grp__name { font-weight: 600; }
.grp__batch { display: flex; align-items: center; gap: 4px; margin-left: auto; }
.grp__batch-label { font-size: 12px; color: var(--el-text-color-secondary); margin-right: 4px; }
.mono { font-family: var(--el-font-family-mono, monospace); font-size: 12px; }
.tool-desc { font-size: 12px; color: var(--el-text-color-secondary); white-space: normal; }
.tool-row {
  display: flex; align-items: flex-start; gap: 10px;
  padding: 6px 0; border-bottom: 1px solid var(--el-border-color-lighter);
}
.tool-row__name { flex: 1; min-width: 0; }
.tool-row__access { flex-shrink: 0; margin-top: 2px; }
.tool-row__switches { display: flex; gap: 10px; flex-shrink: 0; }
.tool-row__cell { display: flex; flex-direction: column; align-items: center; gap: 2px; }
.tool-row__cell-label { font-size: 11px; color: var(--el-text-color-secondary); }
.footer { display: flex; align-items: center; gap: 12px; margin-top: 8px; }
.footer__dirty { color: var(--el-color-warning); font-size: 12px; }
</style>
