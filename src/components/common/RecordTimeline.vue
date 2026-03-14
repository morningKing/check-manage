<template>
  <div class="record-timeline">
    <el-timeline v-if="items.length > 0">
      <el-timeline-item
        v-for="item in items"
        :key="item.id"
        :timestamp="formatTime(item.timestamp)"
        :type="item.type === 'comment' ? 'primary' : item.type === 'statusChange' ? 'warning' : 'info'"
        placement="top"
      >
        <div v-if="item.type === 'comment'" class="timeline-comment">
          <div class="comment-header">
            <span class="comment-author">{{ item.author }}</span>
            <span v-if="canEditComment(item)" class="comment-actions">
              <el-button link size="small" @click="startEditComment(item)">编辑</el-button>
              <el-button link size="small" type="danger" @click="handleDeleteComment(item.id)">删除</el-button>
            </span>
          </div>
          <div v-if="editingCommentId === item.id" class="comment-edit">
            <el-input v-model="editContent" type="textarea" :rows="2" />
            <div class="comment-edit-actions">
              <el-button size="small" @click="editingCommentId = ''">取消</el-button>
              <el-button size="small" type="primary" @click="handleUpdateComment">保存</el-button>
            </div>
          </div>
          <div v-else class="comment-content">{{ item.content }}</div>
        </div>
        <div v-else class="timeline-change">
          <span class="change-author">{{ item.author }}</span>
          <span class="change-action">{{ item.content }}</span>
          <el-tag v-if="item.branchName" :type="item.branchName === '主分支' ? 'info' : 'warning'" size="small" class="branch-tag">
            {{ item.branchName }}
          </el-tag>
          <div v-if="item.fieldChanges?.length" class="field-changes">
            <div v-for="(fc, idx) in item.fieldChanges" :key="idx" class="field-change-item">
              <span class="fc-label">{{ fc.label }}:</span>
              <span class="fc-from">{{ fc.from ?? '-' }}</span>
              <span class="fc-arrow">&rarr;</span>
              <span class="fc-to">{{ fc.to ?? '-' }}</span>
            </div>
          </div>
        </div>
      </el-timeline-item>
    </el-timeline>
    <el-empty v-else :image-size="60" description="暂无记录" />

    <!-- 添加评论 -->
    <div class="add-comment">
      <el-input
        v-model="newComment"
        type="textarea"
        :rows="2"
        placeholder="添加评论..."
      />
      <el-button
        type="primary"
        size="small"
        :disabled="!newComment.trim()"
        :loading="submitting"
        @click="handleAddComment"
        style="margin-top: 8px"
      >
        发送
      </el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { get, post, put, del } from '@/utils/request'
import { useAuthStore } from '@/stores'

const props = defineProps<{
  collection: string
  recordId: string
}>()

const authStore = useAuthStore()

interface TimelineItem {
  type: string
  id: string
  content?: string
  author: string
  timestamp: string
  fieldChanges?: Array<{ field: string; label: string; from: any; to: any }>
  authorId?: string
  branchName?: string
}

const items = ref<TimelineItem[]>([])
const loading = ref(false)
const newComment = ref('')
const submitting = ref(false)
const editingCommentId = ref('')
const editContent = ref('')

async function loadTimeline() {
  loading.value = true
  try {
    const data = await get(`/timeline/${props.collection}/${props.recordId}`)
    items.value = data
  } catch {
    // silent
  } finally {
    loading.value = false
  }
}

async function handleAddComment() {
  if (!newComment.value.trim()) return
  submitting.value = true
  try {
    await post(`/comments/${props.collection}/${props.recordId}`, {
      content: newComment.value.trim(),
    })
    newComment.value = ''
    await loadTimeline()
  } catch {
    ElMessage.error('评论失败')
  } finally {
    submitting.value = false
  }
}

function canEditComment(item: TimelineItem): boolean {
  if (item.type !== 'comment') return false
  return authStore.isAdmin || item.authorId === authStore.user?.id
}

function startEditComment(item: TimelineItem) {
  editingCommentId.value = item.id
  editContent.value = item.content || ''
}

async function handleUpdateComment() {
  if (!editContent.value.trim()) return
  try {
    await put(`/comments/${editingCommentId.value}`, {
      content: editContent.value.trim(),
    })
    editingCommentId.value = ''
    await loadTimeline()
  } catch {
    ElMessage.error('更新失败')
  }
}

async function handleDeleteComment(commentId: string) {
  try {
    await del(`/comments/${commentId}`)
    await loadTimeline()
  } catch {
    ElMessage.error('删除失败')
  }
}

function formatTime(ts: string): string {
  if (!ts) return ''
  try {
    const d = new Date(ts)
    const y = d.getFullYear()
    const m = String(d.getMonth() + 1).padStart(2, '0')
    const day = String(d.getDate()).padStart(2, '0')
    const h = String(d.getHours()).padStart(2, '0')
    const min = String(d.getMinutes()).padStart(2, '0')
    return `${y}-${m}-${day} ${h}:${min}`
  } catch {
    return ts
  }
}

onMounted(loadTimeline)
</script>

<style scoped>
.record-timeline {
  max-height: 400px;
  overflow-y: auto;
  padding: 8px 0;
}
.timeline-comment .comment-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}
.comment-author { font-weight: 500; color: #303133; font-size: 13px; }
.comment-content { color: #606266; font-size: 13px; white-space: pre-wrap; }
.comment-edit { margin-top: 4px; }
.comment-edit-actions { margin-top: 4px; display: flex; gap: 4px; justify-content: flex-end; }
.timeline-change { font-size: 13px; color: #909399; }
.change-author { font-weight: 500; color: #606266; }
.branch-tag { margin-left: 8px; vertical-align: middle; }
.field-changes { margin-top: 4px; }
.field-change-item {
  font-size: 12px;
  display: flex;
  gap: 4px;
  align-items: center;
  padding: 2px 0;
}
.fc-label { color: #606266; font-weight: 500; }
.fc-from { color: #F56C6C; text-decoration: line-through; }
.fc-arrow { color: #c0c4cc; }
.fc-to { color: #67C23A; font-weight: 500; }
.add-comment { margin-top: 16px; border-top: 1px solid #ebeef5; padding-top: 12px; }
</style>
