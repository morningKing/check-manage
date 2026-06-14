<template>
  <div class="workflow-manager">
    <div class="page-header">
      <h2>工作流</h2>
      <el-button type="primary" @click="handleAdd">新建工作流</el-button>
    </div>

    <el-table :data="store.definitions" border stripe v-loading="store.loading">
      <el-table-column prop="name" label="名称" min-width="180" />
      <el-table-column prop="description" label="描述" min-width="200" show-overflow-tooltip />
      <el-table-column label="阶段数" width="90" align="center">
        <template #default="{ row }">{{ row.stages.length }}</template>
      </el-table-column>
      <el-table-column label="启用" width="90" align="center">
        <template #default="{ row }">
          <el-switch :model-value="row.enabled" @change="(v: boolean) => handleToggle(row, v)" />
        </template>
      </el-table-column>
      <el-table-column label="操作" width="140">
        <template #default="{ row }">
          <el-button link @click="handleEdit(row)">编辑</el-button>
          <el-button link type="danger" @click="handleDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 编辑对话框 -->
    <el-dialog v-model="editVisible" :title="editForm.id ? '编辑工作流' : '新建工作流'" width="820px" top="5vh">
      <el-form label-width="90px">
        <el-form-item label="名称">
          <el-input v-model="editForm.name" placeholder="工作流名称" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="editForm.description" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="editForm.enabled" />
        </el-form-item>

        <el-divider content-position="left">阶段</el-divider>

        <el-empty v-if="editForm.stages.length === 0" description="暂无阶段" :image-size="60" />

        <div v-for="(stage, idx) in editForm.stages" :key="stage.id" class="stage-card">
          <div class="stage-card__head">
            <span class="stage-card__idx">阶段 {{ idx + 1 }}</span>
            <div class="stage-card__ops">
              <el-button link :disabled="idx === 0" @click="moveStage(idx, -1)">上移</el-button>
              <el-button link :disabled="idx === editForm.stages.length - 1" @click="moveStage(idx, 1)">下移</el-button>
              <el-button link type="danger" @click="editForm.stages.splice(idx, 1)">删除</el-button>
            </div>
          </div>

          <el-form-item label="阶段名">
            <el-input v-model="stage.name" placeholder="如 初审" />
          </el-form-item>
          <el-form-item label="绑定数据页">
            <el-select v-model="stage.collection" filterable placeholder="选择数据页" style="width: 100%">
              <el-option
                v-for="c in collectionOptions"
                :key="c.value"
                :label="c.label"
                :value="c.value"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="状态字段">
            <el-select
              v-if="fieldsOf(stage.collection).length"
              v-model="stage.statusField"
              filterable
              allow-create
              clearable
              placeholder="选择或输入字段名"
              style="width: 100%"
            >
              <el-option
                v-for="f in fieldsOf(stage.collection)"
                :key="f.value"
                :label="f.label"
                :value="f.value"
              />
            </el-select>
            <el-input v-else v-model="stage.statusField" placeholder="状态字段名" />
          </el-form-item>
          <el-form-item label="推进转换">
            <div class="transition-row">
              <el-input v-model="advFrom(stage).from" placeholder="from（当前状态）" />
              <span class="arrow">→</span>
              <el-input v-model="advFrom(stage).to" placeholder="to（推进后状态）" />
            </div>
          </el-form-item>
          <el-form-item label="回退转换">
            <div class="transition-row">
              <el-input v-model="rejFrom(stage).from" placeholder="from（可选）" />
              <span class="arrow">→</span>
              <el-input v-model="rejFrom(stage).to" placeholder="to（可选）" />
            </div>
          </el-form-item>
          <el-form-item label="办理角色">
            <el-select
              v-model="stage.assignedRoles"
              multiple
              filterable
              placeholder="选择可办理该阶段的角色"
              style="width: 100%"
            >
              <el-option v-for="r in roleOptions" :key="r.id" :label="r.name" :value="r.id" />
            </el-select>
          </el-form-item>

          <el-form-item label="生成下游">
            <div class="spawn-block">
              <div v-for="(m, mi) in spawnRows(stage)" :key="mi" class="map-row">
                <el-input v-model="m.key" placeholder="目标字段" style="width: 180px" />
                <span class="arrow">→</span>
                <el-input
                  v-model="m.value"
                  placeholder="$source.字段 / $NOW / 字面量"
                  style="width: 240px"
                />
                <el-button link type="danger" @click="removeSpawnRow(stage, mi)">删除</el-button>
              </div>
              <el-button size="small" @click="addSpawnRow(stage)">+ 添加字段映射</el-button>
              <div class="linkback">
                <span class="linkback__label">回链字段</span>
                <el-input
                  :model-value="stage.spawn?.linkBackField || ''"
                  placeholder="下游记录回写上游 id 的字段（可选）"
                  style="width: 280px"
                  @update:model-value="(v: string) => setLinkBack(stage, v)"
                />
              </div>
            </div>
          </el-form-item>
        </div>

        <el-button class="add-stage" @click="addStage">+ 添加阶段</el-button>
      </el-form>

      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" @click="handleSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useWorkflowStore } from '@/stores/workflow'
import { usePageConfigStore } from '@/stores/pageConfig'
import { useRoleStore } from '@/stores/role'
import type { WorkflowDefinition, WorkflowStage } from '@/types/workflow'

const store = useWorkflowStore()
const pageConfigStore = usePageConfigStore()
const roleStore = useRoleStore()

const editVisible = ref(false)
const editForm = reactive<WorkflowDefinition>({
  id: undefined,
  name: '',
  description: '',
  enabled: true,
  stages: [],
})

