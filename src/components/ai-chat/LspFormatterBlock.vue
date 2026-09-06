<script setup lang="ts">
import type { LspServerStatus, FormatterStatus } from '@/api/aiChat'

defineProps<{ lsp: LspServerStatus[]; formatters: FormatterStatus[]; error?: string }>()

// LSP 条目字段随 OpenCode 版本可能有差异，宽松取 name/status（缺失时回退 JSON）
function lspName(s: LspServerStatus): string {
  return (s.name ?? s.root ?? JSON.stringify(s)).toString()
}
</script>

<template>
  <div class="lspf">
    <div v-if="error" class="lspf__empty">{{ error }}</div>
    <template v-else>
      <div class="lspf__section">
        <div class="lspf__title">LSP 服务 <span class="lspf__count">({{ lsp.length }})</span></div>
        <div v-if="!lsp.length" class="lspf__empty">
          当前会话尚未激活 LSP 服务——OpenCode 会在助手触及对应类型的文件时自动启动
          （编辑结果会自动附带 LSP 诊断，无需配置）。
        </div>
        <div v-for="(s, i) in lsp" :key="i" class="lspf__row">
          <span class="lspf__dot" :class="{ on: (s.status ?? 'connected') === 'connected' }" />
          <span class="lspf__name">{{ lspName(s) }}</span>
          <span class="lspf__meta">{{ s.status ?? '' }}</span>
        </div>
      </div>

      <div class="lspf__section">
        <div class="lspf__title">
          格式化器
          <span class="lspf__count">({{ formatters.filter(f => f.enabled).length }}/{{ formatters.length }} 启用)</span>
        </div>
        <div v-if="!formatters.length" class="lspf__empty">无法获取格式化器目录</div>
        <template v-else>
          <div class="lspf__hint">
            保存/编辑对应类型文件时自动格式化。默认全部关闭——可在 OpenCode 全局配置的
            <code>formatter</code> 键里启用（如 <code>ruff</code>、<code>prettier</code>）。
          </div>
          <div class="lspf__fmt-grid">
            <span
              v-for="f in formatters" :key="f.name"
              class="lspf__fmt" :class="{ on: f.enabled }"
              :title="f.extensions.join(' ')"
            >
              {{ f.name }}<span class="lspf__ext">{{ f.extensions[0] }}</span>
            </span>
          </div>
        </template>
      </div>
    </template>
  </div>
</template>

<style scoped lang="scss">
.lspf {
  border: 1px solid var(--el-border-color);
  border-radius: 8px;
  padding: 10px 12px;
  background: var(--el-fill-color-light);
  font-size: 13px;
}
.lspf__section + .lspf__section { margin-top: 10px; }
.lspf__title { font-weight: 600; margin-bottom: 6px; }
.lspf__count { color: var(--el-text-color-secondary); font-weight: 400; font-size: 12px; }
.lspf__empty { color: var(--el-text-color-secondary); line-height: 1.6; }
.lspf__hint { color: var(--el-text-color-secondary); font-size: 12px; line-height: 1.6; margin-bottom: 8px; }
.lspf__row { display: flex; align-items: center; gap: 6px; line-height: 1.9; }
.lspf__dot {
  width: 8px; height: 8px; border-radius: 50%;
  background: var(--el-color-info); flex-shrink: 0;
}
.lspf__dot.on { background: var(--el-color-success); }
.lspf__name { font-weight: 600; }
.lspf__meta { color: var(--el-text-color-secondary); font-size: 12px; }
.lspf__fmt-grid { display: flex; flex-wrap: wrap; gap: 6px; }
.lspf__fmt {
  display: inline-flex; align-items: center; gap: 4px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px; padding: 2px 8px;
  color: var(--el-text-color-secondary);
  font-family: var(--el-font-family-mono, monospace); font-size: 12px;
  &.on {
    color: var(--el-color-success);
    border-color: var(--el-color-success);
  }
}
.lspf__ext { opacity: 0.7; }
</style>
