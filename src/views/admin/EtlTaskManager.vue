/**
 * ETL 任务管理页面
 *
 * 职责：
 * - 左侧：ETL 任务列表
 * - 右侧：任务编辑 + 纵向管道可视化编排 + 执行/测试 + 执行日志
 */
<template>
  <div class="etl-manager">
    <el-row :gutter="20" class="full-height">
      <!-- 左侧：任务列表 -->
      <el-col :span="8">
        <el-card class="list-card">
          <template #header>
            <div class="card-header">
              <span>ETL 任务列表</span>
              <el-button type="primary" size="small" @click="handleAddTask">
                <el-icon><Plus /></el-icon>
                新增
              </el-button>
            </div>
          </template>

          <div class="task-list">
            <div
              v-for="task in tasks"
              :key="task.id"
              class="task-item"
              :class="{ active: currentTaskId === task.id }"
              @click="handleSelectTask(task)"
            >
              <div class="task-info">
                <div class="task-name">{{ task.name || '未命名任务' }}</div>
                <div class="task-meta">
                  <el-tag size="small" :type="task.steps.length > 0 ? '' : 'info'">
                    {{ task.steps.length > 0 ? `${task.steps.length} 个步骤` : '草稿' }}
                  </el-tag>
                  <el-tag
                    v-if="task.lastRunStatus"
                    size="small"
                    :type="statusType(task.lastRunStatus)"
                  >
                    {{ statusLabel(task.lastRunStatus) }}
                  </el-tag>
                </div>
              </div>
              <div class="task-actions">
                <el-button
                  type="danger"
                  link
                  size="small"
                  @click.stop="handleDeleteConfirm(task)"
                >
                  删除
                </el-button>
              </div>
            </div>

            <el-empty v-if="tasks.length === 0" description="暂无 ETL 任务" />
          </div>
        </el-card>
      </el-col>

      <!-- 右侧：任务详情 -->
      <el-col :span="16">
        <el-card class="detail-card">
          <template #header>
            <div class="card-header">
              <span>{{ currentTaskId ? (currentTaskId === '__new__' ? '新建任务' : '编辑任务') : '任务详情' }}</span>
            </div>
          </template>

          <div v-if="currentTaskId" class="task-detail">
            <!-- 基本信息 -->
            <el-form ref="formRef" :model="formData" :rules="formRules" label-width="80px" class="task-form">
              <el-row :gutter="16">
                <el-col :span="12">
                  <el-form-item label="任务名称" prop="name">
                    <el-input v-model="formData.name" placeholder="请输入任务名称" maxlength="50" />
                  </el-form-item>
                </el-col>
                <el-col :span="12">
                  <el-form-item label="描述">
                    <el-input v-model="formData.description" placeholder="任务描述（可选）" />
                  </el-form-item>
                </el-col>
              </el-row>
            </el-form>

            <!-- 数据管道 -->
            <div class="pipeline-section">
              <div class="section-header">
                <span class="section-title">数据管道</span>
                <el-dropdown @command="handleAddStep" trigger="click">
                  <el-button type="primary" size="small">
                    <el-icon><Plus /></el-icon>
                    添加步骤
                  </el-button>
                  <template #dropdown>
                    <el-dropdown-menu>
                      <el-dropdown-item
                        v-for="st in STEP_TYPES"
                        :key="st.value"
                        :command="st.value"
                      >
                        <el-icon><component :is="st.icon" /></el-icon>
                        {{ st.label }}
                      </el-dropdown-item>
                    </el-dropdown-menu>
                  </template>
                </el-dropdown>
              </div>

              <!-- 管道流 -->
              <div class="pipeline-flow">
                <template v-for="(step, index) in formData.steps" :key="step.id">
                  <!-- 步骤卡片 -->
                  <div class="step-card">
                    <div class="step-header">
                      <el-icon class="step-icon" :style="{ color: getStepColor(step.type) }">
                        <component :is="getStepIcon(step.type)" />
                      </el-icon>
                      <span class="step-type-label">{{ getStepTypeLabel(step.type) }}</span>
                      <span class="step-name">{{ step.name }}</span>
                      <div class="step-actions">
                        <el-button type="primary" link size="small" @click="handleEditStep(index)">
                          <el-icon><Edit /></el-icon>
                        </el-button>
                        <el-button
                          v-if="index > 0"
                          link
                          size="small"
                          @click="handleMoveStep(index, -1)"
                        >
                          <el-icon><Top /></el-icon>
                        </el-button>
                        <el-button
                          v-if="index < formData.steps.length - 1"
                          link
                          size="small"
                          @click="handleMoveStep(index, 1)"
                        >
                          <el-icon><Bottom /></el-icon>
                        </el-button>
                        <el-button type="danger" link size="small" @click="handleRemoveStep(index)">
                          <el-icon><Delete /></el-icon>
                        </el-button>
                      </div>
                    </div>
                    <div class="step-summary">{{ getStepSummary(step) }}</div>
                  </div>

                  <!-- 连接线 + 插入按钮 -->
                  <div v-if="index < formData.steps.length - 1" class="step-connector">
                    <div class="connector-line"></div>
                    <el-dropdown @command="(cmd: string) => handleInsertStep(index + 1, cmd)" trigger="click" size="small">
                      <el-button class="insert-btn" size="small" circle>
                        <el-icon><Plus /></el-icon>
                      </el-button>
                      <template #dropdown>
                        <el-dropdown-menu>
                          <el-dropdown-item
                            v-for="st in STEP_TYPES"
                            :key="st.value"
                            :command="st.value"
                          >
                            <el-icon><component :is="st.icon" /></el-icon>
                            {{ st.label }}
                          </el-dropdown-item>
                        </el-dropdown-menu>
                      </template>
                    </el-dropdown>
                    <div class="connector-line"></div>
                  </div>
                </template>

                <el-empty v-if="formData.steps.length === 0" description="请添加管道步骤" :image-size="60" />
              </div>
            </div>

            <!-- 操作按钮 -->
            <div class="action-bar">
              <el-button type="primary" @click="handleSave" :loading="saveLoading">
                保存
              </el-button>
              <el-button
                type="success"
                @click="handleDryRun"
                :loading="runLoading"
                :disabled="currentTaskId === '__new__' || formData.steps.length === 0"
              >
                测试运行
              </el-button>
              <el-button
                type="warning"
                @click="handleRun"
                :loading="runLoading"
                :disabled="currentTaskId === '__new__' || formData.steps.length === 0"
              >
                执行
              </el-button>
            </div>

            <!-- 运行结果（测试/执行后显示） -->
            <div v-if="runResult" class="run-result">
              <el-divider content-position="left">运行结果</el-divider>
              <el-alert
                :type="runResult.status === 'success' ? 'success' : runResult.status === 'partial' ? 'warning' : 'error'"
                :title="`${runResultLabel} — 总计 ${runResult.totalRecords} 条，成功 ${runResult.successCount}，失败 ${runResult.errorCount}`"
                :closable="false"
                show-icon
              />
              <!-- 步骤结果 -->
              <div class="step-results">
                <div
                  v-for="sr in runResult.stepResults"
                  :key="sr.stepId"
                  class="step-result-item"
                >
                  <el-icon :color="sr.status === 'success' ? '#67c23a' : '#f56c6c'">
                    <CircleCheckFilled v-if="sr.status === 'success'" />
                    <CircleCloseFilled v-else />
                  </el-icon>
                  <span class="sr-name">{{ sr.stepName }}</span>
                  <span v-if="sr.recordCount !== undefined" class="sr-count">{{ sr.recordCount }} 条</span>
                  <span v-if="sr.error" class="sr-error">{{ sr.error }}</span>
                </div>
              </div>
              <!-- 错误详情 -->
              <div v-if="runResult.errors.length > 0" class="error-detail">
                <div v-for="(err, i) in runResult.errors" :key="i" class="error-line">{{ err }}</div>
              </div>
            </div>

            <!-- 最近执行记录 -->
            <div v-if="currentTaskId !== '__new__' && logs.length > 0" class="logs-section">
              <el-divider content-position="left">最近执行记录</el-divider>
              <div class="log-list">
                <div v-for="log in logs" :key="log.id" class="log-item">
                  <span class="log-time">{{ formatTime(log.startedAt) }}</span>
                  <el-tag size="small" :type="statusType(log.status)">{{ statusLabel(log.status) }}</el-tag>
                  <span class="log-stat">{{ log.successCount }}/{{ log.totalRecords }} 条成功</span>
                  <span v-if="log.errorCount > 0" class="log-errors">{{ log.errorCount }} 条失败</span>
                </div>
              </div>
            </div>
          </div>

          <el-empty v-else description="请选择或新增 ETL 任务" />
        </el-card>
      </el-col>
    </el-row>

    <!-- 删除确认 -->
    <ConfirmDialog
      v-model="deleteDialogVisible"
      title="删除确认"
      :message="`确定要删除 ETL 任务「${taskToDelete?.name}」吗？相关执行日志也将一并删除。`"
      type="danger"
      confirm-text="删除"
      @confirm="handleDelete"
    />

    <!-- 步骤配置弹窗 -->
    <el-dialog
      v-model="stepDialogVisible"
      :title="`配置步骤 — ${editingStep ? getStepTypeLabel(editingStep.type) : ''}`"
      width="700px"
      :close-on-click-modal="false"
    >
      <div v-if="editingStep">
        <el-form label-width="100px">
          <el-form-item label="步骤名称">
            <el-input v-model="editingStep.name" placeholder="请输入步骤名称" maxlength="50" />
          </el-form-item>

          <el-form-item label="错误处理">
            <el-radio-group v-model="editingStep.onError">
              <el-radio value="stop">停止管道</el-radio>
              <el-radio value="skip">跳过继续</el-radio>
            </el-radio-group>
          </el-form-item>

          <el-divider content-position="left">步骤配置</el-divider>

          <!-- HTTP 请求 -->
          <template v-if="editingStep.type === 'http_request'">
            <el-form-item label="请求地址">
              <el-input v-model="editingStep.config.url" placeholder="https://api.example.com/data" />
            </el-form-item>
            <el-form-item label="请求方法">
              <el-radio-group v-model="editingStep.config.method">
                <el-radio value="GET">GET</el-radio>
                <el-radio value="POST">POST</el-radio>
              </el-radio-group>
            </el-form-item>
            <el-form-item label="请求头">
              <div class="kv-list">
                <div v-for="(h, hi) in editingStep.config.headers" :key="hi" class="kv-row">
                  <el-input v-model="h.key" placeholder="Key" style="width: 180px" />
                  <el-input v-model="h.value" placeholder="Value" style="flex: 1" />
                  <el-button type="danger" link @click="editingStep.config.headers.splice(hi, 1)">
                    <el-icon><Delete /></el-icon>
                  </el-button>
                </div>
                <el-button size="small" @click="editingStep.config.headers.push({ key: '', value: '' })">
                  添加请求头
                </el-button>
              </div>
            </el-form-item>
            <el-form-item v-if="editingStep.config.method === 'POST'" label="请求体">
              <el-input v-model="editingStep.config.body" type="textarea" :rows="4" placeholder='{"key": "value"}' />
            </el-form-item>
            <el-form-item label="响应路径">
              <el-input v-model="editingStep.config.responsePath" placeholder="如 data.items（留空则使用整个响应）" />
              <div class="form-tip">用点号分隔的路径，从 JSON 响应中提取记录数组</div>
            </el-form-item>
          </template>

          <!-- JSON 输入 -->
          <template v-if="editingStep.type === 'json_input'">
            <el-form-item label="JSON 数据">
              <el-input
                v-model="editingStep.config.data"
                type="textarea"
                :rows="10"
                placeholder='[{"name": "张三", "age": 25}]'
              />
              <div class="form-tip">输入 JSON 数组或对象。用于测试或手动导入少量数据</div>
            </el-form-item>
          </template>

          <!-- 文件上传 -->
          <template v-if="editingStep.type === 'file_upload'">
            <el-form-item label="数据文件">
              <el-upload
                ref="etlUploadRef"
                :limit="1"
                :file-list="[]"
                :show-file-list="false"
                :on-exceed="handleEtlFileExceed"
                :before-upload="beforeEtlFileUpload"
                :http-request="handleEtlFileUpload"
                accept=".xlsx,.xls,.csv"
              >
                <el-button type="primary">
                  <el-icon><UploadFilled /></el-icon>
                  {{ editingStep.config.fileName ? '重新上传' : '选择文件' }}
                </el-button>
              </el-upload>
              <div v-if="editingStep.config.fileName" class="form-tip">
                已上传：{{ editingStep.config.fileName }}（{{ formatFileSize(editingStep.config.fileSize) }}）
              </div>
              <div class="form-tip">支持 .xlsx / .xls / .csv，首行作为字段名。上传后固定绑定，之后每次运行都解析同一份文件</div>
            </el-form-item>
          </template>

          <!-- Python 脚本 -->
          <template v-if="editingStep.type === 'script'">
            <el-form-item label="脚本代码" class="code-form-item">
              <div class="code-editor-wrapper">
                <Codemirror
                  v-model="editingStep.config.script"
                  :extensions="cmExtensions"
                  :style="{ height: '300px' }"
                  placeholder="请编写 Python 转换脚本..."
                />
              </div>
              <div class="form-tip">
                注入变量：<code>records</code>（当前记录列表 list[dict]）。须设置 <code>result</code> 变量为转换后的记录列表。
                可用模块：json, re, math, collections, datetime, timedelta
              </div>
            </el-form-item>
          </template>

          <!-- 字段映射 -->
          <template v-if="editingStep.type === 'field_mapping'">
            <el-form-item label="字段映射">
              <div class="kv-list">
                <div v-for="(m, mi) in editingStep.config.mappings" :key="mi" class="kv-row">
                  <el-input v-model="m.source" placeholder="源字段名" style="width: 200px" />
                  <el-icon class="arrow-icon"><Right /></el-icon>
                  <el-input v-model="m.target" placeholder="目标字段名" style="width: 200px" />
                  <el-button type="danger" link @click="editingStep.config.mappings.splice(mi, 1)">
                    <el-icon><Delete /></el-icon>
                  </el-button>
                </div>
                <el-button size="small" @click="editingStep.config.mappings.push({ source: '', target: '' })">
                  添加映射
                </el-button>
              </div>
            </el-form-item>
            <el-form-item label="保留未映射">
              <el-switch v-model="editingStep.config.keepUnmapped" />
              <div class="form-tip">开启后，未在映射表中的字段将原样保留</div>
            </el-form-item>
          </template>

          <!-- 条件过滤 -->
          <template v-if="editingStep.type === 'filter'">
            <el-form-item label="过滤表达式">
              <el-input
                v-model="editingStep.config.expression"
                type="textarea"
                :rows="3"
                placeholder="record.get('status') == 'active'"
              />
              <div class="form-tip">
                Python 表达式。变量 <code>record</code> 为当前记录字典。返回 truthy 的记录将被保留。
              </div>
            </el-form-item>
          </template>

          <!-- 写入集合 -->
          <template v-if="editingStep.type === 'save_to_collection'">
            <el-form-item label="目标集合">
              <el-select
                v-model="editingStep.config.collection"
                placeholder="请选择目标数据页"
                filterable
                style="width: 100%"
              >
                <el-option
                  v-for="opt in pageOptions"
                  :key="opt.value"
                  :label="opt.label"
                  :value="opt.collection"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="写入模式">
              <el-radio-group v-model="editingStep.config.mode">
                <el-radio value="insert">新增</el-radio>
                <el-radio value="upsert">新增或更新</el-radio>
                <el-radio value="update">仅更新</el-radio>
              </el-radio-group>
              <div class="form-tip">
                新增：全部插入；新增或更新：按匹配字段查找，有则更新无则插入；仅更新：只更新已有记录
              </div>
            </el-form-item>
            <el-form-item v-if="editingStep.config.mode !== 'insert'" label="匹配字段">
              <el-input v-model="editingStep.config.matchField" placeholder="用于匹配已有记录的字段名" />
            </el-form-item>
          </template>
        </el-form>
      </div>

      <template #footer>
        <el-button @click="stepDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleStepConfirm">确定</el-button>
      </template>
    </el-dialog>

    <!-- 执行确认 -->
    <ConfirmDialog
      v-model="runConfirmVisible"
      title="执行确认"
      message="确定要执行此 ETL 任务吗？数据将实际写入目标集合。"
      type="warning"
      confirm-text="执行"
      @confirm="doRun(false)"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'
