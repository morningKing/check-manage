<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElButton, ElIcon, ElMessage } from 'element-plus'
import { Lock, Check, Close } from '@element-plus/icons-vue'
import type { PermissionRequest } from '@/api/aiChat'

const props = defineProps<{
  request: PermissionRequest
  /** 队列里还有几条待授权（同回合并发的外部目录询问） */
  extra?: number
  /** 由父级注入：实际发出应答（失败会 reject，卡片据此复位按钮） */
  submit: (response: 'once' | 'always' | 'reject') => Promise<void>
}>()

const busy = ref(false)

async function respond(response: 'once' | 'always' | 'reject') {
  if (busy.value) return
  busy.value = true
  try {
    await props.submit(response)
    // 成功提示放在应答真正落定之后——旧实现先弹「已放行」再发请求，
    // 请求失败时界面在说谎，且按钮因 busy 永不复位而全变死。
    ElMessage.success(response === 'reject' ? '已拒绝'
      : response === 'always' ? '已放行并记住该目录' : '已放行本次')
  } catch {
    /* 失败：拦截器已弹错误；保留卡片与按钮，用户可重试或换个选择 */
  } finally {
    busy.value = false
  }
}

const target = computed(() => props.request.metadata?.filepath || props.request.patterns?.[0] || '(未知路径)')
const isDir = computed(() => !props.request.metadata?.filepath)
// 后端重水化时会标注来源（键存在即为子代理发起）；实时事件不带该键
const fromSubagent = computed(() => props.request.subtaskAgent !== undefined)
const subagentLabel = computed(() => props.request.subtaskAgent
  ? `来自子代理：${props.request.subtaskAgent}` : '来自子代理')
</script>

<template>
  <div class="perm-card" data-test="permission-card" role="alert">
    <div class="perm-card__head">
      <ElIcon class="perm-card__icon"><Lock /></ElIcon>
      <span class="perm-card__title">权限请求 · {{ request.permission }}</span>
      <span v-if="fromSubagent" class="perm-card__from">{{ subagentLabel }}</span>
      <span v-if="extra" class="perm-card__extra">另有 {{ extra }} 条待授权</span>
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
      <ElButton size="small" type="primary" :icon="Check" :loading="busy" data-test="perm-once"
                @click="respond('once')">
        本次允许
      </ElButton>
      <ElButton size="small" :disabled="busy" data-test="perm-always" @click="respond('always')">总是允许</ElButton>
      <ElButton size="small" type="danger" plain :icon="Close" :disabled="busy" data-test="perm-reject"
                @click="respond('reject')">
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
.perm-card__from {
  font-size: 12px;
  font-weight: 400;
  padding: 1px 6px;
  border-radius: 4px;
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}
.perm-card__extra {
  margin-left: auto;
  font-size: 12px;
  font-weight: 400;
  color: var(--el-text-color-secondary);
}
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
