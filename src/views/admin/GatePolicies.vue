<template>
  <div class="gate-policies">
    <ElCard>
      <template #header>
        <div class="toolbar">
          <span>门禁策略（{{ policies.length }}）</span>
          <ElButton type="primary" data-test="policy-new" @click="openEditor()">新建策略</ElButton>
        </div>
      </template>
      <ElTable v-loading="loading" :data="policies" data-test="policy-table">
        <ElTableColumn prop="name" label="名称" min-width="160" />
        <ElTableColumn label="规则" min-width="260">
          <template #default="{ row }">
            {{ (row.checks || []).length }} 条
            （deny {{ cnt(row, 'pre') }} / 终态 {{ cnt(row, 'post') }} /
             过程 {{ cnt(row, 'checkpoint') }}）
          </template>
        </ElTableColumn>
        <ElTableColumn label="启用" width="80">
          <template #default="{ row }">
            <ElSwitch :model-value="row.enabled"
                      @change="(v: string | number | boolean) => toggle(row, v === true)" />
          </template>
        </ElTableColumn>
        <ElTableColumn label="更新时间" width="180">
          <template #default="{ row }">{{ fmtTime(row.updated_at) }}</template>
        </ElTableColumn>
        <ElTableColumn label="操作" width="140">
          <template #default="{ row }">
            <ElButton link @click="openEditor(row)">编辑</ElButton>
            <ElButton link type="danger" @click="remove(row)">删除</ElButton>
          </template>
        </ElTableColumn>
      </ElTable>
    </ElCard>

    <ElDialog v-model="editorVisible" :title="editingId ? '编辑策略' : '新建策略'" width="720px">
      <ElForm label-width="90px">
        <ElFormItem label="名称">
          <ElInput v-model="form.name" maxlength="100" data-test="policy-name"
                   placeholder="必填,≤100 字" />
        </ElFormItem>
        <ElFormItem label="描述">
          <ElInput v-model="form.description" placeholder="可选" />
        </ElFormItem>
        <ElFormItem label="启用"><ElSwitch v-model="form.enabled" /></ElFormItem>
        <ElFormItem label="规则">
          <!-- 行编辑与批创建/批编辑对话框同一套(GateCheckRows):
               终态期望/拦截/过程检查三模式,提交归一 serializeChecks -->
          <GateCheckRows v-model="form.checks" />
        </ElFormItem>
      </ElForm>
      <template #footer>
        <ElButton @click="editorVisible = false">取消</ElButton>
        <ElButton type="primary" data-test="policy-save" :loading="saving" @click="save">保存</ElButton>
      </template>
    </ElDialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import {
  ElCard, ElTable, ElTableColumn, ElButton, ElSwitch, ElDialog,
  ElForm, ElFormItem, ElInput, ElMessage, ElMessageBox,
} from 'element-plus'
import {
  listGatePolicies, createGatePolicy, updateGatePolicy, deleteGatePolicy,
} from '@/api/gatePolicies'
import type { GatePolicy } from '@/api/gatePolicies'
import GateCheckRows from '@/components/admin/GateCheckRows.vue'
import type { GateCheckEditRow } from '@/components/admin/GateCheckRows.vue'
import { emptyEditRow, prefillEditRows, serializeChecks } from '@/components/admin/GateCheckRows.vue'

// 门禁策略库管理页(2026-10-11 spec §8.1):策略只是模板——批引用登记时落
// 期望快照,这里改不重写已登记任务;删除受 409 POLICY_IN_USE 防护
const policies = ref<GatePolicy[]>([])
const loading = ref(false)

const editorVisible = ref(false)
const editingId = ref('')
const saving = ref(false)
const form = ref<{ name: string; description: string; enabled: boolean; checks: GateCheckEditRow[] }>({
  name: '', description: '', enabled: true, checks: [emptyEditRow()],
})

async function load() {
  loading.value = true
  try {
    policies.value = (await listGatePolicies()).policies
  } catch { /* 全局拦截器已提示 */ }
  finally { loading.value = false }
}
onMounted(load)

/** 规则计数:按 mode 分桶(missing mode = post,后端归一缺省) */
function cnt(row: GatePolicy, mode: 'pre' | 'post' | 'checkpoint') {
  return (row.checks || []).filter(c => (c.mode || 'post') === mode).length
}

function fmtTime(v?: string | null) {
  return v ? new Date(v).toLocaleString() : '—'
}

function openEditor(row?: GatePolicy) {
  editingId.value = row?.id || ''
  form.value = {
    name: row?.name || '',
    description: row?.description || '',
    enabled: row ? row.enabled !== false : true,
    checks: row ? prefillEditRows(row.checks) : [emptyEditRow()],
  }
  editorVisible.value = true
}

async function toggle(row: GatePolicy, v: boolean) {
  try {
    await updateGatePolicy(row.id, { enabled: v })
    await load()
  } catch { /* 全局拦截器已提示;开关保持原值(非 v-model,随 load 回正) */ }
}

async function remove(row: GatePolicy) {
  const ok = await ElMessageBox.confirm(
    `删除门禁策略「${row.name}」？仍被非终态批引用时删除会被拒绝。`,
    '删除确认', { type: 'warning' },
  ).then(() => true).catch(() => false)
  if (!ok) return
  try {
    await deleteGatePolicy(row.id)
    ElMessage.success('已删除')
    await load()
  } catch { /* 全局拦截器已提示;409 POLICY_IN_USE = 先解绑或等待终态 */ }
}

async function save() {
  if (!form.value.name.trim()) {
    ElMessage.warning('策略名必填')
    return
  }
  // 提交守卫与批对话框同口径(后端 validate_checks 400,前端先行拦截)
  if (form.value.checks.some(c => (c.rubric || '').length > 2000)) {
    ElMessage.error('判官核对要点超长（>2000 字）')
    return
  }
  if (form.value.checks.some(c => c.mode === 'checkpoint' && c.name.trim()
      && c.args_pattern.trim() && !(c.prompt || '').trim())) {
    ElMessage.error('过程检查行需要填写检查提示词（命中后注入给模型）')
    return
  }
  if (form.value.checks.some(c => c.mode === 'checkpoint' && c.hasAssert
      && (!(c.assert_tool || '').trim() || !(c.assert_pattern || '').trim()))) {
    ElMessage.error('终态兜底断言需要填写断言工具与 args 正则')
    return
  }
  const checks = serializeChecks(form.value.checks)
  if (!checks.length) {
    ElMessage.warning('至少需要一条完整的规则（名称与参数正则/判定要点）')
    return
  }
  saving.value = true
  try {
    if (editingId.value) {
      await updateGatePolicy(editingId.value, {
        name: form.value.name.trim(),
        description: form.value.description.trim(),
        checks, enabled: form.value.enabled,
      })
    } else {
      await createGatePolicy({
        name: form.value.name.trim(),
        description: form.value.description.trim(),
        checks, enabled: form.value.enabled,
      })
    }
    ElMessage.success('已保存')
    editorVisible.value = false
    await load()
  } catch { /* 全局拦截器已提示(重名 400:策略名已存在) */ }
  finally { saving.value = false }
}
</script>

<style scoped>
.toolbar { display: flex; align-items: center; justify-content: space-between; }
</style>