import { ElMessage, genFileId } from 'element-plus'
import {
  Plus, Edit, Delete, Top, Bottom, Right,
  Download, Document, Promotion, Switch, Filter, Upload, UploadFilled,
  CircleCheckFilled, CircleCloseFilled,
} from '@element-plus/icons-vue'
import { Codemirror } from 'vue-codemirror'
import { python } from '@codemirror/lang-python'
import { oneDark } from '@codemirror/theme-one-dark'
import { ConfirmDialog } from '@/components/common'
import { usePageConfigStore } from '@/stores'
import {
  getEtlTasks,
  createEtlTask,
  updateEtlTask,
  deleteEtlTask,
  runEtlTask,
  getEtlLogs,
  uploadEtlFile,
} from '@/api/etl'
import type { EtlTask, EtlStep, EtlRunResult, EtlLog } from '@/types'
import type { UploadInstance, UploadRawFile, UploadRequestOptions } from 'element-plus'

// ==================== CodeMirror ====================

const cmExtensions = [python(), oneDark]

// ==================== 常量 ====================

const STEP_TYPES = [
  { value: 'http_request', label: 'HTTP 请求', icon: 'Download' },
  { value: 'json_input', label: 'JSON 输入', icon: 'Document' },
  { value: 'file_upload', label: '文件上传', icon: 'UploadFilled' },
  { value: 'script', label: 'Python 脚本', icon: 'Promotion' },
  { value: 'field_mapping', label: '字段映射', icon: 'Switch' },
  { value: 'filter', label: '条件过滤', icon: 'Filter' },
  { value: 'save_to_collection', label: '写入集合', icon: 'Upload' },
] as const

