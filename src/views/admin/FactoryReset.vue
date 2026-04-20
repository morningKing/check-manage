/**
 * 恢复出厂设置页面（隐藏页面）
 *
 * 通过 /admin/factory-reset 直接访问，不添加菜单项。
 * 仅管理员可访问。
 *
 * 职责：
 * - 显示详细警告信息
 * - 执行前自动创建备份
 * - 删除所有动态业务数据，保留系统配置
 */
<template>
  <div class="factory-reset-page">
    <el-card>
      <template #header>
        <div class="card-header">
          <el-icon :size="24" color="#f56c6c"><WarningFilled /></el-icon>
          <h2 style="margin-left: 8px; color: #f56c6c">恢复出厂设置</h2>
        </div>
      </template>

      <el-alert type="error" :closable="false" show-icon style="margin-bottom: 24px">
        <template #title>
          <strong>警告：此操作将永久删除所有动态数据，不可撤销！</strong>
        </template>
      </el-alert>

      <div class="info-section">
        <h3>删除的数据：</h3>
        <ul class="delete-list">
          <li>所有动态业务数据（dynamic_data）</li>
          <li>所有数据关联关系（data_relations）</li>
          <li>所有版本历史记录（collection_versions, version_snapshots）</li>
          <li>所有操作日志（operation_logs, etl_logs）</li>
          <li>所有通知和提醒（notifications, reminders）</li>
          <li>动态创建的菜单（menus）</li>
          <li>动态创建的页面配置（page_configs）</li>
        </ul>

        <h3 style="color: #409eff; margin-top: 16px">保留的系统配置：</h3>
        <ul class="keep-list">
          <li>系统默认菜单（首页、巡检管理、系统配置等）</li>
          <li>系统默认页面配置（巡检用例、巡检计划等）</li>
          <li>用户账户（users）</li>
          <li>API密钥（api_keys）</li>
          <li>脚本配置（export_scripts, validation_scripts, etl_tasks）</li>
          <li>备份记录（backups）</li>
        </ul>
      </div>

      <el-divider />

      <el-alert type="info" :closable="false" show-icon style="margin-bottom: 24px">
        <template #title>
          安全机制：执行前将自动创建备份
        </template>
        <div style="margin-top: 8px">
          系统会在执行恢复出厂设置前自动创建一个备份，标记为 pre-reset。
          如果您后悔或需要恢复数据，可以从备份列表中还原。
        </div>
      </el-alert>

      <el-button type="danger" size="large" @click="showConfirmDialog" :disabled="resetting">
        <el-icon><Delete /></el-icon>
        恢复出厂设置
      </el-button>
    </el-card>

    <!-- 确认对话框 -->
    <el-dialog
      v-model="confirmDialogVisible"
      title="恢复出厂设置确认"
      width="500px"
      :close-on-click-modal="false"
    >
      <el-alert type="error" :closable="false" show-icon style="margin-bottom: 20px">
        <template #title>
          <strong>警告：此操作不可撤销！</strong>
        </template>
      </el-alert>

      <p style="margin-bottom: 16px">
        请输入 <strong style="color: #f56c6c">RESET</strong> 以确认此操作：
      </p>

      <el-input v-model="confirmText" placeholder="请输入 RESET" style="width: 200px" />

      <template #footer>
        <el-button @click="confirmDialogVisible = false">取消</el-button>
        <el-button
          type="danger"
          @click="handleFactoryReset"
          :loading="resetting"
          :disabled="confirmText !== 'RESET'"
        >
          确认执行
        </el-button>
      </template>
    </el-dialog>

    <!-- 结果对话框 -->
    <el-dialog v-model="resultDialogVisible" title="执行结果" width="500px">
      <el-alert type="success" :closable="false" show-icon style="margin-bottom: 16px">
        <template #title>恢复出厂设置成功</template>
      </el-alert>

      <div class="result-info">
        <p>已删除以下表的数据：</p>
        <ul>
          <li v-for="table in result?.deletedTables" :key="table">
            {{ table }}: {{ result?.deletedRecords[table] || 0 }} 条记录
          </li>
        </ul>
        <p style="margin-top: 16px; color: #409eff">
          自动备份ID：{{ result?.backupId }}
        </p>
      </div>

      <template #footer>
        <el-button type="primary" @click="handleRefresh">刷新页面</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { WarningFilled, Delete } from '@element-plus/icons-vue'
import { factoryReset } from '@/api/backup'

const confirmDialogVisible = ref(false)
const confirmText = ref('')
const resetting = ref(false)
const resultDialogVisible = ref(false)
const result = ref<{
  message: string
  deletedTables: string[]
  deletedRecords: Record<string, number>
  backupId: string
  timestamp: string
} | null>(null)

function showConfirmDialog() {
  confirmText.value = ''
  confirmDialogVisible.value = true
}

async function handleFactoryReset() {
  if (confirmText.value !== 'RESET') {
    ElMessage.warning('请输入正确的确认文字')
    return
  }

  resetting.value = true
  try {
    result.value = await factoryReset('RESET')
    confirmDialogVisible.value = false
    resultDialogVisible.value = true
  } catch (error: any) {
    ElMessage.error(error.response?.data?.error || '恢复出厂设置失败')
  } finally {
    resetting.value = false
  }
}

function handleRefresh() {
  window.location.reload()
}
</script>

<style scoped lang="scss">
.factory-reset-page {
  padding: 20px;
}

.card-header {
  display: flex;
  align-items: center;
}

.info-section {
  margin-bottom: 24px;

  h3 {
    margin-bottom: 8px;
    font-size: 16px;
  }

  ul {
    margin: 0;
    padding-left: 24px;
  }
}

.delete-list {
  li {
    color: #f56c6c;
    margin-bottom: 4px;
  }
}

.keep-list {
  li {
    color: #409eff;
    margin-bottom: 4px;
  }
}

.result-info {
  ul {
    margin: 8px 0;
    padding-left: 24px;
  }
}
</style>