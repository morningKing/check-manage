<!-- src/components/PageConfigRelationGraph.vue -->
<template>
  <div class="relation-graph-container">
    <div v-if="loading" class="loading-state">
      <el-icon class="is-loading"><Loading /></el-icon>
      <span>正在加载关系图谱...</span>
    </div>

    <el-empty
      v-if="!loading && nodes.length === 1 && edges.length === 0"
      description="当前页面配置无关联关系"
    />

    <VueFlow
      v-if="!loading && nodes.length > 0"
      :nodes="nodes"
      :edges="edges"
      :default-viewport="{ zoom: 1.0 }"
      :min-zoom="0.2"
      :max-zoom="4"
      fit-view-on-init
      @node-click="handleNodeClick"
    >
      <Background />
      <Controls />

      <template #node-custom="{ data }">
        <div class="custom-node">
          <div class="node-header">{{ data.name }}</div>
          <div class="node-body">
            <div class="node-meta">{{ data.fields }} 个字段</div>
            <div class="node-id">{{ data.id }}</div>
          </div>
        </div>
      </template>
    </VueFlow>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { VueFlow } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { Loading } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { getPageConfigRelations } from '@/api/page'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'

const props = defineProps<{
  pageId: string
}>()

const emit = defineEmits<{
  navigate: [targetPageId: string]
}>()

const loading = ref(false)
const nodes = ref<any[]>([])
const edges = ref<any[]>([])

defineExpose({ nodes, edges, loading })

function getEdgeStyle(type: string) {
  switch (type) {
    case 'relation':
      return { stroke: '#409EFF', strokeWidth: 2 }
    case 'reference':
      return { stroke: '#67C23A', strokeWidth: 2 }
    case 'quoteSelect':
      return { stroke: '#E6A23C', strokeWidth: 2 }
    default:
      return { stroke: '#909399', strokeWidth: 2 }
  }
}

async function loadRelations() {
  loading.value = true

  try {
    const result = await getPageConfigRelations(props.pageId, 3)

    nodes.value = result.nodes.map(n => ({
      id: n.id,
      type: 'custom',
      position: { x: 0, y: 0 },
      data: {
        name: n.name,
        fields: n.fields,
        id: n.id
      }
    }))

    edges.value = result.edges.map(e => {
      // 将 collection 名称转换为 page_id（后端可能返回collection名称）
      const sourceId = e.source.startsWith('page-') ? e.source : `page-${e.source}`
      const targetId = e.target.startsWith('page-') ? e.target : `page-${e.target}`

      return {
        id: `${sourceId}-${targetId}`,
        source: sourceId,
        target: targetId,
        label: e.label,
        type: 'smoothstep',
        style: getEdgeStyle(e.type),
        animated: e.type === 'relation'
      }
    })

  } catch (error: any) {
    const msg = error?.response?.data?.error || '加载关系图谱失败'
    ElMessage.error(msg)
    nodes.value = []
    edges.value = []
  } finally {
    loading.value = false
  }
}

function handleNodeClick(event: { node: any }) {
  const targetPageId = event.node.id

  if (targetPageId === props.pageId) {
    return
  }

  emit('navigate', targetPageId)
}

onMounted(() => {
  loadRelations()
})

watch(() => props.pageId, () => {
  loadRelations()
})
</script>

<style scoped>
.relation-graph-container {
  width: 100%;
  height: 600px;
  border: 1px solid #E4E7ED;
  border-radius: 4px;
  position: relative;
}

.loading-state {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  gap: 8px;
  color: #606266;
}

.custom-node {
  background: #fff;
  border: 2px solid #409EFF;
  border-radius: 8px;
  padding: 12px;
  min-width: 120px;
  box-shadow: 0 2px 4px rgba(0,0,0,0.1);
}

.node-header {
  font-weight: bold;
  font-size: 14px;
  color: #303133;
  margin-bottom: 8px;
}

.node-body {
  font-size: 12px;
  color: #606266;
}

.node-meta {
  margin-bottom: 4px;
}

.node-id {
  color: #909399;
  font-size: 11px;
}
</style>