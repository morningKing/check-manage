<!-- 平台全局技能面板：原「AI 全局技能与 MCP 服务管理」页主体，现挂在
     OpenCode 运行时管理页的「平台技能」页签下。
     平台技能按会话注入、即传即用，与 OpenCode 全局目录技能互不影响。 -->
<template>
  <div class="platform-skills">
    <div class="platform-skills__toolbar">
      <el-button type="primary" size="small" data-test="platform-skill-upload" @click="showUpload = true">
        上传技能
      </el-button>
      <span class="platform-skills__hint">
        上传后对所有新建 AI 会话（交互式与批任务）自动可用，即传即用、无需重启。
      </span>
    </div>

    <el-table :data="skills" v-loading="loading" size="small">
      <el-table-column prop="name" label="名称" width="180">
        <template #default="{ row }">
          <span class="platform-skills__name">{{ row.name }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="description" label="描述" min-width="240" show-overflow-tooltip />
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag size="small" :type="row.enabled ? 'success' : 'info'">
            {{ row.enabled ? '启用' : '禁用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="大小" width="100">
        <template #default="{ row }">{{ formatSize(row.fileSize) }}</template>
      </el-table-column>
      <el-table-column prop="uploadedBy" label="上传者" width="120" />
      <el-table-column label="创建时间" width="170">
        <template #default="{ row }">{{ fmt(row.createdAt) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="200" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openFiles(row)">文件</el-button>
          <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
          <el-switch v-model="row.enabled" size="small" style="margin: 0 8px"
                     @change="onToggle(row)" />
          <el-button link type="danger" @click="onDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- Upload dialog -->
    <el-dialog v-model="showUpload" title="上传全局技能" width="480px" destroy-on-close>
      <el-form label-width="80px">
        <el-form-item label="技能文件">
          <el-upload :auto-upload="false" :limit="1" accept=".zip"
                     :on-change="onFileChange" :file-list="uploadFileList">
            <el-button>选择 zip 文件</el-button>
            <template #tip>
              <div class="el-upload__tip">zip 包需包含 SKILL.md 文件（含 name frontmatter），最大 5 MB</div>
            </template>
          </el-upload>
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="uploadDesc" type="textarea" :rows="2" placeholder="技能用途说明" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showUpload = false">取消</el-button>
        <el-button type="primary" :loading="uploading" :disabled="!uploadFile" @click="doUpload">
          上传
        </el-button>
      </template>
    </el-dialog>

    <!-- Edit dialog -->
    <el-dialog v-model="showEdit" title="编辑技能" width="480px" destroy-on-close>
      <el-form label-width="80px">
        <el-form-item label="名称">
          <el-input :model-value="editTarget?.name" disabled />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="editDesc" type="textarea" :rows="3" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showEdit = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="doSave">保存</el-button>
      </template>
    </el-dialog>

    <!-- Files dialog -->
    <el-dialog v-model="showFiles" :title="`技能文件 — ${filesTarget?.name}`" width="600px"
               destroy-on-close>
      <div v-loading="filesLoading">
        <div v-if="skillFiles.length === 0" class="el-text-color-secondary">无文件</div>
        <div v-for="f in skillFiles" :key="f.path" class="platform-skills__file-item"
             @click="previewFile(f)">
          <span class="platform-skills__file-name">{{ f.name }}</span>
          <span class="platform-skills__file-path el-text-color-secondary">{{ f.path }}</span>
          <span class="platform-skills__file-size el-text-color-secondary">{{ formatSize(f.size) }}</span>
        </div>
      </div>
    </el-dialog>

    <!-- File preview dialog -->
    <el-dialog v-model="showPreview" :title="previewPath" width="700px" destroy-on-close>
      <pre v-if="previewContent" class="platform-skills__preview">{{ previewContent }}</pre>
      <div v-else-if="previewBinary" class="el-text-color-secondary">二进制文件，无法预览</div>
      <div v-else v-loading="true" style="height: 100px" />
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  listGlobalSkills, updateGlobalSkill, deleteGlobalSkill,
  listSkillFiles, readSkillFile, uploadGlobalSkill,
  type GlobalSkill, type GlobalSkillFile,
} from '@/api/aiSkills'

const skills = ref<GlobalSkill[]>([])
const loading = ref(false)

// Upload
const showUpload = ref(false)
const uploadFile = ref<File | null>(null)
const uploadFileList = ref<any[]>([])
const uploadDesc = ref('')
const uploading = ref(false)

// Edit
const showEdit = ref(false)
const editTarget = ref<GlobalSkill | null>(null)
const editDesc = ref('')
const saving = ref(false)

// Files
const showFiles = ref(false)
const filesTarget = ref<GlobalSkill | null>(null)
const skillFiles = ref<GlobalSkillFile[]>([])
const filesLoading = ref(false)

// Preview
const showPreview = ref(false)
const previewPath = ref('')
const previewContent = ref('')
const previewBinary = ref(false)

async function fetchList() {
  loading.value = true
  try {
    const res = await listGlobalSkills()
    skills.value = res.skills
  } catch {
    skills.value = []
  } finally {
    loading.value = false
  }
}

function onFileChange(file: any) {
  uploadFile.value = file.raw
}

async function doUpload() {
  if (!uploadFile.value) return
  uploading.value = true
  try {
    await uploadGlobalSkill(uploadFile.value, uploadDesc.value)
    ElMessage.success('上传成功')
    showUpload.value = false
    uploadFile.value = null
    uploadFileList.value = []
    uploadDesc.value = ''
    await fetchList()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.error || e?.message || '上传失败')
  } finally {
    uploading.value = false
  }
}

function openEdit(skill: GlobalSkill) {
  editTarget.value = skill
  editDesc.value = skill.description
  showEdit.value = true
}

async function doSave() {
  if (!editTarget.value) return
  saving.value = true
  try {
    await updateGlobalSkill(editTarget.value.id, { description: editDesc.value })
    ElMessage.success('已保存')
    showEdit.value = false
    await fetchList()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.error || '保存失败')
  } finally {
    saving.value = false
  }
}

