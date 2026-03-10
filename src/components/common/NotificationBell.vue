<template>
  <el-popover placement="bottom-end" :width="360" trigger="click" @show="loadNotifications">
    <template #reference>
      <el-badge :value="unreadCount" :hidden="unreadCount === 0" :max="99">
        <el-button :icon="Bell" circle />
      </el-badge>
    </template>
    <div class="notification-panel">
      <div class="notification-header">
        <span>通知</span>
        <el-button v-if="unreadCount > 0" link size="small" @click="markAllRead">全部已读</el-button>
      </div>
      <div class="notification-list" v-loading="loading">
        <div
          v-for="n in notifications"
          :key="n.id"
          class="notification-item"
          :class="{ unread: !n.isRead }"
          @click="handleClick(n)"
        >
          <div class="notif-title">{{ n.title }}</div>
          <div v-if="n.content" class="notif-content">{{ n.content }}</div>
          <div class="notif-time">{{ formatTime(n.createdAt) }}</div>
        </div>
        <el-empty v-if="!loading && notifications.length === 0" :image-size="50" description="暂无通知" />
      </div>
    </div>
  </el-popover>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { Bell } from '@element-plus/icons-vue'
import { get, put } from '@/utils/request'

interface Notification {
  id: string
  userId: string
  type: string
  title: string
  content: string | null
  sourceCollection: string | null
  sourceRecordId: string | null
  isRead: boolean
  createdAt: string
}

const router = useRouter()
const notifications = ref<Notification[]>([])
const unreadCount = ref(0)
const loading = ref(false)
let pollTimer: ReturnType<typeof setInterval> | null = null

async function loadUnreadCount() {
  try {
    const data = await get('/notifications/unread-count')
    unreadCount.value = data.count || 0
  } catch {
    // silent
  }
}

async function loadNotifications() {
  loading.value = true
  try {
    const data = await get('/notifications', { limit: 30 })
    notifications.value = data
  } catch {
    // silent
  } finally {
    loading.value = false
  }
}

async function markAllRead() {
  try {
    await put('/notifications/read-all')
    unreadCount.value = 0
    notifications.value.forEach(n => n.isRead = true)
  } catch {
    // silent
  }
}

async function handleClick(n: Notification) {
  if (!n.isRead) {
    try {
      await put(`/notifications/${n.id}/read`)
      n.isRead = true
      unreadCount.value = Math.max(0, unreadCount.value - 1)
    } catch {
      // silent
    }
  }
  if (n.sourceCollection && n.sourceRecordId) {
    router.push({
      path: `/${n.sourceCollection}`,
      query: { recordId: n.sourceRecordId },
    })
  }
}

function formatTime(ts: string): string {
  if (!ts) return ''
  try {
    const d = new Date(ts)
    const now = new Date()
    const diff = now.getTime() - d.getTime()
    if (diff < 60000) return '刚刚'
    if (diff < 3600000) return `${Math.floor(diff / 60000)} 分钟前`
    if (diff < 86400000) return `${Math.floor(diff / 3600000)} 小时前`
    return `${Math.floor(diff / 86400000)} 天前`
  } catch {
    return ts
  }
}

onMounted(() => {
  loadUnreadCount()
  pollTimer = setInterval(loadUnreadCount, 30000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>

<style scoped>
.notification-panel {
  max-height: 400px;
  display: flex;
  flex-direction: column;
}
.notification-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-bottom: 8px;
  border-bottom: 1px solid #ebeef5;
  font-weight: 600;
  font-size: 14px;
}
.notification-list {
  overflow-y: auto;
  max-height: 350px;
  padding-top: 4px;
}
.notification-item {
  padding: 8px 4px;
  cursor: pointer;
  border-bottom: 1px solid #f5f5f5;
  transition: background 0.2s;
}
.notification-item:hover { background: #f5f7fa; }
.notification-item.unread { background: #ecf5ff; }
.notification-item.unread:hover { background: #d9ecff; }
.notif-title { font-size: 13px; color: #303133; font-weight: 500; }
.notif-content { font-size: 12px; color: #909399; margin-top: 2px; }
.notif-time { font-size: 11px; color: #c0c4cc; margin-top: 4px; }
</style>
