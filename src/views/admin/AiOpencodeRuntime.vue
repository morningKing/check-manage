<template>
  <div class="oc-runtime">
    <!-- ── 状态条：生效状态 / serve 健康 / 进程归属 / 应用·重启 ── -->
    <div class="oc-runtime__header">
      <span class="oc-runtime__title">OpenCode 运行时管理</span>
      <div class="oc-runtime__status">
        <!-- 保存成功 ≠ 运行时生效：runtimeInSync 才代表 serve 已加载当前配置 -->
        <el-tag v-if="runtimeInfo" :type="runtimeInfo.runtimeInSync ? 'success' : 'warning'" size="small">
          {{ runtimeInfo.runtimeInSync ? '运行时已生效' : `${runtimeInfo.pendingCount} 项已保存待应用` }}
        </el-tag>
        <el-tag v-if="runtimeInfo" size="small" :type="OWNERSHIP_TAG[runtimeInfo.ownership.mode]?.type ?? 'info'">
          {{ OWNERSHIP_TAG[runtimeInfo.ownership.mode]?.label ?? runtimeInfo.ownership.mode }}
        </el-tag>
        <el-tag v-if="runtimeInfo" :type="runtimeInfo.serve.healthy ? 'success' : 'danger'" size="small">
          {{ runtimeInfo.serve.healthy ? `服务正常${runtimeInfo.serve.version ? ' · v' + runtimeInfo.serve.version : ''}` : '服务不可达' }}
        </el-tag>
        <el-tooltip content="修改 skill/agent 文件后必须重启 opencode serve 才生效（OpenCode 启动时一次性加载配置，无热加载）">
          <el-icon class="oc-runtime__help"><QuestionFilled /></el-icon>
        </el-tooltip>
        <el-button v-if="canPerm('admin.ai_runtime_apply')" type="warning" plain size="small"
                   :loading="applying" :disabled="pendingCount === 0"
                   @click="onApply">应用配置</el-button>
        <el-button v-if="canPerm('admin.ai_runtime_restart')" type="danger" plain size="small"
                   :loading="restarting" @click="onRestart">重启 OpenCode</el-button>
        <el-button size="small" @click="refreshAll">刷新</el-button>
      </div>
    </div>
    <p class="oc-runtime__desc">
      直接管理 OpenCode 全局目录中的技能与 Agent（<code>{{ overview?.globalDir || '~/.config/opencode' }}</code>），
      对该机器上所有 OpenCode 会话生效。平台「AI 技能管理」按会话注入、即传即用，两者互不影响。
    </p>

    <el-tabs v-model="activeTab">
      <!-- ─────────────── Skill 页签 ─────────────── -->
      <el-tab-pane label="技能 (Skill)" name="skills">
        <div class="oc-runtime__toolbar">
          <el-button v-if="canPerm('admin.ai_skill_write')" type="primary" size="small" @click="openSkillCreate">新建技能</el-button>
          <el-button v-if="canPerm('admin.ai_skill_write')" size="small" @click="showZipUpload = true">上传 zip</el-button>
          <el-button v-if="canPerm('admin.ai_runtime_publish')" size="small" @click="openPublish">从平台技能库发布</el-button>
        </div>
        <el-table :data="skillItems" v-loading="loadingSkills" size="small">
          <el-table-column prop="name" label="名称" min-width="160" show-overflow-tooltip />
          <el-table-column prop="description" label="描述" min-width="220" show-overflow-tooltip />
          <el-table-column label="来源" width="100">
            <template #default="{ row }">
              <el-tag size="small" :type="SOURCE_TAG[row.source]?.type ?? 'info'">
                {{ SOURCE_TAG[row.source]?.label ?? row.source }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="生效状态" width="110">
            <template #default="{ row }">
              <el-tag size="small" :type="RUNTIME_TAG[row.runtime]?.type ?? 'info'">
                {{ RUNTIME_TAG[row.runtime]?.label ?? row.runtime }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="修改时间" width="160">
            <template #default="{ row }">{{ fmtTime(row.mtime) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="180" fixed="right">
            <template #default="{ row }">
              <template v-if="row.source === 'global' && canPerm('admin.ai_skill_write')">
                <el-button link type="primary" @click="openSkillEdit(row)">编辑</el-button>
                <el-button link type="primary" @click="openFiles(row)">文件</el-button>
                <el-button link type="danger" @click="onDeleteSkill(row)">删除</el-button>
              </template>
              <span v-else class="oc-runtime__readonly-hint">只读</span>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- ─────────────── Agent 页签 ─────────────── -->
      <el-tab-pane label="智能体 (Agent)" name="agents">
        <div class="oc-runtime__toolbar">
          <el-button v-if="canPerm('admin.ai_agent_write')" type="primary" size="small" @click="openAgentCreate">新建 Agent</el-button>
        </div>
        <el-table :data="agentItems" v-loading="loadingAgents" size="small">
          <el-table-column prop="name" label="名称" min-width="150" show-overflow-tooltip />
          <el-table-column prop="description" label="描述" min-width="200" show-overflow-tooltip />
          <el-table-column label="模式" width="95">
            <template #default="{ row }">
              <el-tag v-if="row.mode" size="small" :type="row.mode === 'subagent' ? 'info' : 'primary'">
                {{ row.mode }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="model" label="模型" min-width="130" show-overflow-tooltip />
          <el-table-column label="来源" width="95">
            <template #default="{ row }">
              <el-tag size="small" :type="SOURCE_TAG[row.source]?.type ?? 'info'">
                {{ SOURCE_TAG[row.source]?.label ?? row.source }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="生效状态" width="110">
            <template #default="{ row }">
              <el-tag size="small" :type="RUNTIME_TAG[row.runtime]?.type ?? 'info'">
                {{ RUNTIME_TAG[row.runtime]?.label ?? row.runtime }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="170" fixed="right">
            <template #default="{ row }">
              <template v-if="canPerm('admin.ai_agent_write')">
                <el-button v-if="row.source === 'builtin'" link type="warning"
                           @click="onToggleAgent(row, true)">禁用</el-button>
                <template v-else-if="row.source === 'file'">
                  <el-button v-if="row.runtime === 'disabled'" link type="success"
                             @click="onToggleAgent(row, false)">启用</el-button>
                  <el-button v-else link type="primary" @click="openAgentEdit(row)">编辑</el-button>
                  <el-button link type="danger" @click="onDeleteAgent(row)">删除</el-button>
                </template>
                <span v-else class="oc-runtime__readonly-hint">只读（插件）</span>
              </template>
              <span v-else class="oc-runtime__readonly-hint">只读</span>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <!-- ── Skill 编辑/新建 dialog ── -->
    <el-dialog v-model="showSkillDialog" :title="skillForm.creating ? '新建技能' : `编辑技能 — ${skillForm.name}`"
               width="720px" destroy-on-close>
      <el-form label-width="80px">
        <el-form-item label="名称">
          <el-input v-model="skillForm.name" :disabled="!skillForm.creating"
                    placeholder="小写字母/数字/连字符，如 my-skill" />
        </el-form-item>
        <el-form-item label="编辑方式">
          <el-radio-group v-model="skillForm.raw" @change="onSkillModeChange">
            <el-radio-button :value="false">表单</el-radio-button>
            <el-radio-button :value="true">源码 (SKILL.md)</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <template v-if="!skillForm.raw">
          <el-form-item label="描述">
            <el-input v-model="skillForm.description" type="textarea" :rows="2"
                      placeholder="必填：做什么 + 何时触发（OpenCode 会过滤没有描述的技能）" />
          </el-form-item>
          <el-form-item label="正文">
            <el-input v-model="skillForm.body" type="textarea" :rows="12" class="oc-runtime__code"
                      placeholder="# 技能指令正文 (Markdown)" />
          </el-form-item>
        </template>
        <el-form-item v-else label="内容">
          <el-input v-model="skillForm.content" type="textarea" :rows="16" class="oc-runtime__code"
                    placeholder="---&#10;name: my-skill&#10;description: ...&#10;---&#10;正文" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showSkillDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveSkill">保存</el-button>
      </template>
    </el-dialog>

    <!-- ── zip 上传 dialog ── -->
    <el-dialog v-model="showZipUpload" title="上传技能 zip 到 OpenCode 全局" width="480px" destroy-on-close>
      <el-upload :auto-upload="false" :limit="1" accept=".zip" :on-change="onZipChange"
                 :on-remove="() => (zipFile = null)" drag>
        <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
        <div class="el-upload__text">拖拽或点击选择 zip 文件</div>
        <template #tip>
          <div class="el-upload__tip">需包含 SKILL.md（frontmatter name 与目录一致），最大 5 MB</div>
        </template>
      </el-upload>
      <div class="oc-runtime__overwrite-row">
        <el-checkbox v-model="zipOverwrite">覆盖同名技能</el-checkbox>
        <span class="oc-runtime__hint">勾选后，同名技能（含 skills/ 复数目录下的）会被整体替换</span>
      </div>
      <template #footer>
        <el-button @click="showZipUpload = false">取消</el-button>
        <el-button type="primary" :loading="saving" :disabled="!zipFile" @click="doZipUpload">上传</el-button>
      </template>
    </el-dialog>

    <!-- ── 从平台技能库发布 dialog ── -->
    <el-dialog v-model="showPublish" title="发布平台技能到 OpenCode 全局" width="480px" destroy-on-close>
      <el-form label-width="90px">
        <el-form-item label="平台技能">
          <el-select v-model="publishSkillId" placeholder="选择要发布的技能" style="width: 100%">
            <el-option v-for="s in platformSkills" :key="s.id" :value="s.id"
                       :label="`${s.name}${s.description ? ' — ' + s.description : ''}`" />
          </el-select>
        </el-form-item>
        <el-form-item label="覆盖同名">
          <el-switch v-model="publishOverwrite" />
        </el-form-item>
      </el-form>
      <p class="oc-runtime__hint">发布即复制到 OpenCode 全局目录，之后两处各自独立维护。</p>
      <template #footer>
        <el-button @click="showPublish = false">取消</el-button>
        <el-button type="primary" :loading="saving" :disabled="!publishSkillId" @click="doPublish">
          发布
        </el-button>
      </template>
    </el-dialog>

    <!-- ── skill 附属文件管理 dialog ── -->
    <el-dialog v-model="showFiles" :title="`技能文件 — ${filesSkill}（脚本/模板等附属文件）`"
               width="680px" destroy-on-close>
      <div class="oc-runtime__toolbar">
        <el-button size="small" @click="openFileCreate">新建文本文件</el-button>
        <span class="oc-runtime__hint">脚本类文件保存后对之后的 Agent 运行即时生效；SKILL.md 变更需重启 OpenCode。</span>
      </div>
      <el-table :data="skillFiles" v-loading="filesLoading" size="small" max-height="380">
        <el-table-column prop="path" label="路径" min-width="240" show-overflow-tooltip />
        <el-table-column label="大小" width="100">
          <template #default="{ row }">{{ formatSize(row.size) }}</template>
        </el-table-column>
        <el-table-column label="修改时间" width="160">
          <template #default="{ row }">{{ fmtTime(row.mtime) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openFileEdit(row)">
              {{ row.path.toUpperCase() === 'SKILL.MD' ? '编辑' : '查看/编辑' }}
            </el-button>
            <el-button link type="danger" @click="onDeleteFile(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>

    <!-- ── 附属文件编辑/新建 dialog ── -->
    <el-dialog v-model="showFileEdit"
               :title="fileForm.creating ? `新建文件 — ${filesSkill}` : `编辑文件 — ${filesSkill}/${fileForm.path}`"
               width="760px" destroy-on-close :close-on-click-modal="false">
      <el-alert v-if="fileForm.truncated" type="warning" :closable="false" show-icon
                title="文件超过 256 KB，仅展示前 256 KB。为避免数据丢失，截断内容不可保存，请本地修改后重新上传技能。"
                class="oc-runtime__alert" />
      <el-form v-if="fileForm.creating" label-width="80px">
        <el-form-item label="路径">
          <el-input v-model="fileForm.path" placeholder="相对路径，如 scripts/run.py" />
        </el-form-item>
      </el-form>
      <el-input v-model="fileForm.content" type="textarea" :rows="18" class="oc-runtime__code"
                :disabled="fileForm.truncated" placeholder="文件内容（文本文件）" />
      <template #footer>
        <el-button @click="showFileEdit = false">取消</el-button>
        <el-button type="primary" :loading="saving" :disabled="fileForm.truncated || !fileForm.path.trim()"
                   @click="saveFile">保存</el-button>
      </template>
    </el-dialog>

    <!-- ── Agent 编辑/新建 dialog ── -->
    <el-dialog v-model="showAgentDialog" :title="agentForm.creating ? '新建 Agent' : `编辑 Agent — ${agentForm.name}`"
               width="760px" destroy-on-close>
      <el-form label-width="90px">
        <el-form-item label="名称">
          <el-input v-model="agentForm.name" :disabled="!agentForm.creating"
                    placeholder="字母/数字开头，如 code-reviewer" />
        </el-form-item>
        <el-form-item label="编辑方式">
          <el-radio-group v-model="agentForm.raw" @change="onAgentModeChange">
            <el-radio-button :value="false">表单</el-radio-button>
            <el-radio-button :value="true">源码 (Markdown)</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <template v-if="!agentForm.raw">
          <el-form-item label="描述">
            <el-input v-model="agentForm.description" type="textarea" :rows="2"
                      placeholder="必填：何时使用该 Agent" />
          </el-form-item>
          <el-form-item label="模式">
            <el-radio-group v-model="agentForm.mode">
              <el-radio value="primary">primary（主对话可选）</el-radio>
              <el-radio value="subagent">subagent（@提及委托）</el-radio>
              <el-radio value="all">all</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="模型">
            <el-input v-model="agentForm.model" placeholder="providerID/modelID，留空用默认" />
          </el-form-item>
          <el-form-item label="温度 / top_p">
            <el-input v-model="agentForm.temperature" placeholder="如 0.7" style="width: 140px" />
            <el-input v-model="agentForm.topP" placeholder="如 0.9" style="width: 140px; margin-left: 8px" />
          </el-form-item>
          <el-form-item label="Prompt">
            <el-input v-model="agentForm.body" type="textarea" :rows="10" class="oc-runtime__code"
                      placeholder="Agent 的系统提示词（Markdown 正文）" />
          </el-form-item>
        </template>
        <el-form-item v-else label="内容">
          <el-input v-model="agentForm.content" type="textarea" :rows="16" class="oc-runtime__code"
                    placeholder="---&#10;name: my-agent&#10;description: ...&#10;mode: primary&#10;---&#10;Prompt 正文" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showAgentDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveAgent">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { QuestionFilled, UploadFilled } from '@element-plus/icons-vue'
import * as api from '@/api/aiOpencodeAdmin'
import type {
  OpencodeOverview, GlobalSkillItem, GlobalAgentItem,
} from '@/api/aiOpencodeAdmin'
import { listPlatformSkills } from '@/api/aiOpencodeAdmin'
import type { GlobalSkill } from '@/api/aiSkills'
import { useAuthStore } from '@/stores/auth'

const SOURCE_TAG: Record<string, { label: string; type: 'primary' | 'success' | 'warning' | 'info' }> = {
  global: { label: '全局目录', type: 'primary' },
  file: { label: '全局目录', type: 'primary' },
  builtin: { label: '内置', type: 'success' },
  plugin: { label: '插件', type: 'warning' },
  external: { label: '外部/插件', type: 'info' },
}
const RUNTIME_TAG: Record<string, { label: string; type: 'success' | 'warning' | 'info' | 'danger' }> = {
  loaded: { label: '已生效', type: 'success' },
  pending: { label: '待重启', type: 'warning' },
  disabled: { label: '已禁用', type: 'info' },
  serveOffline: { label: '服务离线', type: 'danger' },
}
// serve 进程归属（Spec §11）：platform 才允许平台重启；external/unknown 提示
// 需手动处理，避免误杀外部实例。
const OWNERSHIP_TAG: Record<string, { label: string; type: 'success' | 'warning' | 'info' | 'danger' }> = {
  platform: { label: '平台托管', type: 'success' },
  external: { label: '外部进程', type: 'danger' },
  unknown: { label: '归属未知', type: 'warning' },
  none: { label: '无监听', type: 'info' },
}

const auth = useAuthStore()
/** 后端为真正的权限判定方；这里只控制界面入口（Spec §12）。 */
function canPerm(key: string): boolean {
  return auth.can(key)
}

const overview = ref<OpencodeOverview | null>(null)
const runtimeInfo = ref<api.RuntimeStatusInfo | null>(null)
const activeTab = ref<'skills' | 'agents'>('skills')
const skillItems = ref<GlobalSkillItem[]>([])
const agentItems = ref<GlobalAgentItem[]>([])
const loadingSkills = ref(false)
const loadingAgents = ref(false)
const saving = ref(false)
const restarting = ref(false)
const applying = ref(false)

const pendingCount = computed(() => runtimeInfo.value?.pendingCount
  ?? overview.value?.pendingChanges ?? 0)

async function refreshOverview() {
  try {
    overview.value = await api.getOpencodeOverview()
  } catch { /* 表格区已有独立报错 */ }
  try {
    runtimeInfo.value = await api.getRuntimeStatus()
  } catch { /* 状态标签保留上次值 */ }
}
async function refreshSkills() {
  loadingSkills.value = true
  try {
    skillItems.value = (await api.listGlobalOpencodeSkills()).items
  } catch (e: unknown) { ElMessage.error(errText(e)) } finally { loadingSkills.value = false }
}
async function refreshAgents() {
  loadingAgents.value = true
  try {
    agentItems.value = (await api.listGlobalOpencodeAgents()).items
  } catch (e: unknown) { ElMessage.error(errText(e)) } finally { loadingAgents.value = false }
}
function refreshAll() {
  refreshOverview()
  refreshSkills()
  refreshAgents()
}
onMounted(refreshAll)

// ── Skill 编辑 ──────────────────────────────────────────────
const showSkillDialog = ref(false)
const skillForm = ref({
  creating: true, name: '', description: '', body: '', content: '', raw: false,
})

function openSkillCreate() {
  skillForm.value = { creating: true, name: '', description: '', body: '', content: '', raw: false }
  showSkillDialog.value = true
}
async function openSkillEdit(row: GlobalSkillItem) {
  try {
    const d = await api.getGlobalOpencodeSkill(row.name)
    skillForm.value = {
      creating: false, name: d.name, description: d.description, body: d.body,
      content: d.content, raw: false,
    }
    showSkillDialog.value = true
  } catch (e: unknown) { ElMessage.error(errText(e)) }
}
function onSkillModeChange(toRaw: boolean) {
  // 切到源码时用当前表单内容拼一份完整文档，避免丢字
  if (toRaw && !skillForm.value.content) {
    skillForm.value.content = [
      '---', `name: ${skillForm.value.name}`,
      `description: ${JSON.stringify(skillForm.value.description || '')}`, '---',
      skillForm.value.body,
    ].join('\n')
  }
}
async function saveSkill() {
  saving.value = true
  try {
    const f = skillForm.value
    if (f.creating) {
      await api.createGlobalOpencodeSkill({
        name: f.name.trim(), description: f.description, body: f.body,
      })
    } else if (f.raw) {
      await api.updateGlobalOpencodeSkill(f.name, { content: f.content })
    } else {
      await api.updateGlobalOpencodeSkill(f.name, { description: f.description, body: f.body })
    }
    ElMessage.success('已保存，需重启 OpenCode 后生效')
    showSkillDialog.value = false
    refreshSkills()
    refreshOverview()
  } catch (e: unknown) { ElMessage.error(errText(e)) } finally { saving.value = false }
}

async function onDeleteSkill(row: GlobalSkillItem) {
  try {
    await ElMessageBox.confirm(`删除全局技能「${row.name}」？该目录将从磁盘移除。`, '删除确认', { type: 'warning' })
  } catch { return }
  try {
    await api.deleteGlobalOpencodeSkill(row.name)
    ElMessage.success('已删除')
    refreshSkills()
    refreshOverview()
  } catch (e: unknown) { ElMessage.error(errText(e)) }
}

// ── zip 上传 ──
const showZipUpload = ref(false)
const zipFile = ref<File | null>(null)
const zipOverwrite = ref(false)
function onZipChange(file: { raw?: File }) { zipFile.value = file.raw ?? null }
async function doZipUpload() {
  if (!zipFile.value) return
  saving.value = true
  try {
    const r = await api.uploadGlobalOpencodeSkillZip(zipFile.value, zipOverwrite.value)
    ElMessage.success(`已安装「${r.name}」，需重启 OpenCode 后生效`)
    showZipUpload.value = false
    zipFile.value = null
    zipOverwrite.value = false
    refreshSkills()
    refreshOverview()
  } catch (e: unknown) { ElMessage.error(errText(e)) } finally { saving.value = false }
}

// ── 从平台技能库发布 ──
const showPublish = ref(false)
const publishSkillId = ref('')
const publishOverwrite = ref(false)
const platformSkills = ref<GlobalSkill[]>([])
async function openPublish() {
  showPublish.value = true
  if (!platformSkills.value.length) {
    try {
      platformSkills.value = (await listPlatformSkills()).skills
    } catch (e: unknown) { ElMessage.error(errText(e)) }
  }
}
async function doPublish() {
  saving.value = true
  try {
    const r = await api.publishPlatformSkill(publishSkillId.value, publishOverwrite.value)
    ElMessage.success(`已发布「${r.name}」，需重启 OpenCode 后生效`)
    showPublish.value = false
    refreshSkills()
    refreshOverview()
  } catch (e: unknown) { ElMessage.error(errText(e)) } finally { saving.value = false }
}

// ── skill 附属文件管理 ──
const showFiles = ref(false)
const filesSkill = ref('')
const skillFiles = ref<api.SkillFileInfo[]>([])
const filesLoading = ref(false)
const showFileEdit = ref(false)
const fileForm = ref({ creating: false, path: '', content: '', truncated: false })

async function openFiles(row: GlobalSkillItem) {
  filesSkill.value = row.name
  showFiles.value = true
  filesLoading.value = true
  try {
    skillFiles.value = (await api.listSkillFiles(row.name)).files
  } catch (e: unknown) { ElMessage.error(errText(e)) } finally { filesLoading.value = false }
}

function openFileCreate() {
  fileForm.value = { creating: true, path: '', content: '', truncated: false }
  showFileEdit.value = true
}

async function openFileEdit(row: api.SkillFileInfo) {
  try {
    const d = await api.readSkillFile(filesSkill.value, row.path)
    fileForm.value = {
      creating: false, path: row.path, content: d.content,
      truncated: d.truncated,
    }
    if (d.binary) {
      ElMessage.info('二进制文件不支持在线编辑')
      return
    }
    showFileEdit.value = true
  } catch (e: unknown) { ElMessage.error(errText(e)) }
}

async function saveFile() {
  saving.value = true
  try {
    await api.writeSkillFile(filesSkill.value, fileForm.value.path.trim(),
                             fileForm.value.content)
    ElMessage.success('已保存（脚本类文件即时生效；SKILL.md 变更需重启 OpenCode）')
    showFileEdit.value = false
    skillFiles.value = (await api.listSkillFiles(filesSkill.value)).files
  } catch (e: unknown) { ElMessage.error(errText(e)) } finally { saving.value = false }
}

async function onDeleteFile(row: api.SkillFileInfo) {
  try {
    await ElMessageBox.confirm(`删除文件「${row.path}」？`, '删除确认', { type: 'warning' })
  } catch { return }
  try {
    await api.deleteSkillFile(filesSkill.value, row.path)
    ElMessage.success('已删除')
    skillFiles.value = (await api.listSkillFiles(filesSkill.value)).files
  } catch (e: unknown) { ElMessage.error(errText(e)) }
}

function formatSize(n: number | null): string {
  if (n == null) return '—'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

// ── Agent 编辑 ──────────────────────────────────────────────
const showAgentDialog = ref(false)
const agentForm = ref({
  creating: true, name: '', description: '', mode: 'subagent', model: '',
  temperature: '', topP: '', body: '', content: '', raw: false,
})

function openAgentCreate() {
  agentForm.value = {
    creating: true, name: '', description: '', mode: 'subagent', model: '',
    temperature: '', topP: '', body: '', content: '', raw: false,
  }
  showAgentDialog.value = true
}
async function openAgentEdit(row: GlobalAgentItem) {
  try {
    const d = await api.getGlobalOpencodeAgent(row.fileName || row.name)
    const m = d.meta as Record<string, unknown>
    agentForm.value = {
      creating: false, name: d.name,
      description: String(m.description ?? ''), mode: String(m.mode ?? 'subagent'),
      model: String(m.model ?? ''),
      temperature: m.temperature != null ? String(m.temperature) : '',
      topP: m.top_p != null ? String(m.top_p) : '',
      body: d.body, content: d.content, raw: false,
    }
    showAgentDialog.value = true
  } catch (e: unknown) { ElMessage.error(errText(e)) }
}
function onAgentModeChange(toRaw: boolean) {
  if (toRaw && !agentForm.value.content) {
    const f = agentForm.value
    const lines = ['---', `name: ${f.name}`, `description: ${JSON.stringify(f.description || '')}`]
    if (f.mode) lines.push(`mode: ${f.mode}`)
    if (f.model) lines.push(`model: ${f.model}`)
    lines.push('---', f.body)
    f.content = lines.join('\n')
  }
}
async function saveAgent() {
  saving.value = true
  try {
    const f = agentForm.value
    const structured = {
      description: f.description, mode: f.mode, model: f.model.trim(),
      temperature: f.temperature ? Number(f.temperature) : null,
      topP: f.topP ? Number(f.topP) : null, body: f.body,
    }
    if (f.creating) {
      await api.createGlobalOpencodeAgent({ name: f.name.trim(), ...structured })
    } else if (f.raw) {
      await api.updateGlobalOpencodeAgent(f.name, { content: f.content })
    } else {
      await api.updateGlobalOpencodeAgent(f.name, structured)
    }
    ElMessage.success('已保存，需重启 OpenCode 后生效')
    showAgentDialog.value = false
    refreshAgents()
    refreshOverview()
  } catch (e: unknown) { ElMessage.error(errText(e)) } finally { saving.value = false }
}

async function onDeleteAgent(row: GlobalAgentItem) {
  try {
    await ElMessageBox.confirm(
      `删除 Agent 文件「${row.fileName}」？若是内置开关文件则等于恢复启用。`, '删除确认', { type: 'warning' })
  } catch { return }
  try {
    await api.deleteGlobalOpencodeAgent(row.fileName || row.name)
    ElMessage.success('已删除')
    refreshAgents()
    refreshOverview()
  } catch (e: unknown) { ElMessage.error(errText(e)) }
}

async function onToggleAgent(row: GlobalAgentItem, disable: boolean) {
  try {
    await api.setGlobalOpencodeAgentDisabled(row.fileName || row.name, disable)
    ElMessage.success(disable ? '已禁用（重启后生效）' : '已启用（重启后生效）')
    refreshAgents()
    refreshOverview()
  } catch (e: unknown) { ElMessage.error(errText(e)) }
}

// ── 应用 / 重启 ──
// 外部进程（非平台托管）不允许平台重启，按钮点了也会被后端拒绝——提前提示。
const ownershipBlocked = computed(() =>
  ['external', 'unknown'].includes(runtimeInfo.value?.ownership.mode || ''))

async function onApply() {
  if (ownershipBlocked.value) {
    ElMessage.warning('当前 serve 不是平台托管进程，请先手动停止后由平台拉起，再应用配置')
    return
  }
  applying.value = true
  try {
    const r = await api.applyRuntimeConfig()
    ElMessage.success(`配置已应用（v${r.version ?? '?'}），运行时已加载最新内容`)
    refreshAll()
  } catch (e: unknown) { ElMessage.error(errText(e)) } finally { applying.value = false }
}

async function onRestart() {
  restarting.value = true
  try {
    const rt = runtimeInfo.value ?? await api.getRuntimeStatus()
    if (['external', 'unknown'].includes(rt.ownership.mode)) {
      ElMessage.error(`端口上的 OpenCode 进程（PID ${rt.ownership.pid ?? '?'}）不是平台启动的，平台不会自动重启它；请手动停止后再试`)
      return
    }
    const w = rt.activeWorkload
    const busy = w.batchChildren + w.interactiveSessions
    // 空闲 → 普通重启；有运行中负载 → 只有持 admin.ai_runtime_force 的管理员
    // 走强制路径（Spec §10.3 强制模式：默认禁止，需高危权限 + 明确二次确认）。
    const force = busy > 0
    if (force && !canPerm('admin.ai_runtime_force')) {
      ElMessage.warning('当前有正在运行的 AI 会话/批任务，普通重启会被拒绝；强制中断需要「OpenCode 强制重启」权限（可用「应用配置」在空闲时生效）')
      return
    }
    const msg = force
      ? `当前有 ${busy} 个运行中的 AI 会话/批任务（含 ${w.batchChildren} 个批任务子任务），强制重启会立即中断它们，相关会话将被标记为中断。确定强制重启？`
      : '重启 opencode serve 以加载最新的 skill/agent 变更？'
    try {
      await ElMessageBox.confirm(msg, force ? '强制重启 OpenCode' : '重启 OpenCode',
                                 { type: 'warning', confirmButtonText: force ? '强制重启' : '重启' })
    } catch { return }
    const r = await api.restartOpencodeServe(force)
    ElMessage.success(`OpenCode 已重启（v${r.version ?? '?'}），变更已生效`)
    refreshAll()
  } catch (e: unknown) { ElMessage.error(errText(e)) } finally { restarting.value = false }
}

// ── utils ──
function fmtTime(ts: number | null): string {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleString()
}
function errText(e: unknown): string {
  const err = e as { response?: { data?: { error?: string } }; message?: string }
  return err?.response?.data?.error || err?.message || '操作失败'
}
</script>

<style scoped>
.oc-runtime__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
}
.oc-runtime__title {
  font-size: 16px;
  font-weight: 600;
}
.oc-runtime__status {
  display: flex;
  align-items: center;
  gap: 12px;
}
.oc-runtime__pending-badge {
  margin-right: 4px;
}
.oc-runtime__help {
  color: var(--el-text-color-secondary);
  cursor: help;
}
.oc-runtime__desc {
  color: var(--el-text-color-secondary);
  font-size: 13px;
  margin: 8px 0 4px;
}
.oc-runtime__desc code {
  background: var(--el-fill-color-light);
  padding: 1px 5px;
  border-radius: 3px;
}
.oc-runtime__toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 10px;
}
.oc-runtime__readonly-hint {
  color: var(--el-text-color-placeholder);
  font-size: 12px;
}
.oc-runtime__code :deep(textarea) {
  font-family: Consolas, Monaco, 'Courier New', monospace;
  font-size: 12.5px;
  line-height: 1.5;
}
.oc-runtime__hint {
  color: var(--el-text-color-secondary);
  font-size: 12.5px;
  margin: 0 0 8px;
}
.oc-runtime__alert {
  margin-bottom: 12px;
}
.oc-runtime__overwrite-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
}
</style>
