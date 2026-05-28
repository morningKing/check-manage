<script setup lang="ts">
import { ref, computed } from 'vue'
import { ElIcon } from 'element-plus'
import { CircleCheck, CircleClose, Clock, Download, Document, ArrowRight } from '@element-plus/icons-vue'
import type { RunResultEntry } from '@/stores/aiChat'

const props = defineProps<{ result: RunResultEntry; downloadUrl: (path: string) => string }>()

const ok = computed(() => props.result.exitCode === 0 && !props.result.timedOut)
const open = ref(!ok.value)  // expand by default on error
const fileName = (p: string) => p.split('/').pop() || p
</script>

<template>
  <div class="run-result" :class="ok ? 'run-result--ok' : 'run-result--err'">
    <div class="run-result__head" @click="open = !open">
      <ElIcon class="run-result__chev" :class="{ open }"><ArrowRight /></ElIcon>
      <ElIcon class="run-result__status">
        <Clock v-if="result.timedOut" />
        <CircleCheck v-else-if="ok" />
        <CircleClose v-else />
      </ElIcon>
      <span class="run-result__title">
        运行 {{ result.filename }} ·
        {{ result.timedOut ? '超时' : (ok ? '成功' : '出错 (exit ' + result.exitCode + ')') }}
      </span>
      <span v-if="result.outputFiles.length" class="run-result__count">
        生成 {{ result.outputFiles.length }} 个文件
      </span>
    </div>

    <div v-show="open" class="run-result__body">
      <template v-if="result.outputFiles.length">
        <div class="run-result__sub">产出文件</div>
        <a
          v-for="p in result.outputFiles" :key="p"
          class="run-result__file" :href="downloadUrl(p)" target="_blank" rel="noopener"
        >
          <ElIcon><Document /></ElIcon><span>{{ fileName(p) }}</span><ElIcon class="dl"><Download /></ElIcon>
        </a>
      </template>
      <template v-if="result.stdout">
        <div class="run-result__sub">输出</div>
        <pre class="run-result__pre">{{ result.stdout }}</pre>
      </template>
      <template v-if="result.stderr">
        <div class="run-result__sub run-result__sub--err">错误</div>
        <pre class="run-result__pre run-result__pre--err">{{ result.stderr }}</pre>
      </template>
      <div v-if="!result.outputFiles.length && !result.stdout && !result.stderr" class="run-result__empty">
        （无输出）
      </div>
    </div>
  </div>
</template>

<style scoped lang="scss">
.run-result {
  border: 1px solid var(--el-border-color-light);
  border-left-width: 3px;
  border-radius: 8px;
  margin: 8px 0 16px;
  background: var(--el-fill-color-lighter);
  font-size: 13px;
  overflow: hidden;
  &--ok { border-left-color: var(--el-color-success); }
  &--err { border-left-color: var(--el-color-danger); }
}
.run-result__head {
  display: flex; align-items: center; gap: 6px;
  padding: 8px 12px; cursor: pointer; user-select: none;
  &:hover { background: var(--el-fill-color-light); }
}
.run-result__chev { transition: transform .15s; color: var(--el-text-color-secondary); &.open { transform: rotate(90deg); } }
.run-result--ok .run-result__status { color: var(--el-color-success); }
.run-result--err .run-result__status { color: var(--el-color-danger); }
.run-result__title { font-weight: 500; color: var(--el-text-color-primary); }
.run-result__count { margin-left: auto; color: var(--el-text-color-secondary); font-size: 12px; }
.run-result__body { padding: 4px 12px 12px; border-top: 1px solid var(--el-border-color-lighter); }
.run-result__sub { font-size: 12px; color: var(--el-text-color-secondary); margin: 8px 0 4px; &--err { color: var(--el-color-danger); } }
.run-result__file {
  display: flex; align-items: center; gap: 6px; padding: 5px 8px; border-radius: 6px;
  text-decoration: none; color: var(--el-text-color-primary);
  &:hover { background: var(--el-fill-color); }
  .dl { color: var(--el-color-primary); margin-left: auto; }
}
.run-result__pre {
  margin: 0; padding: 8px 10px; background: var(--el-fill-color-dark);
  border-radius: 6px; font-family: var(--el-font-family-mono, monospace); font-size: 12px;
  white-space: pre-wrap; word-break: break-word; max-height: 220px; overflow: auto;
  &--err { color: var(--el-color-danger); }
}
.run-result__empty { color: var(--el-text-color-secondary); padding: 6px 0; }
</style>
