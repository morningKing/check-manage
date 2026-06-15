<!-- src/views/workflow/WorkflowEditor.vue
     工作流全屏编辑页：顶部工具栏（名称/描述/启用 + 保存/返回）+ 主区大画布 + 右侧阶段属性面板。
     线性流水线模型：阶段顺序链（推进→下一、回退→上一）。画布用于查看与点选定位，编辑在右侧面板。 -->
<template>
  <div class="wf-editor" v-loading="loading">
    <header class="wf-editor__bar">
      <div class="wf-editor__meta">
        <el-input v-model="form.name" placeholder="工作流名称" class="wf-name" />
        <el-input v-model="form.description" placeholder="描述（可选）" class="wf-desc" />
        <span class="wf-enable">启用 <el-switch v-model="form.enabled" /></span>
      </div>
      <div class="wf-editor__ops">
        <el-button @click="goBack">返回</el-button>
        <el-button type="primary" :loading="saving" @click="onSave">保存</el-button>
      </div>
    </header>

    <div class="wf-editor__body">
      <section class="wf-editor__canvas">
        <div class="wf-editor__canvas-ops">
          <span class="wf-editor__hint">点节点编辑该阶段 · 推进=下一阶段 / 回退=上一阶段</span>
          <el-button size="small" type="primary" plain @click="addStage">+ 添加阶段</el-button>
        </div>
        <WorkflowGraph
          v-if="form.stages.length"
          class="wf-editor__graph"
          :stages="form.stages"
          :selected-id="selectedId"
          @select="selectStage"
        />
        <el-empty v-else description="还没有阶段，点击「添加阶段」开始" />
      </section>

      <aside class="wf-editor__panel">
        <template v-if="selectedStage">
          <div class="wf-panel__head">
            <span class="wf-panel__title">阶段 {{ selectedIndex + 1 }}</span>
            <div class="wf-panel__ops">
              <el-button link :disabled="selectedIndex === 0" @click="moveStage(selectedIndex, -1)">上移</el-button>
              <el-button link :disabled="selectedIndex === form.stages.length - 1" @click="moveStage(selectedIndex, 1)">下移</el-button>
              <el-button link type="danger" @click="removeStage(selectedIndex)">删除</el-button>
            </div>
          </div>

          <el-form label-position="top" class="wf-panel__form">
            <el-form-item label="阶段名">
              <el-input v-model="selectedStage.name" placeholder="如 初审" />
            </el-form-item>
            <el-form-item label="绑定数据页">
              <el-select v-model="selectedStage.collection" filterable placeholder="选择数据页" style="width: 100%">
                <el-option v-for="c in collectionOptions" :key="c.value" :label="c.label" :value="c.value" />
              </el-select>
            </el-form-item>
            <el-form-item label="状态字段">
              <el-select
                v-if="fieldsOf(selectedStage.collection).length"
                v-model="selectedStage.statusField"
                filterable
                allow-create
                clearable
                placeholder="选择或输入字段名"
                style="width: 100%"
              >
                <el-option v-for="f in fieldsOf(selectedStage.collection)" :key="f.value" :label="f.label" :value="f.value" />
              </el-select>
              <el-input v-else v-model="selectedStage.statusField" placeholder="状态字段名" />
            </el-form-item>
            <el-form-item label="推进转换（当前状态 → 推进后状态）">
              <div class="wf-transition">
                <el-input v-model="advOf(selectedStage).from" placeholder="from" />
                <span class="arrow">→</span>
                <el-input v-model="advOf(selectedStage).to" placeholder="to" />
              </div>
            </el-form-item>
            <el-form-item label="回退转换（可选）">
              <div class="wf-transition">
                <el-input v-model="rejOf(selectedStage).from" placeholder="from" />
                <span class="arrow">→</span>
                <el-input v-model="rejOf(selectedStage).to" placeholder="to" />
              </div>
            </el-form-item>
            <el-form-item label="办理角色">
              <el-select v-model="selectedStage.assignedRoles" multiple filterable placeholder="可办理该阶段的角色" style="width: 100%">
                <el-option v-for="r in roleOptions" :key="r.id" :label="r.name" :value="r.id" />
              </el-select>
            </el-form-item>
            <el-form-item label="生成下游（字段映射）">
              <div class="wf-spawn">
                <div v-for="(m, mi) in spawnRows(selectedStage)" :key="mi" class="wf-map-row">
                  <el-input v-model="m.key" placeholder="目标字段" />
                  <span class="arrow">→</span>
                  <el-input v-model="m.value" placeholder="$source.字段 / $NOW / 字面量" />
                  <el-button link type="danger" @click="removeSpawnRow(selectedStage, mi)">删除</el-button>
                </div>
                <el-button size="small" @click="addSpawnRow(selectedStage)">+ 添加字段映射</el-button>
                <div class="wf-linkback">
                  <span class="wf-linkback__label">回链字段</span>
                  <el-input
                    :model-value="selectedStage.spawn?.linkBackField || ''"
                    placeholder="下游记录回写上游 id 的字段（可选）"
                    @update:model-value="(v: string) => setLinkBack(selectedStage!, v)"
                  />
                </div>
              </div>
            </el-form-item>
          </el-form>
        </template>
        <el-empty v-else description="选择画布中的阶段进行编辑，或先添加阶段" :image-size="60" />
      </aside>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useWorkflowStore } from '@/stores/workflow'