// 数据页选项：pageId 去掉 `page-` 前缀即 collection 名
const collectionOptions = computed(() =>
  pageConfigStore.pageConfigs.map((c) => ({
    label: `${c.name}（${c.id.replace('page-', '')}）`,
    value: c.id.replace('page-', ''),
  })),
)

const roleOptions = computed(() => roleStore.options)

/** 给定 collection 名，返回其字段选项（fieldName 作为值） */
function fieldsOf(collection: string): { label: string; value: string }[] {
  if (!collection) return []
  const config = pageConfigStore.pageConfigs.find((c) => c.id.replace('page-', '') === collection)
  if (!config) return []
  return config.fields.map((f) => ({ label: `${f.label}（${f.fieldName}）`, value: f.fieldName }))
}

// --- 转换的就地编辑（保证对象存在）---
function advFrom(stage: WorkflowStage) {
  if (!stage.advanceTransition) stage.advanceTransition = { from: '', to: '' }
  return stage.advanceTransition
}
function rejFrom(stage: WorkflowStage) {
  if (!stage.rejectTransition) stage.rejectTransition = { from: '', to: '' }
  return stage.rejectTransition
}

// --- spawn 字段映射：以行数组形式编辑，保存时转回 Record ---
interface SpawnRow {
  key: string
  value: string
}
// 每个阶段的 spawn 行编辑态（按 stage.id 缓存）
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

function blankStage(): WorkflowStage {
  return {
    id: `stage-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    name: '',
    collection: '',
    statusField: '',
    assignedRoles: [],
  }
}

function addStage() {
  editForm.stages.push(blankStage())
}

function moveStage(idx: number, dir: number) {
  const target = idx + dir
  if (target < 0 || target >= editForm.stages.length) return
  const arr = editForm.stages
  ;[arr[idx], arr[target]] = [arr[target], arr[idx]]
}

function handleAdd() {
  Object.assign(editForm, { id: undefined, name: '', description: '', enabled: true, stages: [] })
  for (const k of Object.keys(spawnRowState)) delete spawnRowState[k]
  editVisible.value = true
}

function handleEdit(row: WorkflowDefinition) {
  // 深拷贝避免直接改表格里的对象
  const clone: WorkflowDefinition = JSON.parse(JSON.stringify(row))
  Object.assign(editForm, {
    id: clone.id,
    name: clone.name,
    description: clone.description || '',
    enabled: clone.enabled,
    stages: clone.stages || [],
  })
  for (const k of Object.keys(spawnRowState)) delete spawnRowState[k]
  editVisible.value = true
}

/** 把编辑态收敛为可提交的定义（清洗空转换/空映射） */
function buildPayload(): WorkflowDefinition {
  const stages: WorkflowStage[] = editForm.stages.map((stage) => {
    const out: WorkflowStage = {
      id: stage.id,
      name: stage.name,
      collection: stage.collection,
    }
    if (stage.statusField) out.statusField = stage.statusField
    const adv = stage.advanceTransition
    if (adv && (adv.from || adv.to)) out.advanceTransition = { from: adv.from, to: adv.to }
    const rej = stage.rejectTransition
    if (rej && (rej.from || rej.to)) out.rejectTransition = { from: rej.from, to: rej.to }
    if (stage.assignedRoles && stage.assignedRoles.length) out.assignedRoles = stage.assignedRoles

    const rows = spawnRowState[stage.id]
    const mapping: Record<string, string> = {}
    if (rows) {
      for (const r of rows) {
        if (r.key.trim()) mapping[r.key.trim()] = r.value
      }
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

  return {
    id: editForm.id,
    name: editForm.name,
    description: editForm.description || undefined,
    enabled: editForm.enabled,
    stages,
  }
}

async function handleSave() {
  if (!editForm.name.trim()) {
    ElMessage.warning('请填写工作流名称')
    return
  }
  for (const s of editForm.stages) {
    if (!s.name.trim() || !s.collection) {
      ElMessage.warning('每个阶段都需要阶段名与绑定数据页')
      return
    }
  }
  try {
    const saved = await store.save(buildPayload())
    editVisible.value = false
    if (saved.warnings && saved.warnings.length) {
      ElMessage.warning({
        message: '已保存，但存在配置问题：\n' + saved.warnings.join('\n'),
        duration: 6000,
      })
    } else {
      ElMessage.success('保存成功')
    }
  } catch {
    /* 全局拦截器已提示 */
  }
}

async function handleToggle(row: WorkflowDefinition, enabled: boolean) {
  try {
    await store.save({ ...row, enabled })
  } catch {
    /* 全局拦截器已提示 */
  }
}

async function handleDelete(row: WorkflowDefinition) {
  if (!row.id) return
  try {
    await ElMessageBox.confirm(`确定删除工作流「${row.name}」？`, '确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await store.remove(row.id)
    ElMessage.success('已删除')
  } catch {
    /* 全局拦截器已提示 */
  }
}

onMounted(async () => {
  await store.loadDefinitions()
  // 加载数据页与角色选项（reuse 既有机制）
  if (!pageConfigStore.pageConfigs.length) {
    try {
      await pageConfigStore.fetchPageConfigs()
    } catch {
      /* 非致命 */
    }
  }
  try {
    await roleStore.loadOptions()
  } catch {
    /* 非致命 */
  }
})
</script>

<style scoped lang="scss">
.workflow-manager {
  padding: 0;
}
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.page-header h2 {
  margin: 0;
}
.stage-card {
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
  padding: 12px 16px;
  margin-bottom: 14px;
  background: var(--el-fill-color-light);
}
.stage-card__head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}
.stage-card__idx {
  font-weight: 600;
}
.transition-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}
.arrow {
  color: var(--el-text-color-secondary);
}
.spawn-block {
  width: 100%;
}
.map-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.linkback {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}
.linkback__label {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.add-stage {
  width: 100%;
  margin-top: 4px;
}
</style>
