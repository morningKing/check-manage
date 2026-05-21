<template>
  <el-dialog
    v-model="visible"
    title="管理视图"
    width="700px"
    :close-on-click-modal="false"
  >
    <div class="view-manage-container">
      <div class="view-list">
        <div class="view-list-header">
          <span>视图列表</span>
          <el-button size="small" type="primary" @click="handleCreate">+ 新建</el-button>
        </div>
        <div class="view-list-content">
          <div
            v-for="view in views"
            :key="view.id"
            class="view-item"
            :class="{ active: selectedViewId === view.id }"
            @click="selectedViewId = view.id"
          >
            <div class="view-item-name">
              {{ view.name }}
              <el-tag v-if="view.isDefault" size="small" type="success">默认</el-tag>
            </div>
            <div class="view-item-meta">
              {{ view.isPublic ? '公共' : '私人' }}
              · {{ view.columns.filter(c => c.visible).length }}列
            </div>
          </div>
          <div v-if="views.length === 0" class="empty-list">暂无视图</div>
        </div>
      </div>

      <div class="view-edit">
        <ViewEditPanel
          :view="selectedView"
          @save="handleSave"
          @copy="handleCopy"
          @delete="handleDelete"
          @edit-columns="handleEditColumns"
        />
      </div>
    </div>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useColumnViewStore } from '@/stores/columnView'
import { useAuthStore } from '@/stores/auth'
import ViewEditPanel from './ViewEditPanel.vue'

const props = defineProps<{
  pageId: string
  fields: any[]
}>()

const authStore = useAuthStore()
const isAdmin = computed(() => authStore.isAdmin)

const emit = defineEmits<{
  'edit-columns': [view: any]
}>()

const columnViewStore = useColumnViewStore()

const visible = ref(false)
const selectedViewId = ref<number | null>(null)

const views = computed(() => columnViewStore.views)
const selectedView = computed(() =>
  views.value.find(v => v.id === selectedViewId.value) || null
)

watch(views, (newViews) => {
  if (newViews.length > 0 && !selectedViewId.value) {
    selectedViewId.value = newViews[0].id
  }
}, { immediate: true })

function open() {
  visible.value = true
  columnViewStore.loadViews(props.pageId)
}

function close() {
  visible.value = false
}

async function handleCreate() {
  try {
    let isPublic = false
    if (isAdmin.value) {
      try {
        await ElMessageBox.confirm(
          '视图类型：公共视图所有用户可见，私人视图仅自己可见',
          '新建视图',
          {
            distinguishCancelAndClose: true,
            confirmButtonText: '公共视图',
            cancelButtonText: '私人视图',
            type: 'info',
          }
        )
        isPublic = true
      } catch (action) {
        if (action !== 'cancel') return
        isPublic = false
      }
    }

    const { value: name } = await ElMessageBox.prompt('请输入视图名称', '新建视图', {
      inputPattern: /^.{1,50}$/,
      inputErrorMessage: '名称长度1-50个字符'
    }) as { value: string }

    const defaultColumns = props.fields
      .filter((f: any) => !f.hidden)
      .map((f: any, i: number) => ({
        fieldId: f.id,
        visible: true,
        order: i,
        width: f.width || 'auto'
      }))

    const newView = await columnViewStore.createView(props.pageId, {
      name,
      isPublic,
      columns: defaultColumns
    })

    selectedViewId.value = newView.id
    ElMessage.success('创建成功')
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error('创建失败')
    }
  }
}

async function handleSave(name: string, isDefault: boolean, isPublic: boolean) {
  if (!selectedView.value) return
  try {
    await columnViewStore.updateView(props.pageId, selectedView.value.id, { name, isPublic })
    if (isDefault) {
      await columnViewStore.setDefault(props.pageId, selectedView.value.id)
    }
    ElMessage.success('保存成功')
  } catch (error) {
    ElMessage.error('保存失败')
  }
}

async function handleCopy() {
  if (!selectedView.value) return
  try {
    const newView = await columnViewStore.copyView(props.pageId, selectedView.value.id)
    selectedViewId.value = newView.id
    ElMessage.success('复制成功')
  } catch (error) {
    ElMessage.error('复制失败')
  }
}

async function handleDelete() {
  if (!selectedView.value) return
  try {
    await columnViewStore.removeView(props.pageId, selectedView.value.id)
    selectedViewId.value = views.value[0]?.id || null
    ElMessage.success('删除成功')
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.error || '删除失败')
  }
}

function handleEditColumns() {
  if (!selectedView.value) return
  emit('edit-columns', selectedView.value)
  close()
}

defineExpose({ open, close })
</script>

<style scoped>
.view-manage-container { display: flex; gap: 16px; min-height: 400px; }
.view-list { width: 250px; border: 1px solid #ebeef5; border-radius: 4px; overflow: hidden; }
.view-list-header { display: flex; justify-content: space-between; align-items: center; padding: 12px; background: #f5f7fa; border-bottom: 1px solid #ebeef5; font-weight: 500; }
.view-list-content { max-height: 350px; overflow-y: auto; }
.view-item { padding: 12px; border-bottom: 1px solid #ebeef5; cursor: pointer; transition: background 0.2s; }
.view-item:hover { background: #f5f7fa; }
.view-item.active { background: #ecf5ff; border-left: 3px solid #409eff; }
.view-item-name { font-size: 14px; margin-bottom: 4px; display: flex; align-items: center; gap: 8px; }
.view-item-meta { font-size: 12px; color: #909399; }
.empty-list { padding: 40px; text-align: center; color: #909399; }
.view-edit { flex: 1; border: 1px solid #ebeef5; border-radius: 4px; }
</style>