import { usePageConfigStore } from '@/stores/pageConfig'
import { useRoleStore } from '@/stores/role'
import type { WorkflowDefinition, WorkflowStage } from '@/types/workflow'
import WorkflowGraph from '@/components/workflow/WorkflowGraph.vue'

const route = useRoute()
const router = useRouter()
const store = useWorkflowStore()
const pageConfigStore = usePageConfigStore()
const roleStore = useRoleStore()

const LIST_PATH = '/admin/structure?tab=workflows'

const loading = ref(false)
const saving = ref(false)
const form = reactive<WorkflowDefinition>({ id: undefined, name: '', description: '', enabled: true, stages: [] })
const selectedId = ref<string | undefined>(undefined)

const selectedIndex = computed(() => form.stages.findIndex((s) => s.id === selectedId.value))
const selectedStage = computed<WorkflowStage | undefined>(() => form.stages[selectedIndex.value])

// ---- 选项 ----
const collectionOptions = computed(() =>
  pageConfigStore.pageConfigs.map((c) => ({ label: `${c.name}（${c.id.replace('page-', '')}）`, value: c.id.replace('page-', '') })),
)
const roleOptions = computed(() => roleStore.options)
function fieldsOf(collection: string): { label: string; value: string }[] {
  if (!collection) return []
  const config = pageConfigStore.pageConfigs.find((c) => c.id.replace('page-', '') === collection)
  if (!config) return []
  return config.fields.map((f) => ({ label: `${f.label}（${f.fieldName}）`, value: f.fieldName }))
}

// ---- 转换就地编辑（保证对象存在）----
function advOf(stage: WorkflowStage) {
  if (!stage.advanceTransition) stage.advanceTransition = { from: '', to: '' }
  return stage.advanceTransition
}
function rejOf(stage: WorkflowStage) {
  if (!stage.rejectTransition) stage.rejectTransition = { from: '', to: '' }
  return stage.rejectTransition
}

// ---- spawn 字段映射（行编辑态，按 stage.id 缓存）----
interface SpawnRow { key: string; value: string }
const spawnRowState = reactive<Record<string, SpawnRow[]>>({})
function spawnRows(stage: WorkflowStage): SpawnRow[] {
  if (!spawnRowState[stage.id]) {
    const mapping = stage.spawn?.fieldMapping || {}
    spawnRowState[stage.id] = Object.entries(mapping).map(([key, value]) => ({ key, value }))
  }
  return spawnRowState[stage.id]
}
function addSpawnRow(stage: WorkflowStage) {
  spawnRows(stage).push({ key: '', value: '' })
}
function removeSpawnRow(stage: WorkflowStage, i: number) {
  spawnRows(stage).splice(i, 1)
}
function setLinkBack(stage: WorkflowStage, v: string) {
  if (!stage.spawn) stage.spawn = { fieldMapping: {} }
  stage.spawn.linkBackField = v
}

// ---- 阶段增删改 ----
function blankStage(): WorkflowStage {
  return { id: `stage-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`, name: '', collection: '', statusField: '', assignedRoles: [] }
}
function addStage() {
  const s = blankStage()
  form.stages.push(s)
  selectedId.value = s.id
}
function selectStage(id: string) {
  selectedId.value = id
}
function moveStage(idx: number, dir: number) {
  const target = idx + dir
  if (target < 0 || target >= form.stages.length) return
  const arr = form.stages
  ;[arr[idx], arr[target]] = [arr[target], arr[idx]]
}
function removeStage(idx: number) {
  const removed = form.stages[idx]
  form.stages.splice(idx, 1)
  if (removed) delete spawnRowState[removed.id]
  selectedId.value = form.stages[Math.min(idx, form.stages.length - 1)]?.id
}

