<script setup lang="ts">
import type { McpServer } from '@/api/aiChat'

defineProps<{ servers: McpServer[] }>()
</script>

<template>
  <div class="mcp-services">
    <template v-if="servers.length">
      <div class="mcp-services__title">MCP 服务 ({{ servers.length }})</div>
      <div v-for="s in servers" :key="s.name" class="mcp-server">
        <div class="mcp-server__head">
          <span class="mcp-dot" :class="{ on: s.status === 'connected' }" />
          <span class="mcp-server__name">{{ s.name }}</span>
          <span class="mcp-server__status">{{ s.status }}</span>
        </div>
        <ul class="mcp-tools">
          <li v-for="t in s.tools" :key="t.name">
            <code>{{ t.name }}</code><span v-if="t.description"> — {{ t.description }}</span>
          </li>
          <li v-if="!s.tools.length" class="mcp-tools__empty">（无可用工具信息）</li>
        </ul>
      </div>
    </template>
    <div v-else class="mcp-services__empty">无法获取 MCP 服务（OpenCode 不可用）</div>
  </div>
</template>

<style scoped lang="scss">
.mcp-services {
  border: 1px solid var(--el-border-color);
  border-radius: 8px;
  padding: 10px 12px;
  background: var(--el-fill-color-light);
  font-size: 13px;
}
.mcp-services__title { font-weight: 600; margin-bottom: 8px; }
.mcp-server { margin-bottom: 8px; }
.mcp-server__head { display: flex; align-items: center; gap: 6px; }
.mcp-dot {
  width: 8px; height: 8px; border-radius: 50%;
  background: var(--el-color-info);
}
.mcp-dot.on { background: var(--el-color-success); }
.mcp-server__name { font-weight: 600; }
.mcp-server__status { color: var(--el-text-color-secondary); font-size: 12px; }
.mcp-tools { margin: 4px 0 0; padding-left: 18px; }
.mcp-tools li { line-height: 1.7; }
.mcp-tools code { font-family: var(--el-font-family-mono, monospace); }
.mcp-tools__empty { color: var(--el-text-color-secondary); list-style: none; }
</style>
