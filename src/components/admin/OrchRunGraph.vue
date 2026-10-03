<script setup lang="ts">
import { computed, markRaw } from 'vue'
import { VueFlow } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { MiniMap } from '@vue-flow/minimap'
import { Controls } from '@vue-flow/controls'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/minimap/dist/style.css'
import '@vue-flow/controls/dist/style.css'
import { toFlowNodes, toFlowEdges, dagreLayout } from '@/utils/orchGraph'
import OrchStepNode from './OrchStepNode.vue'

const props = defineProps<{
  definition: { nodes: any[]; edges: any[] }
  steps: any[]
}>()
const emit = defineEmits<{ (e: 'select-step', nodeId: string): void }>()

// 自定义节点类型：markRaw 避免 Vue Flow 内部把组件包成响应式（性能告警）。
// OrchStepNode 只声明 data prop，而 NodeComponent 类型要求完整 NodeProps
// 实例签名，运行时 Vue Flow 只传 data/handles——此处断言收窄绕开误报。
const nodeTypes = { 'orch-step': markRaw(OrchStepNode) as any }

const flowNodes = computed(() => {
  const nodes = toFlowNodes(props.definition.nodes, props.steps)
  const positions = dagreLayout(props.definition.nodes, props.definition.edges)
  return nodes.map(n => ({ ...n, position: positions[n.id] ?? { x: 0, y: 0 } }))
})
const flowEdges = computed(() => toFlowEdges(props.definition.edges, props.steps))

function onNodeClick(e: any) {
  const nodeId = e.node?.id
  if (nodeId) emit('select-step', nodeId)
}
</script>

<template>
  <div class="orch-run-graph" style="height: 400px; border: 1px solid #ebeef5; border-radius: 8px;">
    <VueFlow
      :nodes="flowNodes"
      :edges="flowEdges"
      :node-types="nodeTypes"
      :fit-view-on-init="true"
      :nodes-connectable="false"
      :elements-selectable="false"
      @node-click="onNodeClick"
    >
      <Background pattern-color="#e0e0e0" :gap="20" />
      <MiniMap pannable zoomable />
      <Controls />
    </VueFlow>
  </div>
</template>