const STEP_ICONS: Record<string, any> = {
  http_request: Download,
  json_input: Document,
  file_upload: UploadFilled,
  script: Promotion,
  field_mapping: Switch,
  filter: Filter,
  save_to_collection: Upload,
}

const STEP_COLORS: Record<string, string> = {
  http_request: '#409eff',
  json_input: '#67c23a',
  file_upload: '#20a0ff',
  script: '#e6a23c',
  field_mapping: '#909399',
  filter: '#f56c6c',
  save_to_collection: '#9b59b6',
}

// ==================== Store ====================

const pageConfigStore = usePageConfigStore()

// ==================== Refs ====================

const formRef = ref<FormInstance>()
const tasks = ref<EtlTask[]>([])
const currentTaskId = ref<string | null>(null)
const formData = ref({
  name: '',
  description: '',
  steps: [] as EtlStep[],
})
const saveLoading = ref(false)
const runLoading = ref(false)
const deleteDialogVisible = ref(false)
const taskToDelete = ref<EtlTask | null>(null)
const runResult = ref<EtlRunResult | null>(null)
const logs = ref<EtlLog[]>([])

// 步骤编辑
const stepDialogVisible = ref(false)
const editingStep = ref<EtlStep | null>(null)
const editingStepIndex = ref(-1)

