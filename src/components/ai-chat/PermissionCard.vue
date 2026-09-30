<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElButton, ElIcon, ElMessage } from 'element-plus'
import { Lock, Check, Close } from '@element-plus/icons-vue'
import type { PermissionRequest } from '@/api/aiChat'

const props = defineProps<{ request: PermissionRequest }>()
const emit = defineEmits<{ reply: [response: 'once' | 'always' | 'reject'] }>()

const busy = ref(false)

function respond(response: 'once' | 'always' | 'reject') {
  if (busy.value) return
  busy.value = true
  emit('reply', response)
  ElMessage.success(response === 'reject' ? '已拒绝' : response === 'always' ? '已放行并记住该目录' : '已放行本次')
}

const target = computed(() => props.request.metadata?.filepath || props.request.patterns?.[0] || '(未知路径)')
const isDir = computed(() => !props.request.metadata?.filepath)
</script>

<template>
  <div class="perm-card" data-test="permission-card" role="alert">
    <div class="perm-card__head">
      <ElIcon class="perm-card__icon"><Lock /></ElIcon>
      <span class="perm-card__title">权限请求 · {{ request.permission }}</span>
    </div>
    <div class="perm-card__body">
      <p class="perm-card__target" :title="target">
        {{ isDir ? '目录' : '文件' }}：<code>{{ target }}</code>
      </p>
      <p class="perm-card__hint">
        该路径在会话工作区之外，需要你授权后 AI 才能继续读取。选择「总是允许」会把
        <code>{{ (request.patterns || [])[0] || target }}</code> 记入放行名单（后续不再询问）。
      </p>
    </div>
    <div class="perm-card__actions">
      <ElButton size="small" type="primary" :icon="Check" data-test="perm-once" @click="respond('once')">
        本次允许
      </ElButton>
      <ElButton size="small" data-test="perm-always" @click="respond('always')">总是允许</ElButton>
      <ElButton size="small" type="danger" plain :icon="Close" data-test="perm-reject" @click="respond('reject')">
        拒绝
      </ElButton>
    </div>
  </div>
</template>

<style scoped>
.perm-card {
  border: 1px solid var(--el-color-warning-light-5);
  background: var(--el-color-warning-light-9);
  border-radius: 8px;
  padding: 10px 12px;
  margin: 8px 0;
}
.perm-card__head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  margin-bottom: 6px;
}
.perm-card__icon { color: var(--el-color-warning); }
.perm-card__target { margin: 0 0 4px; word-break: break-all; }
.perm-card__target code {
  background: var(--el-fill-color-light);
  padding: 1px 4px;
  border-radius: 4px;
  font-size: 12px;
}
.perm-card__hint {
  margin: 0 0 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.perm-card__hint code {
  background: var(--el-fill-color-light);
  padding: 0 4px;
  border-radius: 4px;
}
.perm-card__actions { display: flex; gap: 8px; }
</style>