async function onToggle(skill: GlobalSkill) {
  try {
    await updateGlobalSkill(skill.id, { enabled: skill.enabled })
  } catch {
    skill.enabled = !skill.enabled
    ElMessage.error('操作失败')
  }
}

async function onDelete(skill: GlobalSkill) {
  try {
    await ElMessageBox.confirm(`确定删除全局技能「${skill.name}」？`, '确认')
    await deleteGlobalSkill(skill.id)
    ElMessage.success('已删除')
    await fetchList()
  } catch { /* cancelled */ }
}

async function openFiles(skill: GlobalSkill) {
  filesTarget.value = skill
  showFiles.value = true
  filesLoading.value = true
  try {
    const res = await listSkillFiles(skill.id)
    skillFiles.value = res.files
  } catch {
    skillFiles.value = []
  } finally {
    filesLoading.value = false
  }
}

async function previewFile(f: GlobalSkillFile) {
  if (!filesTarget.value) return
  previewPath.value = f.path
  previewContent.value = ''
  previewBinary.value = false
  showPreview.value = true
  try {
    const res = await readSkillFile(filesTarget.value.id, f.path)
    if (res.binary) {
      previewBinary.value = true
    } else {
      previewContent.value = res.content
    }
  } catch {
    previewContent.value = '(读取失败)'
  }
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function fmt(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-CN', { hour12: false })
}

onMounted(fetchList)
</script>

<style scoped lang="scss">
.platform-skills {
  &__toolbar {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 10px;
  }

  &__hint {
    color: var(--el-text-color-secondary);
    font-size: 12.5px;
  }

  &__name {
    font-weight: 500;
    font-family: monospace;
  }

  &__file-item {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 6px 0;
    cursor: pointer;
    &:hover { background: var(--el-fill-color-light); }
  }

  &__file-name {
    min-width: 120px;
    font-weight: 500;
  }

  &__file-path {
    flex: 1;
    font-family: monospace;
    font-size: 12px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  &__file-size {
    min-width: 70px;
    text-align: right;
  }

  &__preview {
    font-family: monospace;
    font-size: 13px;
    white-space: pre-wrap;
    word-break: break-all;
    max-height: 500px;
    overflow-y: auto;
    background: var(--el-fill-color-lighter);
    padding: 12px;
    border-radius: 4px;
  }
}
</style>
