<template>
  <div class="view-selector">
    <el-select
      :model-value="currentViewId"
      placeholder="选择视图"
      clearable
      style="width: 200px"
      @update:model-value="handleSelect"
    >
      <template #prefix>
        <el-icon><View /></el-icon>
      </template>

      <el-option-group label="公共视图" v-if="publicViews.length">
        <el-option
          v-for="view in publicViews"
          :key="view.id"
          :value="view.id"
          :label="view.name + (view.isDefault ? '（默认）' : '')"
        />
      </el-option-group>

      <el-option-group label="我的视图" v-if="myViews.length">
        <el-option
          v-for="view in myViews"
          :key="view.id"
          :value="view.id"
          :label="view.name"
        />
      </el-option-group>
    </el-select>

    <el-button v-if="!isGuest" @click="emit('manage')" title="管理视图">
      <el-icon><Setting /></el-icon>
    </el-button>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useColumnViewStore } from '@/stores/columnView'
import { useAuthStore } from '@/stores/auth'
import { View, Setting } from '@element-plus/icons-vue'

const columnViewStore = useColumnViewStore()
const authStore = useAuthStore()

const isGuest = computed(() => authStore.isGuest)
const currentViewId = computed(() => columnViewStore.currentViewId)
const publicViews = computed(() => columnViewStore.publicViews)
const myViews = computed(() => columnViewStore.myViews)

const emit = defineEmits<{
  select: [viewId: number | null]
  manage: []
}>()

function handleSelect(viewId: number | null) {
  emit('select', viewId)
}
</script>

<style scoped>
.view-selector {
  display: flex;
  align-items: center;
  gap: 8px;
}
</style>
