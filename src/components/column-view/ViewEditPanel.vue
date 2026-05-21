<template>
  <div class="view-edit-panel" v-if="view">
    <el-form label-position="top" :model="formData">
      <el-form-item label="视图名称">
        <el-input v-model="formData.name" placeholder="请输入视图名称" />
      </el-form-item>

      <el-form-item label="视图类型">
        <el-tag :type="view.isPublic ? 'success' : 'info'">
          {{ view.isPublic ? '公共视图' : '私人视图' }}
        </el-tag>
      </el-form-item>

      <el-form-item>
        <el-checkbox
          v-if="view.isPublic && isAdmin"
          v-model="formData.isDefault"
          @change="handleSetDefault"
        >
          设为默认视图
        </el-checkbox>
      </el-form-item>

      <el-form-item label="列配置">
        <el-button type="primary" @click="emit('edit-columns')">
          编辑列配置
        </el-button>
        <span class="column-count" v-if="view.columns">
          {{ view.columns.filter(c => c.visible).length }} 列
        </span>
      </el-form-item>

      <el-divider />

      <div class="action-buttons">
        <el-button type="primary" @click="handleSave" :disabled="!hasChanges">
          保存
        </el-button>
        <el-button @click="emit('copy')">复制</el-button>
        <el-button type="danger" @click="handleDelete" :disabled="view.isDefault">
          删除
        </el-button>
      </div>
    </el-form>
  </div>
  <div v-else class="empty-panel">
    选择一个视图进行编辑
  </div>
</template>

<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import { ElMessageBox } from 'element-plus'
import type { ColumnView } from '@/types'
import { useAuthStore } from '@/stores/auth'

const props = defineProps<{
  view: ColumnView | null
}>()

const emit = defineEmits<{
  save: [name: string, isDefault: boolean]
  copy: []
  delete: []
  'edit-columns': []
}>()

const authStore = useAuthStore()
const isAdmin = computed(() => authStore.isAdmin)

const formData = ref({
  name: '',
  isDefault: false
})

const hasChanges = computed(() => {
  if (!props.view) return false
  return formData.value.name !== props.view.name
})

watch(() => props.view, (newView) => {
  if (newView) {
    formData.value.name = newView.name
    formData.value.isDefault = newView.isDefault
  }
}, { immediate: true })

function handleSave() {
  emit('save', formData.value.name, formData.value.isDefault)
}

async function handleDelete() {
  await ElMessageBox.confirm('确定要删除此视图吗？', '确认删除', { type: 'warning' })
  emit('delete')
}

function handleSetDefault(value: boolean) {
  if (value) {
    emit('save', formData.value.name, true)
  }
}
</script>

<style scoped>
.view-edit-panel { padding: 16px; }
.column-count { margin-left: 12px; color: #909399; font-size: 12px; }
.action-buttons { display: flex; gap: 8px; }
.empty-panel { display: flex; align-items: center; justify-content: center; height: 200px; color: #909399; }
</style>