// 执行确认
const runConfirmVisible = ref(false)

// ==================== 校验 ====================

const formRules: FormRules = {
  name: [{ required: true, message: '请输入任务名称', trigger: 'blur' }],
}

// ==================== 计算属性 ====================

const pageOptions = computed(() => {
  return pageConfigStore.pageConfigs.map(pc => ({
    value: pc.id,
    label: pc.name,
    collection: pc.id.replace('page-', ''),
  }))
})

const runResultLabel = computed(() => {
  if (!runResult.value) return ''
  const s = runResult.value.status
  if (s === 'success') return '执行成功'
  if (s === 'partial') return '部分成功'
  return '执行失败'
})

// ==================== 辅助方法 ====================

function statusType(status: string) {
  if (status === 'success') return 'success'
  if (status === 'partial') return 'warning'
  if (status === 'error') return 'danger'
  return 'info'
}

function statusLabel(status: string) {
  if (status === 'success') return '成功'
  if (status === 'partial') return '部分成功'
  if (status === 'error') return '失败'
  return status
}

function getStepIcon(type: string) {
  return STEP_ICONS[type] || Document
}

function getStepColor(type: string) {
  return STEP_COLORS[type] || '#409eff'
}

function getStepTypeLabel(type: string) {
  const found = STEP_TYPES.find(t => t.value === type)
  return found ? found.label : type
}

