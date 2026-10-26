<script setup lang="ts">
/** 内置 MCP 工具治理抽屉：角色×工具矩阵（spec §7.1）。
 *  矩阵布局：全局吸顶列头（工具/读写/4 角色列）+ 分组节（组头行内嵌整组批量
 *  开关，与角色列对齐）；搜索过滤、组折叠、变更单元格高亮、改动计数、重置；
 *  吸底保存栏常驻可见。保存=全量 PUT，即时生效（≤3s）。 */
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getMcpTools, putMcpToolRules,
  type McpGovernance, type McpGovernanceGroup, type McpGovernanceTool, type McpToolRuleInput,
} from '@/api/aiMcpServers'

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void }>()

const loading = ref(false)
const saving = ref(false)
/** 本地可编辑副本：groups → tools[i].rules[role]（直接改，保存时扁平化） */
const editable = ref<McpGovernance | null>(null)
/** 基线（响应式）：dirty 比对与单元格变更高亮、重置的还原源 */
const baseline = ref<McpGovernance | null>(null)
const keyword = ref('')
const collapsed = ref<Set<string>>(new Set())

function snapshot(g: McpGovernance | null): McpGovernance | null {
  return g ? JSON.parse(JSON.stringify(g)) : null
}

const dirty = computed(() =>
  !!editable.value && !!baseline.value &&
  JSON.stringify(editable.value.groups) !== JSON.stringify(baseline.value.groups))

/** 与基线不同的开关格数（保存栏改动计数） */
const changedCount = computed(() => {
  if (!editable.value || !baseline.value) return 0
  const base = new Map<string, boolean>()
  for (const g of baseline.value.groups)
    for (const t of g.tools)
      for (const [role, v] of Object.entries(t.rules)) base.set(`${t.name}::${role}`, v)
  let n = 0
  for (const g of editable.value.groups)
    for (const t of g.tools)
      for (const [role, v] of Object.entries(t.rules))
        if (base.get(`${t.name}::${role}`) !== v) n += 1
  return n
})

function isChanged(t: McpGovernanceTool, role: string): boolean {
  for (const g of baseline.value?.groups || []) {
    const b = g.tools.find(x => x.name === t.name)
    if (b) return b.rules[role] !== t.rules[role]
  }
  return false
}

/** 搜索过滤：按工具名/描述匹配，组内无命中则整组隐藏 */
const filteredGroups = computed(() => {
  if (!editable.value) return []
  const kw = keyword.value.trim().toLowerCase()
  if (!kw) return editable.value.groups
  return editable.value.groups
    .map(g => ({ ...g, tools: g.tools.filter(t =>
      t.name.toLowerCase().includes(kw) || (t.description || '').toLowerCase().includes(kw)) }))
    .filter(g => g.tools.length > 0)
})

const totalToolCount = computed(() =>
  (editable.value?.groups || []).reduce((n, g) => n + g.tools.length, 0))
const visibleToolCount = computed(() =>
  filteredGroups.value.reduce((n, g) => n + g.tools.length, 0))

watch(() => props.modelValue, async (open) => {
  if (!open || editable.value) return
  loading.value = true
  try {
    editable.value = await getMcpTools()
    baseline.value = snapshot(editable.value)
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.error || '加载工具清单失败')
  } finally { loading.value = false }
}, { immediate: true })

function setToolRole(tool: McpGovernanceTool, role: string, v: boolean) {
  tool.rules[role] = v
}

function toggleCollapse(gid: string) {
  const s = new Set(collapsed.value)
  if (s.has(gid)) s.delete(gid); else s.add(gid)
  collapsed.value = s
}

/** 组×角色批量：目标值取「该组该角色是否未全开」，整组一起切。 */
function toggleGroupRole(group: McpGovernanceGroup, role: string) {
  const target = !group.tools.every(t => t.rules[role])
  for (const t of group.tools) t.rules[role] = target
}

function groupAllOn(group: McpGovernanceGroup, role: string) {
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
    baseline.value = snapshot(editable.value)
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.error || '保存失败')
  } finally { saving.value = false }
}