// ---- 收敛为可提交定义（清洗空转换/空映射）----
function buildPayload(): WorkflowDefinition {
  const stages: WorkflowStage[] = form.stages.map((stage) => {
    const out: WorkflowStage = { id: stage.id, name: stage.name, collection: stage.collection }
    if (stage.statusField) out.statusField = stage.statusField
    const adv = stage.advanceTransition
    if (adv && (adv.from || adv.to)) out.advanceTransition = { from: adv.from, to: adv.to }
    const rej = stage.rejectTransition
    if (rej && (rej.from || rej.to)) out.rejectTransition = { from: rej.from, to: rej.to }
    if (stage.assignedRoles && stage.assignedRoles.length) out.assignedRoles = stage.assignedRoles
    const rows = spawnRowState[stage.id]
    const mapping: Record<string, string> = {}
    if (rows) {
      for (const r of rows) if (r.key.trim()) mapping[r.key.trim()] = r.value
    } else if (stage.spawn?.fieldMapping) {
      Object.assign(mapping, stage.spawn.fieldMapping)
    }
    const linkBack = stage.spawn?.linkBackField
    if (Object.keys(mapping).length || linkBack) {
      out.spawn = { fieldMapping: mapping }
      if (linkBack) out.spawn.linkBackField = linkBack
    }
    return out
  })
  return { id: form.id, name: form.name, description: form.description || undefined, enabled: form.enabled, stages }
}

async function onSave() {
  if (!form.name.trim()) {
    ElMessage.warning('请填写工作流名称')
    return
  }
  for (const s of form.stages) {
    if (!s.name.trim() || !s.collection) {
      ElMessage.warning('每个阶段都需要阶段名与绑定数据页')
      selectedId.value = s.id
      return
    }
  }
  saving.value = true
  try {
    const saved = await store.save(buildPayload())
    if (saved.warnings && saved.warnings.length) {
      ElMessage.warning({ message: '已保存，但存在配置问题：\n' + saved.warnings.join('\n'), duration: 6000 })
    } else {
      ElMessage.success('保存成功')
    }
    router.push(LIST_PATH)
  } catch {
    /* 全局拦截器已提示 */
  } finally {
    saving.value = false
  }
}

function goBack() {
  router.push(LIST_PATH)
}

async function ensureOptions() {
  if (!pageConfigStore.pageConfigs.length) {
    try { await pageConfigStore.fetchPageConfigs() } catch { /* 非致命 */ }
  }
  try { await roleStore.loadOptions() } catch { /* 非致命 */ }
}

onMounted(async () => {
  loading.value = true
  try {
    await ensureOptions()
    const id = route.params.id as string | undefined
    if (id) {
      if (!store.definitions.length) await store.loadDefinitions()
      const found = store.definitions.find((d) => d.id === id)
      if (found) {
        const clone: WorkflowDefinition = JSON.parse(JSON.stringify(found))
        Object.assign(form, { id: clone.id, name: clone.name, description: clone.description || '', enabled: clone.enabled, stages: clone.stages || [] })
      } else {
        ElMessage.error('工作流不存在')
        router.push(LIST_PATH)
        return
      }
    }
    selectedId.value = form.stages[0]?.id
  } finally {
    loading.value = false
  }
})
</script>

<style scoped lang="scss">
.wf-editor {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 130px);
  min-height: 520px;
}
.wf-editor__bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  padding: 12px 16px;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  margin-bottom: 12px;
}
.wf-editor__meta {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 1;
  min-width: 0;
}
.wf-name { max-width: 260px; }
.wf-desc { max-width: 360px; }
.wf-enable {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
  color: var(--el-text-color-regular);
  font-size: 14px;
}
.wf-editor__body {
  display: flex;
  gap: 12px;
  flex: 1;
  min-height: 0;
}
.wf-editor__canvas {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  background: var(--el-bg-color);
  overflow: hidden;
}
.wf-editor__canvas-ops {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}
.wf-editor__hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.wf-editor__graph {
  flex: 1;
  min-height: 0;
  border: none;
  border-radius: 0;
}
.wf-editor__panel {
  width: 360px;
  flex: none;
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  background: var(--el-bg-color);
  padding: 14px 16px;
  overflow-y: auto;
}
.wf-panel__head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}
.wf-panel__title {
  font-weight: 600;
  font-size: 15px;
}
.wf-panel__form :deep(.el-form-item) {
  margin-bottom: 14px;
}
.wf-transition {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}
.arrow { color: var(--el-text-color-secondary); }
.wf-spawn {
  width: 100%;
}
.wf-map-row {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
}
.wf-linkback {
  margin-top: 8px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.wf-linkback__label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  white-space: nowrap;
}
</style>