function formatFileSize(bytes: number | null | undefined): string {
  if (!bytes) return '0 B'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function getStepSummary(step: EtlStep): string {
  const c = step.config
  switch (step.type) {
    case 'http_request':
      return `${c.method || 'GET'} ${c.url || '(未配置)'}`
    case 'json_input':
      return c.data ? `${c.data.length} 字符 JSON` : '(未配置)'
    case 'file_upload':
      return c.fileName ? `${c.fileName}（${formatFileSize(c.fileSize)}）` : '(未上传文件)'
    case 'script':
      return c.script ? `Python 脚本 (${c.script.split('\n').length} 行)` : '(未配置)'
    case 'field_mapping': {
      const count = (c.mappings || []).length
      return count > 0 ? `${count} 个字段映射` : '(未配置)'
    }
    case 'filter':
      return c.expression || '(未配置)'
    case 'save_to_collection':
      return c.collection ? `→ ${c.collection} (${c.mode || 'insert'})` : '(未配置)'
    default:
      return ''
  }
}

function formatTime(ts: string): string {
  if (!ts) return '-'
  const d = new Date(ts)
  const pad = (n: number) => n.toString().padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function createDefaultConfig(type: string): Record<string, any> {
  switch (type) {
    case 'http_request':
      return { url: '', method: 'GET', headers: [], body: '', responsePath: '' }
    case 'json_input':
      return { data: '[\n  \n]' }
    case 'file_upload':
      return { fileId: null, fileName: null, fileSize: null }
    case 'script':
      return { script: '# records: list[dict] — 当前记录列表\n# 须设置 result 变量为转换后的列表\n\nresult = records\n' }
    case 'field_mapping':
      return { mappings: [{ source: '', target: '' }], keepUnmapped: false }
    case 'filter':
      return { expression: '' }
    case 'save_to_collection':
      return { collection: '', mode: 'insert', matchField: '' }
    default:
      return {}
  }
}

function generateStepId(): string {
  return `step-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`
}

const etlUploadRef = ref<UploadInstance>()

function handleEtlFileExceed(files: File[]): void {
  etlUploadRef.value?.clearFiles()
  const file = files[0] as UploadRawFile
  file.uid = genFileId()
  etlUploadRef.value?.handleStart(file)
}

function beforeEtlFileUpload(file: File): boolean {
  const ext = file.name.toLowerCase().split('.').pop() || ''
  if (!['xlsx', 'xls', 'csv'].includes(ext)) {
    ElMessage.error('仅支持 .xlsx / .xls / .csv 文件')
    return false
  }
  return true
}

async function handleEtlFileUpload(options: UploadRequestOptions): Promise<void> {
  if (!editingStep.value) return
  try {
    const res = await uploadEtlFile(options.file as File)
    editingStep.value.config.fileId = res.id
    editingStep.value.config.fileName = res.name
    editingStep.value.config.fileSize = res.size
    if (options.onSuccess) options.onSuccess(res)
    ElMessage.success('上传成功')
  } catch (err: any) {
    if (options.onError) options.onError(err)
    ElMessage.error(err?.message || '上传失败')
    throw err
  }
}

// ==================== 数据加载 ====================

async function loadTasks() {
  try {
    tasks.value = await getEtlTasks()
  } catch {
    ElMessage.error('加载任务列表失败')
  }
}

async function loadLogs(taskId: string) {
  try {
    logs.value = await getEtlLogs(taskId)
  } catch {
    logs.value = []
  }
}

// ==================== 任务操作 ====================

function handleSelectTask(task: EtlTask) {
  currentTaskId.value = task.id
  formData.value = {
    name: task.name,
    description: task.description || '',
    steps: JSON.parse(JSON.stringify(task.steps)),
  }
  runResult.value = null
  loadLogs(task.id)
}

function handleAddTask() {
  currentTaskId.value = '__new__'
  formData.value = {
    name: '',
    description: '',
    steps: [],
  }
  runResult.value = null
  logs.value = []
}

async function handleSave() {
  const valid = await formRef.value?.validate()
  if (!valid) return

  saveLoading.value = true
  try {
    const payload = {
      name: formData.value.name,
      description: formData.value.description,
      steps: formData.value.steps,
      enabled: true,
    }
    if (currentTaskId.value === '__new__') {
      const created = await createEtlTask(payload)
      currentTaskId.value = created.id
      ElMessage.success('创建成功')
    } else {
      await updateEtlTask(currentTaskId.value!, payload)
      ElMessage.success('保存成功')
    }
    await loadTasks()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.error || '保存失败')
  } finally {
    saveLoading.value = false
  }
}

function handleDeleteConfirm(task: EtlTask) {
  taskToDelete.value = task
  deleteDialogVisible.value = true
}

async function handleDelete() {
  if (!taskToDelete.value) return
  try {
    await deleteEtlTask(taskToDelete.value.id)
    ElMessage.success('删除成功')
    deleteDialogVisible.value = false
    if (currentTaskId.value === taskToDelete.value.id) {
      currentTaskId.value = null
    }
    await loadTasks()
  } catch {
    ElMessage.error('删除失败')
  }
}

// ==================== 步骤操作 ====================

function handleAddStep(type: string) {
  const step: EtlStep = {
    id: generateStepId(),
    type: type as EtlStep['type'],
    name: getStepTypeLabel(type),
    config: createDefaultConfig(type),
    onError: 'stop',
  }
  formData.value.steps.push(step)
  // 打开编辑弹窗
  editingStepIndex.value = formData.value.steps.length - 1
  editingStep.value = JSON.parse(JSON.stringify(step))
  stepDialogVisible.value = true
}

function handleInsertStep(index: number, type: string) {
  const step: EtlStep = {
    id: generateStepId(),
    type: type as EtlStep['type'],
    name: getStepTypeLabel(type),
    config: createDefaultConfig(type),
    onError: 'stop',
  }
  formData.value.steps.splice(index, 0, step)
  editingStepIndex.value = index
  editingStep.value = JSON.parse(JSON.stringify(step))
  stepDialogVisible.value = true
}

function handleEditStep(index: number) {
  editingStepIndex.value = index
  editingStep.value = JSON.parse(JSON.stringify(formData.value.steps[index]))
  stepDialogVisible.value = true
}

function handleStepConfirm() {
  if (editingStep.value && editingStepIndex.value >= 0) {
    formData.value.steps[editingStepIndex.value] = JSON.parse(JSON.stringify(editingStep.value))
  }
  stepDialogVisible.value = false
}

function handleRemoveStep(index: number) {
  formData.value.steps.splice(index, 1)
}

function handleMoveStep(index: number, direction: number) {
  const newIndex = index + direction
  if (newIndex < 0 || newIndex >= formData.value.steps.length) return
  const steps = formData.value.steps
  const temp = steps[index]
  steps[index] = steps[newIndex]
  steps[newIndex] = temp
}

// ==================== 执行 ====================

function handleDryRun() {
  doRun(true)
}

function handleRun() {
  runConfirmVisible.value = true
}

async function doRun(dryRun: boolean) {
  if (currentTaskId.value === '__new__') return

  // 先保存
  const valid = await formRef.value?.validate()
  if (!valid) return

  saveLoading.value = true
  try {
    await updateEtlTask(currentTaskId.value!, {
      name: formData.value.name,
      description: formData.value.description,
      steps: formData.value.steps,
      enabled: true,
    })
    await loadTasks()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.error || '保存失败')
    saveLoading.value = false
    return
  }
  saveLoading.value = false

  runLoading.value = true
  runResult.value = null
  try {
    const result = await runEtlTask(currentTaskId.value!, { dryRun })
    runResult.value = result
    if (result.status === 'success') {
      ElMessage.success(dryRun ? '测试运行成功' : '执行成功')
    } else if (result.status === 'partial') {
      ElMessage.warning('部分记录处理失败')
    } else {
      ElMessage.error('执行失败')
    }
    if (!dryRun) {
      await loadLogs(currentTaskId.value!)
      await loadTasks()
    }
  } catch (e: any) {
    const errData = e.response?.data
    if (errData) {
      runResult.value = errData
    }
    ElMessage.error(e.response?.data?.errors?.[0] || '执行出错')
  } finally {
    runLoading.value = false
  }
}

