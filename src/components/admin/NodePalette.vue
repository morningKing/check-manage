<script setup lang="ts">
/**
 * 节点类型面板（spec §4.1）：agent/tool/approval/join 四种，点击或拖入画布。
 * dragstart 与 click 同发 add-node——编辑器在画布 drop 与面板点击两条路径
 * 上都创建节点（jsdom/无 DnD 环境下 click 兜底）。
 */
defineEmits<{ (e: 'add-node', kind: string): void }>()
const kinds = [
  { kind: 'agent', label: 'Agent', icon: '🤖', desc: 'AI 执行步骤' },
  { kind: 'tool', label: 'Tool', icon: '🔧', desc: '工具调用' },
  { kind: 'approval', label: 'Approval', icon: '✋', desc: '人工审批' },
  { kind: 'join', label: 'Join', icon: '🔗', desc: '汇聚节点' },
]
</script>

<template>
  <div class="node-palette">
    <h4>节点类型</h4>
    <div v-for="k in kinds" :key="k.kind"
         class="node-palette__item" draggable="true"
         :data-kind="k.kind"
         @dragstart="$emit('add-node', k.kind)"
         @click="$emit('add-node', k.kind)">
      <span class="node-palette__icon">{{ k.icon }}</span>
      <div>
        <div class="node-palette__label">{{ k.label }}</div>
        <div class="node-palette__desc">{{ k.desc }}</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.node-palette { width: 180px; padding: 12px; border-right: 1px solid #ebeef5; }
.node-palette__item {
  display: flex; align-items: center; gap: 8px; padding: 8px;
  border: 1px solid #dcdfe6; border-radius: 6px; margin-bottom: 8px;
  cursor: grab; transition: border-color 0.2s;
}
.node-palette__item:hover { border-color: #409eff; }
.node-palette__icon { font-size: 20px; }
.node-palette__label { font-weight: 500; font-size: 13px; }
.node-palette__desc { color: #909399; font-size: 11px; }
</style>
