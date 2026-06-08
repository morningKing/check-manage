<template>
  <div class="data-migration-page">
    <el-tabs v-model="activeTab" class="migration-tabs">
      <el-tab-pane label="数据导出" name="export">
        <ExportTab v-if="activeTab === 'export'" />
      </el-tab-pane>
      <el-tab-pane label="批量导入" name="import">
        <ImportTab v-if="activeTab === 'import'" />
      </el-tab-pane>
      <el-tab-pane label="批量清空" name="clear">
        <ClearTab v-if="activeTab === 'clear'" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import ExportTab from './data-migration/ExportTab.vue'
import ImportTab from './data-migration/ImportTab.vue'
import ClearTab from './data-migration/ClearTab.vue'

const activeTab = ref('export')
</script>

<style scoped lang="scss">
.data-migration-page {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.migration-tabs {
  height: 100%;
  display: flex;
  flex-direction: column;
  :deep(.el-tabs__content) {
    flex: 1;
    min-height: 0;
    overflow: auto;
  }
}
</style>