// ==================== 生命周期 ====================

onMounted(async () => {
  await loadTasks()
  if (pageConfigStore.pageConfigs.length === 0) {
    await pageConfigStore.fetchPageConfigs()
  }
})
</script>

<style scoped lang="scss">
.etl-manager {
  height: 100%;
}

.full-height {
  height: 100%;
}

.list-card,
.detail-card {
  height: 100%;

  :deep(.el-card__body) {
    height: calc(100% - 60px);
    overflow: auto;
  }
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

// ==================== 任务列表 ====================

.task-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.task-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  border: 1px solid #e4e7ed;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.2s;

  &:hover {
    border-color: #409eff;
    background-color: #f5f7fa;
  }

  &.active {
    border-color: #409eff;
    background-color: #ecf5ff;
  }

  .task-info {
    .task-name {
      font-weight: 500;
      color: #303133;
    }

    .task-meta {
      margin-top: 4px;
      display: flex;
      gap: 4px;
    }
  }

  .task-actions {
    opacity: 0;
    transition: opacity 0.2s;
  }

  &:hover .task-actions {
    opacity: 1;
  }
}

// ==================== 任务详情 ====================

.task-detail {
  .task-form {
    margin-bottom: 16px;
  }
}

// ==================== 管道编排 ====================

.pipeline-section {
  margin-bottom: 20px;
}

