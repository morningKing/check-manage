/**
 * 系统备份管理页面
 *
 * 职责：
 * - 查看备份列表
 * - 手动创建备份（支持全量/表级）
 * - 下载/还原/删除备份
 * - 上传外部备份还原
 * - 配置定时备份设置
 *
 * 仅管理员可访问
 */
<template>
  <div class="backup-manager">
    <!-- 定时备份设置 -->
    <el-card style="margin-bottom: 16px">
      <template #header>
        <div class="card-header">
          <h2>定时备份设置</h2>
        </div>
      </template>
      <el-form :inline="true" class="settings-form">
        <el-form-item label="启用定时备份">
          <el-switch v-model="settings.enabled" />
        </el-form-item>
        <el-form-item label="备份周期">
          <el-select v-model="settings.interval" style="width: 120px" :disabled="!settings.enabled">
            <el-option
              v-for="opt in BACKUP_INTERVAL_OPTIONS"
              :key="opt.value"
              :label="opt.label"
              :value="opt.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="保留数量">
          <el-input-number
            v-model="settings.retentionCount"
            :min="1"
            :max="100"
            :disabled="!settings.enabled"
            style="width: 130px"
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="handleSaveSettings" :loading="settingsSaving">
            保存设置
          </el-button>
        </el-form-item>
      </el-form>
      <div v-if="settings.lastBackupAt" class="last-backup-info">
        上次自动备份时间：{{ formatDate(settings.lastBackupAt) }}
      </div>
    </el-card>

    <!-- 备份列表 -->
    <el-card>
      <template #header>
        <div class="card-header">
          <h2>备份列表</h2>
          <div class="header-actions">
            <el-button type="primary" @click="handleCreateBackup" :loading="creating">
              <el-icon><Plus /></el-icon>
              创建备份
            </el-button>
            <el-button @click="triggerUpload">
              <el-icon><Upload /></el-icon>
              上传还原
            </el-button>
            <input
              ref="fileInputRef"
              type="file"
              accept=".zip"
              style="display: none"
              @change="handleFileSelected"
            />
          </div>
        </div>
      </template>

      <el-table :data="backupList" v-loading="loading" stripe border style="width: 100%">
        <el-table-column prop="name" label="备份名称" min-width="200" show-overflow-tooltip />
        <el-table-column prop="type" label="类型" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="BACKUP_TYPE_TAG_TYPES[row.type] || 'info'" size="small">
              {{ BACKUP_TYPE_LABELS[row.type] || row.type }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="backupScope" label="备份范围" width="140" align="center">
          <template #default="{ row }">
            <el-tag :type="row.backupScope === 'full' ? '' : 'warning'" size="small">
              {{ row.backupScope === 'full' ? '全量' : '表级' }}
            </el-tag>
            <el-tooltip v-if="row.backupScope === 'partial' && row.backupTables?.length" placement="top">
              <template #content>
                <div style="max-width: 300px">
                  {{ row.backupTables.map((t: string) => tableLabelMap[t] || t).join('、') }}
                </div>
              </template>
              <el-icon style="margin-left: 4px; cursor: pointer"><InfoFilled /></el-icon>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="BACKUP_STATUS_TAG_TYPES[row.status] || 'info'" size="small">
              {{ BACKUP_STATUS_LABELS[row.status] || row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="fileSize" label="文件大小" width="110" align="right">
          <template #default="{ row }">
            {{ formatFileSize(row.fileSize) }}
          </template>
        </el-table-column>
        <el-table-column prop="recordsCount" label="记录数" width="90" align="center" />
        <el-table-column prop="createdBy" label="创建人" width="100" />
        <el-table-column prop="createdAt" label="创建时间" width="180">
          <template #default="{ row }">
            {{ formatDate(row.createdAt) }}
          </template>
        </el-table-column>
        <el-table-column prop="note" label="备注" min-width="120" show-overflow-tooltip />
        <el-table-column label="操作" width="200" align="center" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" link size="small" @click="handleDownload(row)">
              下载
            </el-button>
            <el-button type="warning" link size="small" @click="handleRestoreConfirm(row)">
              还原
            </el-button>
            <el-button type="danger" link size="small" @click="handleDeleteConfirm(row)">
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 创建备份对话框 -->
    <el-dialog v-model="createDialogVisible" title="创建备份" width="600px">
      <el-form label-width="100px">
        <el-form-item label="备份范围">
          <el-radio-group v-model="createScope">
            <el-radio value="full">全量备份</el-radio>
            <el-radio value="partial">表级备份</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="createScope === 'partial'" label="选择数据">
          <div class="table-selector">
            <el-input
              v-model="tableSearchKey"
              placeholder="搜索..."
              clearable
              style="margin-bottom: 8px"
            />
            <div class="table-list">
              <template v-for="t in availableTables" :key="t.name">
                <!-- 分组表（动态数据） -->
                <template v-if="t.isGroup && t.children">
                  <div class="table-group-header">
                    <el-checkbox
                      :model-value="isGroupAllSelected(t)"
                      :indeterminate="isGroupIndeterminate(t)"
                      @change="toggleGroup(t, $event)"
                    >
                      {{ t.label }}（全选）
                    </el-checkbox>
                  </div>
                  <div class="table-group-children">
                    <el-checkbox
                      v-for="child in filterChildren(t.children)"
                      :key="child.name"
                      :model-value="createSelectedTables.includes(child.name)"
                      @change="toggleChild(child.name, $event)"
                    >
                      {{ child.label }}
                      <span class="record-count">({{ child.count || 0 }} 条)</span>
                    </el-checkbox>
                  </div>
                </template>
                <!-- 普通表 -->
                <template v-else-if="!t.isGroup && matchesSearch(t.label)">
                  <el-checkbox
                    :model-value="createSelectedTables.includes(t.name)"
                    @change="toggleChild(t.name, $event)"
                  >
                    {{ t.label }}
                  </el-checkbox>
                </template>
              </template>
            </div>
          </div>
        </el-form-item>
        <el-form-item label="备注（可选）">
          <el-input v-model="createNote" placeholder="例如：部署前备份" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="doCreateBackup" :loading="creating">
          确认创建
        </el-button>
      </template>
    </el-dialog>

    <!-- 还原确认 -->
    <el-dialog v-model="restoreDialogVisible" title="还原确认" width="500px">
      <el-alert
        title="警告：还原操作将覆盖当前数据！"
        type="warning"
        :closable="false"
        show-icon
        style="margin-bottom: 16px"
      />
      <p>确定要从备份 <strong>{{ restoreTarget?.name }}</strong> 还原吗？</p>

      <!-- 表级备份显示表选择 -->
      <div v-if="restoreTarget?.backupScope === 'partial'" style="margin-top: 12px">
        <el-form-item label="选择还原数据">
          <el-select
            v-model="restoreSelectedTables"
            multiple
            filterable
            placeholder="请选择要还原的数据"
            style="width: 100%"
          >
            <el-option
              v-for="t in restoreTarget?.backupTables || []"
              :key="t"
              :label="formatBackupTableName(t)"
              :value="t"
            />
          </el-select>
        </el-form-item>
      </div>

      <p style="color: #909399; font-size: 13px; margin-top: 12px">还原后页面将自动刷新。</p>
      <template #footer>
        <el-button @click="restoreDialogVisible = false">取消</el-button>
        <el-button type="warning" @click="doRestore" :loading="restoring">
          确认还原
        </el-button>
      </template>
    </el-dialog>

    <!-- 上传还原确认 -->
    <el-dialog v-model="uploadRestoreDialogVisible" title="上传还原确认" width="420px">
      <el-alert
        title="警告：还原操作将覆盖当前所有业务数据！"
        type="warning"
        :closable="false"
        show-icon
        style="margin-bottom: 16px"
      />
      <p>确定要从上传的文件 <strong>{{ uploadFile?.name }}</strong> 还原吗？</p>
      <p style="color: #909399; font-size: 13px">还原后页面将自动刷新。</p>
      <template #footer>
        <el-button @click="uploadRestoreDialogVisible = false">取消</el-button>
        <el-button type="warning" @click="doUploadRestore" :loading="restoring">
          确认还原
        </el-button>
      </template>
    </el-dialog>

    <!-- 删除确认 -->
    <ConfirmDialog
      v-model="deleteDialogVisible"
      title="删除确认"
      message="确定要删除这个备份吗？备份文件将被永久删除。"
      type="danger"
      confirm-text="删除"
      @confirm="doDelete"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus, Upload, InfoFilled } from '@element-plus/icons-vue'
import {
  getBackups,
  createBackup,
  deleteBackup,
  downloadBackup,
  restoreBackup,
  uploadAndRestore,
  getBackupSettings,
  updateBackupSettings,
  getBackupTables,
} from '@/api/backup'
import { ConfirmDialog } from '@/components/common'
import {
  BACKUP_TYPE_LABELS,
  BACKUP_TYPE_TAG_TYPES,
  BACKUP_STATUS_LABELS,
  BACKUP_STATUS_TAG_TYPES,
  BACKUP_INTERVAL_OPTIONS,
} from '@/types'
import type { Backup } from '@/types'

// ==================== State ====================

const loading = ref(false)
const creating = ref(false)
const restoring = ref(false)
const settingsSaving = ref(false)

const backupList = ref<Backup[]>([])
const settings = reactive({
  enabled: false,
  interval: 'daily' as 'daily' | 'weekly' | 'monthly',
  retentionCount: 10,
  lastBackupAt: null as string | null,
})

// 可备份的表列表
interface BackupTableItem {
  name: string
  label: string
  isGroup?: boolean
  children?: { name: string; label: string; count?: number }[]
}
const availableTables = ref<BackupTableItem[]>([])

// 表名 -> 中文标签映射
const tableLabelMap = computed(() => {
  const map: Record<string, string> = {}
  for (const t of availableTables.value) {
    if (t.isGroup && t.children) {
      for (const child of t.children) {
        map[child.name] = child.label
      }
    }
    map[t.name] = t.label
  }
  return map
})

// 创建备份对话框
const createDialogVisible = ref(false)
const createNote = ref('')
const createScope = ref<'full' | 'partial'>('full')
const createSelectedTables = ref<string[]>([])
const tableSearchKey = ref('')

// 还原确认
const restoreDialogVisible = ref(false)
const restoreTarget = ref<Backup | null>(null)
const restoreSelectedTables = ref<string[]>([])

const deleteDialogVisible = ref(false)
const deleteTarget = ref<Backup | null>(null)

const uploadRestoreDialogVisible = ref(false)
const uploadFile = ref<File | null>(null)
const fileInputRef = ref<HTMLInputElement | null>(null)

// ==================== 方法 ====================

function formatDate(value: string | null): string {
  if (!value) return '-'
  try {
    const date = new Date(value)
    if (isNaN(date.getTime())) return value
    const y = date.getFullYear()
    const m = String(date.getMonth() + 1).padStart(2, '0')
    const d = String(date.getDate()).padStart(2, '0')
    const h = String(date.getHours()).padStart(2, '0')
    const min = String(date.getMinutes()).padStart(2, '0')
    const sec = String(date.getSeconds()).padStart(2, '0')
    return `${y}-${m}-${d} ${h}:${min}:${sec}`
  } catch {
    return value || '-'
  }
}

function formatFileSize(bytes: number): string {
  if (!bytes || bytes === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let i = 0
  let size = bytes
  while (size >= 1024 && i < units.length - 1) {
    size /= 1024
    i++
  }
  return `${size.toFixed(i === 0 ? 0 : 1)} ${units[i]}`
}

async function loadBackups(): Promise<void> {
  loading.value = true
  try {
    backupList.value = await getBackups()
  } catch {
    ElMessage.error('加载备份列表失败')
  } finally {
    loading.value = false
  }
}

async function loadSettings(): Promise<void> {
  try {
    const s = await getBackupSettings()
    settings.enabled = s.enabled
    settings.interval = s.interval
    settings.retentionCount = s.retentionCount
    settings.lastBackupAt = s.lastBackupAt
  } catch {
    // 静默处理
  }
}

async function loadAvailableTables(): Promise<void> {
  try {
    availableTables.value = await getBackupTables()
  } catch {
    // 静默处理
  }
}

async function handleSaveSettings(): Promise<void> {
  settingsSaving.value = true
  try {
    await updateBackupSettings({
      enabled: settings.enabled,
      interval: settings.interval,
      retentionCount: settings.retentionCount,
    })
    ElMessage.success('设置已保存')
  } catch {
    ElMessage.error('保存设置失败')
  } finally {
    settingsSaving.value = false
  }
}

function handleCreateBackup(): void {
  createNote.value = ''
  createScope.value = 'full'
  createSelectedTables.value = []
  tableSearchKey.value = ''
  createDialogVisible.value = true
}

function matchesSearch(label: string): boolean {
  if (!tableSearchKey.value) return true
  return label.toLowerCase().includes(tableSearchKey.value.toLowerCase())
}

function filterChildren(children: { name: string; label: string; count?: number }[]) {
  if (!tableSearchKey.value) return children
  return children.filter(c => matchesSearch(c.label))
}

function isGroupAllSelected(t: BackupTableItem): boolean {
  if (!t.children) return false
  return t.children.every(c => createSelectedTables.value.includes(c.name))
}

function isGroupIndeterminate(t: BackupTableItem): boolean {
  if (!t.children) return false
  const selected = t.children.filter(c => createSelectedTables.value.includes(c.name))
  return selected.length > 0 && selected.length < t.children.length
}

function toggleGroup(t: BackupTableItem, checked: boolean): void {
  if (!t.children) return
  if (checked) {
    // 全选
    for (const c of t.children) {
      if (!createSelectedTables.value.includes(c.name)) {
        createSelectedTables.value.push(c.name)
      }
    }
  } else {
    // 取消全选
    createSelectedTables.value = createSelectedTables.value.filter(
      name => !t.children!.some(c => c.name === name)
    )
  }
}

function toggleChild(name: string, checked: boolean): void {
  if (checked) {
    if (!createSelectedTables.value.includes(name)) {
      createSelectedTables.value.push(name)
    }
  } else {
    createSelectedTables.value = createSelectedTables.value.filter(n => n !== name)
  }
}

function formatBackupTableName(name: string): string {
  // 处理 dynamic_data:collection 格式
  if (name.startsWith('dynamic_data:')) {
    const collection = name.substring('dynamic_data:'.length)
    return tableLabelMap.value[name] || collection
  }
  return tableLabelMap.value[name] || name
}

async function doCreateBackup(): Promise<void> {
  if (createScope.value === 'partial' && createSelectedTables.value.length === 0) {
    ElMessage.warning('请至少选择一张表')
    return
  }

  creating.value = true
  try {
    const tables = createScope.value === 'partial' ? createSelectedTables.value : undefined
    await createBackup(createNote.value || undefined, tables)
    ElMessage.success('备份创建成功')
    createDialogVisible.value = false
    await loadBackups()
  } catch {
    ElMessage.error('备份创建失败')
  } finally {
    creating.value = false
  }
}

async function handleDownload(row: Backup): Promise<void> {
  try {
    await downloadBackup(row.id, row.name)
  } catch {
    ElMessage.error('下载失败')
  }
}

function handleRestoreConfirm(row: Backup): void {
  restoreTarget.value = row
  // 表级备份默认全选
  restoreSelectedTables.value = row.backupScope === 'partial' && row.backupTables
    ? [...row.backupTables]
    : []
  restoreDialogVisible.value = true
}

async function doRestore(): Promise<void> {
  if (!restoreTarget.value) return

  // 表级备份需要选择表
  if (restoreTarget.value.backupScope === 'partial' && restoreSelectedTables.value.length === 0) {
    ElMessage.warning('请至少选择一张表')
    return
  }

  restoring.value = true
  try {
    const tables = restoreTarget.value.backupScope === 'partial'
      ? restoreSelectedTables.value
      : undefined
    await restoreBackup(restoreTarget.value.id, tables)
    ElMessage.success('还原成功，页面即将刷新')
    restoreDialogVisible.value = false
    setTimeout(() => window.location.reload(), 1500)
  } catch {
    ElMessage.error('还原失败')
  } finally {
    restoring.value = false
  }
}

function handleDeleteConfirm(row: Backup): void {
  deleteTarget.value = row
  deleteDialogVisible.value = true
}

async function doDelete(): Promise<void> {
  if (!deleteTarget.value) return
  try {
    await deleteBackup(deleteTarget.value.id)
    ElMessage.success('删除成功')
    deleteDialogVisible.value = false
    await loadBackups()
  } catch {
    // Error shown by interceptor
  }
}

function triggerUpload(): void {
  fileInputRef.value?.click()
}

function handleFileSelected(event: Event): void {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  uploadFile.value = file
  uploadRestoreDialogVisible.value = true
  // Reset input so same file can be selected again
  input.value = ''
}

async function doUploadRestore(): Promise<void> {
  if (!uploadFile.value) return
  restoring.value = true
  try {
    await uploadAndRestore(uploadFile.value)
    ElMessage.success('还原成功，页面即将刷新')
    uploadRestoreDialogVisible.value = false
    setTimeout(() => window.location.reload(), 1500)
  } catch {
    ElMessage.error('上传还原失败')
  } finally {
    restoring.value = false
  }
}

// ==================== 生命周期 ====================

onMounted(() => {
  loadBackups()
  loadSettings()
  loadAvailableTables()
})
</script>

<style scoped lang="scss">
.backup-manager {
  height: 100%;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;

  h2 {
    margin: 0;
    font-size: 18px;
    font-weight: 600;
    color: #303133;
  }
}

.header-actions {
  display: flex;
  gap: 8px;
}

.settings-form {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.last-backup-info {
  margin-top: 8px;
  color: #909399;
  font-size: 13px;
}

.table-selector {
  width: 100%;
  max-height: 300px;
  overflow-y: auto;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
  padding: 8px;
}

.table-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.table-group-header {
  padding: 8px 0 4px 0;
  border-bottom: 1px solid #ebeef5;
  margin-bottom: 4px;
}

.table-group-header :deep(.el-checkbox__label) {
  font-weight: 600;
  color: #303133;
}

.table-group-children {
  padding-left: 24px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.record-count {
  color: #909399;
  font-size: 12px;
  margin-left: 4px;
}
</style>