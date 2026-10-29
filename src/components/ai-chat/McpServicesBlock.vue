<script setup lang="ts">
import { computed } from 'vue'
import type { McpServer, McpTool } from '@/api/aiChat'

const props = defineProps<{ servers: McpServer[] }>()

/** 工具带 group_name 时按组分区（首现顺序）；缺组归「其他」。 */
function groupTools(tools: McpTool[]): { title: string; tools: McpTool[] }[] {
  const map = new Map<string, { title: string; tools: McpTool[] }>()
  for (const t of tools) {
    const title = t.group_name || '其他'
    if (!map.has(title)) map.set(title, { title, tools: [] })
    map.get(title)!.tools.push(t)
  }
  return [...map.values()]
}

const anyGrouped = computed(() =>
  props.servers.some(s => s.tools.some(t => t.group_name)))
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
        <template v-if="anyGrouped">
          <div v-for="g in groupTools(s.tools)" :key="g.title" class="mcp-tools__group">
            <div class="mcp-tools__group-title">{{ g.title }}（{{ g.tools.length }}）</div>
            <ul class="mcp-tools">
              <li v-for="t in g.tools" :key="t.name">
                <code>{{ t.name }}</code><span v-if="t.description"> — {{ t.description }}</span>
              </li>
            </ul>
          </div>
          <!-- 外部自定义服务：后端只回传连接状态，不回传工具清单——必须留兜底行，
               否则分组模式下外部服务只剩名字，看起来像「被搞没了」 -->
          <div v-if="!s.tools.length" class="mcp-tools__empty">
            （外部服务：此处仅显示连接状态；其工具随会话注入，清单不在此展示）
          </div>
        </template>
        <ul v-else class="mcp-tools">
          <li v-for="t in s.tools" :key="t.name">
            <code>{{ t.name }}</code><span v-if="t.description"> — {{ t.description }}</span>
          </li>
          <li v-if="!s.tools.length" class="mcp-tools__empty">（无可用工具信息）</li>
        </ul>
      </div>
    </template>
    <div v-else class="mcp-services__empty">未获取到 MCP 服务（OpenCode 不可用，或尚未配置任何服务）</div>
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
.mcp-tools__group { margin-top: 4px; }
.mcp-tools__group-title {
  font-weight: 600; font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 6px;
}
</style>