.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;

  .section-title {
    font-size: 15px;
    font-weight: 500;
    color: #303133;
  }
}

.pipeline-flow {
  display: flex;
  flex-direction: column;
  align-items: center;
}

.step-card {
  width: 100%;
  max-width: 600px;
  border: 1px solid #dcdfe6;
  border-radius: 8px;
  padding: 12px 16px;
  background: #fff;
  transition: border-color 0.2s;

  &:hover {
    border-color: #409eff;
  }

  .step-header {
    display: flex;
    align-items: center;
    gap: 8px;

    .step-icon {
      font-size: 18px;
    }

    .step-type-label {
      font-size: 12px;
      color: #909399;
      background: #f0f2f5;
      padding: 2px 8px;
      border-radius: 3px;
    }

    .step-name {
      flex: 1;
      font-weight: 500;
      color: #303133;
    }

    .step-actions {
      display: flex;
      gap: 2px;
      opacity: 0;
      transition: opacity 0.2s;
    }
  }

  &:hover .step-actions {
    opacity: 1;
  }

  .step-summary {
    margin-top: 8px;
    font-size: 12px;
    color: #909399;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.step-connector {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0;
  position: relative;

  .connector-line {
    width: 2px;
    height: 12px;
    background: #dcdfe6;
  }

  .insert-btn {
    width: 22px;
    height: 22px;
    font-size: 10px;
    color: #c0c4cc;
    border-color: #dcdfe6;

    &:hover {
      color: #409eff;
      border-color: #409eff;
    }
  }
}

// ==================== 操作区 ====================

.action-bar {
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
}

// ==================== 运行结果 ====================

.run-result {
  margin-bottom: 16px;

  .step-results {
    margin-top: 12px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .step-result-item {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 13px;
    padding: 4px 8px;
    background: #fafafa;
    border-radius: 4px;

    .sr-name {
      font-weight: 500;
      color: #303133;
    }

    .sr-count {
      color: #909399;
    }

    .sr-error {
      color: #f56c6c;
      font-size: 12px;
    }
  }

  .error-detail {
    margin-top: 12px;
    background: #fef0f0;
    border-radius: 4px;
    padding: 12px;
    font-size: 12px;
    color: #f56c6c;

    .error-line {
      margin-bottom: 4px;

      &:last-child {
        margin-bottom: 0;
      }
    }
  }
}

// ==================== 执行日志 ====================

.logs-section {
  .log-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .log-item {
    display: flex;
    align-items: center;
    gap: 12px;
    font-size: 13px;
    padding: 8px 12px;
    background: #fafafa;
    border-radius: 4px;

    .log-time {
      color: #909399;
      font-family: monospace;
    }

    .log-stat {
      color: #606266;
    }

    .log-errors {
      color: #f56c6c;
    }
  }
}

// ==================== 步骤配置弹窗 ====================

.kv-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.kv-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.arrow-icon {
  color: #909399;
  font-size: 16px;
}

.form-tip {
  font-size: 12px;
  color: #909399;
  margin-top: 4px;

  code {
    background: #f0f2f5;
    padding: 1px 4px;
    border-radius: 2px;
    font-family: 'Consolas', 'Monaco', monospace;
    font-size: 11px;
    color: #e6a23c;
  }
}

.code-form-item {
  :deep(.el-form-item__content) {
    display: block;
  }
}

.code-editor-wrapper {
  width: 100%;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
  overflow: hidden;

  :deep(.cm-editor) {
    font-size: 13px;

    .cm-scroller {
      font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
    }
  }
}
</style>
