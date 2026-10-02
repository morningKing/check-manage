<template>
  <div class="fit-patterns">
    <p v-if="!patterns.length" class="muted">
      {{ isGlobal ? '暂无偏离模式数据。' : '该定义无步骤级偏离记录。' }}
    </p>
    <ElTable v-else :data="patterns" size="small">
      <ElTableColumn v-if="isGlobal" prop="defName" label="定义" min-width="150" />
      <ElTableColumn prop="stepId" label="偏差步骤" width="140" />
      <ElTableColumn prop="missTasks" label="偏离任务数" width="110" sortable />
      <ElTableColumn prop="versionsAffected" label="涉及版本" width="90" />
      <ElTableColumn label="版本 hash" min-width="160">
        <template #default="{ row }">
          <span class="mono">{{ (row.versionHashes || []).join(' ') }}</span>
        </template>
      </ElTableColumn>
      <ElTableColumn prop="avgScore" label="均分" width="70" />
    </ElTable>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { ElTable, ElTableColumn } from 'element-plus'
import { listSkillDefPatterns } from '@/api/aiSkills'
import type { SkillDefPattern } from '@/api/aiSkills'

const props = defineProps<{ defKind?: string; defName?: string }>()
/** 两个 props 都缺省 = 全局视图（父组件的全局偏离对话框直接复用本组件） */
const isGlobal = computed(() => !props.defName)

const patterns = ref<SkillDefPattern[]>([])
async function load() {
  const res = await listSkillDefPatterns({
    defKind: props.defKind, defName: props.defName, limit: 50,
  })
  patterns.value = res.patterns || []
}
watch([() => props.defKind, () => props.defName], () => void load(), { immediate: true })
</script>

<style scoped>
.mono { font-family: monospace; font-size: 12px; }
.muted { color: var(--el-text-color-secondary); font-size: 12px; }
</style>