function resetChanges() {
  editable.value = snapshot(baseline.value)
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
  <el-drawer :model-value="props.modelValue" title="工具治理（角色 × 工具）" size="860px"
             :before-close="beforeClose" @update:model-value="emit('update:modelValue', $event)">
    <div v-loading="loading" class="gov">
      <p class="hint">
        开关按角色控制工具可用性，保存后<b>即时生效</b>（≤3s 内对新调用拒绝）；
        已打开会话的工具列表要等其重连后刷新展示。
      </p>
      <el-alert v-if="editable && !editable.groups.length" type="info" :closable="false"
                title="暂无工具清单（mcp-server 未同步快照）——请确认 3003 已随新版本重启。" />
      <template v-else-if="editable">
        <div class="gov-toolbar">
          <input v-model="keyword" class="gov-search" placeholder="搜索工具名或描述…" />
          <span class="gov-toolbar__meta">{{ visibleToolCount }} / {{ totalToolCount }} 个工具</span>
        </div>

        <div class="gov-grid gov-head">
          <span class="gov-col-tool">工具</span>
          <span class="gov-col-access">读写</span>
          <span v-for="r in editable.roles" :key="r.role" class="gov-col-role">{{ r.label }}</span>
        </div>

        <div v-for="g in filteredGroups" :key="g.id" class="gov-group">
          <div class="gov-grid gov-group__head">
            <button class="gov-group__toggle" type="button" @click="toggleCollapse(g.id)">
              <span class="gov-group__chevron">{{ collapsed.has(g.id) ? '▶' : '▼' }}</span>
              {{ g.name }}<span class="gov-group__count">（{{ g.tools.length }}）</span>
            </button>
            <span class="gov-col-access" />
            <div v-for="r in editable.roles" :key="r.role" class="gov-col-role">
              <el-switch size="small" :title="`整组 × ${r.label}`"
                         :model-value="groupAllOn(g, r.role)"
                         @change="toggleGroupRole(g, r.role)" />
            </div>
          </div>
          <template v-if="!collapsed.has(g.id)">
            <div v-for="t in g.tools" :key="t.name" class="gov-grid gov-row">
              <div class="gov-col-tool">
                <span class="mono">{{ t.name }}</span>
                <div class="tool-desc" :title="t.description">{{ t.description }}</div>
              </div>
              <div class="gov-col-access">
                <el-tag :type="t.access === 'write' ? 'danger' : 'info'" size="small">
                  {{ t.access === 'write' ? '写' : '读' }}
                </el-tag>
              </div>
              <div v-for="r in editable.roles" :key="r.role"
                   class="gov-col-role" data-test="gov-cell"
                   :data-tool="t.name" :data-role="r.role"
                   :class="{ 'is-changed': isChanged(t, r.role) }">
                <el-switch :model-value="!!t.rules[r.role]"
                           @change="(v: string | number | boolean) => setToolRole(t, r.role, !!v)" />
              </div>
            </div>
          </template>
        </div>

        <div v-if="!filteredGroups.length" class="gov-empty">
          没有匹配「{{ keyword }}」的工具
        </div>
      </template>

      <div class="gov-footer">
        <span class="gov-footer__dirty" :class="{ on: dirty }">
          {{ dirty ? `有未保存改动（${changedCount} 项）` : '与线上一致' }}
        </span>
        <div class="gov-footer__actions">
          <el-button :disabled="!dirty" @click="resetChanges">重置</el-button>
          <el-button :disabled="!dirty" :loading="saving" type="primary" @click="save">保存</el-button>
        </div>
      </div>
    </div>
  </el-drawer>
</template>

<style scoped>
.gov { display: flex; flex-direction: column; min-height: 100%; }
.hint { margin: 0 0 10px; font-size: 13px; color: var(--el-text-color-secondary); }
.mono { font-family: var(--el-font-family-mono, monospace); font-size: 12px; }

.gov-toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.gov-search {
  flex: 1; max-width: 320px; padding: 5px 10px; font-size: 13px;
  border: 1px solid var(--el-border-color); border-radius: 6px; outline: none;
  background: var(--el-bg-color); color: var(--el-text-color-primary);
}
.gov-search:focus { border-color: var(--el-color-primary); }
.gov-toolbar__meta { font-size: 12px; color: var(--el-text-color-secondary); }

/* 矩阵网格：工具列弹性 + 读写列 + 4 角色等宽列——所有行共用同一模板保证对齐 */
.gov-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 52px repeat(4, 76px);
  align-items: center; column-gap: 4px;
}
.gov-head {
  position: sticky; top: 0; z-index: 2;
  background: var(--el-bg-color);
  padding: 6px 0; border-bottom: 1px solid var(--el-border-color);
  font-weight: 600; font-size: 13px;
}
.gov-col-role { text-align: center; }

.gov-group { border-bottom: 1px solid var(--el-border-color-lighter); }
.gov-group__head { padding: 4px 0; background: var(--el-fill-color-lighter); }
.gov-group__toggle {
  display: flex; align-items: center; gap: 4px;
  border: none; background: none; cursor: pointer; padding: 2px 0;
  font-size: 13px; font-weight: 600; color: var(--el-text-color-primary);
  text-align: left; min-width: 0;
}
.gov-group__chevron { font-size: 10px; color: var(--el-text-color-secondary); }
.gov-group__count { font-weight: 400; color: var(--el-text-color-secondary); }

.gov-row { padding: 5px 0; border-bottom: 1px solid var(--el-border-color-lighter); }
.gov-row:last-child { border-bottom: none; }
.tool-desc {
  font-size: 12px; color: var(--el-text-color-secondary);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.gov-col-role.is-changed { border-radius: 6px; background: var(--el-color-warning-light-9); }

.gov-empty { padding: 20px 0; text-align: center; color: var(--el-text-color-secondary); font-size: 13px; }

.gov-footer {
  position: sticky; bottom: 0; z-index: 2;
  display: flex; align-items: center; gap: 12px;
  margin-top: auto; padding: 10px 0 2px;
  background: var(--el-bg-color); border-top: 1px solid var(--el-border-color);
}
.gov-footer__dirty { font-size: 12px; color: var(--el-text-color-secondary); }
.gov-footer__dirty.on { color: var(--el-color-warning); font-weight: 600; }
.gov-footer__actions { margin-left: auto; display: flex; gap: 8px; }
</style>
