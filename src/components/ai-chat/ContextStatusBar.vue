<!-- 输入框上方的常驻用量状态条（AI-Chat 优化 F1）：模型 ｜ 上下文水位线 ｜ 累计
     token/费用。水位 <70% 默认色、70%~90% 警告色、≥90% 危险色并弹出压缩建议
     气泡（「立即压缩」交给父组件已有的 onCompact 流程）。数据来自会话消息
     meta（见 utils/aiUsage.ts），父组件用 :key="activeId" 挂载以在切换会话时
     重置「稍后」的静音状态。 -->
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElButton, ElIcon } from 'element-plus'
import { Warning } from '@element-plus/icons-vue'
import { formatTokens, formatCost } from '@/utils/aiMeta'
import { usageLevel, contextPercent, type SessionUsage } from '@/utils/aiUsage'

const props = defineProps<{
  usage: SessionUsage
  modelLabel: string
  contextLimit?: number | null
}>()
const emit = defineEmits<{ (e: 'compact'): void }>()

const pct = computed(() => contextPercent(props.usage, props.contextLimit))
const level = computed(() => usageLevel(pct.value))
const pctText = computed(() => (pct.value == null ? null : `${pct.value.toFixed(1)}%`))
const pctIntText = computed(() => (pct.value == null ? '' : pct.value.toFixed(0)))
const hasContext = computed(() => props.usage.contextTokens != null)
const hasTotal = computed(() => props.usage.totalTokens > 0 || props.usage.cost > 0)

// 「稍后」只压住本轮危险提示；水位回落到 90% 以下后重新武装，再次越线仍会
// 提醒（一次 dismiss 不做永久静音——静音的判定依据是水位本身）。
const dismissed = ref(false)
watch(pct, (v) => { if (v != null && v < 90) dismissed.value = false })
</script>

<template>
  <div class="ctx-bar">
    <div v-if="level === 'danger' && !dismissed" class="ctx-bar__alert">
      <ElIcon><Warning /></ElIcon>
      <span class="ctx-bar__alert-text">上下文已达 {{ pctIntText }}%，建议压缩以释放空间</span>
      <ElButton size="small" type="primary" @click="emit('compact')">立即压缩</ElButton>
      <ElButton size="small" @click="dismissed = true">稍后</ElButton>
    </div>
    <div class="ctx-bar__line" :class="`ctx-bar__line--${level}`">
      <span class="ctx-bar__seg">模型: {{ modelLabel }}</span>
      <span v-if="hasContext" class="ctx-bar__seg ctx-bar__ctx">
        上下文: {{ formatTokens(usage.contextTokens ?? 0)
        }}<template v-if="contextLimit"> / {{ formatTokens(contextLimit) }}（{{ pctText }}）</template>
      </span>
      <span v-if="hasTotal" class="ctx-bar__seg">
        累计: {{ formatTokens(usage.totalTokens) }}<template v-if="usage.cost"> · {{ formatCost(usage.cost) }}</template>
      </span>
    </div>
  </div>
</template>

<style scoped lang="scss">
.ctx-bar {
  max-width: 780px;
  margin: 0 auto;
}
.ctx-bar__line {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 18px;
  padding: 2px 14px 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  font-family: var(--el-font-family-mono, monospace);
  &--warn .ctx-bar__ctx { color: var(--el-color-warning); }
  &--danger .ctx-bar__ctx { color: var(--el-color-danger); font-weight: 600; }
}
.ctx-bar__alert {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin: 0 0 6px;
  padding: 6px 12px;
  border: 1px solid var(--el-color-danger-light-7);
  border-radius: 8px;
  background: var(--el-color-danger-light-9);
  color: var(--el-color-danger);
  font-size: 13px;
  .ctx-bar__alert-text { flex: 1; min-width: 200px; }
  .el-button { margin-left: 0; }
}
</style>
