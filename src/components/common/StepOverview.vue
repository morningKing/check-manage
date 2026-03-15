/**
 * 步骤概览组件
 *
 * 职责：
 * - 展示变更统计（新增、删除、修改记录数量）
 * - 提供快捷操作按钮（全部接受源/目标版本）
 * - 提供"下一步"按钮进入选择步骤
 */
<template>
  <div class="step-overview">
    <!-- 变更统计卡片 -->
    <div class="statistics-cards">
      <div class="stat-card added">
        <div class="stat-icon">
          <el-icon><CirclePlusFilled /></el-icon>
        </div>
        <div class="stat-content">
          <div class="stat-value">{{ addedCount }}</div>
          <div class="stat-label">新增记录</div>
        </div>
      </div>

      <div class="stat-card removed">
        <div class="stat-icon">
          <el-icon><RemoveFilled /></el-icon>
        </div>
        <div class="stat-content">
          <div class="stat-value">{{ removedCount }}</div>
          <div class="stat-label">删除记录</div>
        </div>
      </div>

      <div class="stat-card modified">
        <div class="stat-icon">
          <el-icon><EditPen /></el-icon>
        </div>
        <div class="stat-content">
          <div class="stat-value">{{ modifiedCount }}</div>
          <div class="stat-label">修改记录</div>
        </div>
      </div>

      <div class="stat-card total">
        <div class="stat-icon">
          <el-icon><DataAnalysis /></el-icon>
        </div>
        <div class="stat-content">
          <div class="stat-value">{{ totalChanges }}</div>
          <div class="stat-label">变更总计</div>
        </div>
      </div>
    </div>

    <!-- 变更摘要 -->
    <div class="change-summary">
      <el-alert
        :title="summaryText"
        type="info"
        :closable="false"
        show-icon
      />
    </div>

    <!-- 无变更提示 -->
    <el-empty
      v-if="totalChanges === 0"
      description="没有需要处理的变更"
      :image-size="120"
    />

    <!-- 快捷操作按钮 -->
    <div v-if="totalChanges > 0" class="quick-actions">
      <div class="action-title">快捷操作</div>
      <div class="action-buttons">
        <el-button
          type="success"
          plain
          @click="handleAcceptAllSource"
        >
          <el-icon><Check /></el-icon>
          全部接受源版本
        </el-button>
        <el-button
          type="warning"
          plain
          @click="handleAcceptAllTarget"
        >
          <el-icon><Check /></el-icon>
          全部接受目标版本
        </el-button>
      </div>
      <div class="action-hint">
        <el-icon><InfoFilled /></el-icon>
        <span>快捷操作将预填充所有记录的处理决策，您仍可在后续步骤中调整</span>
      </div>
    </div>

    <!-- 操作指引 -->
    <div v-if="totalChanges > 0" class="guide-section">
      <el-steps :active="0" simple>
        <el-step title="概览" description="查看变更统计" />
        <el-step title="选择" description="选择要处理的记录" />
        <el-step title="确认" description="执行合并操作" />
      </el-steps>
    </div>

    <!-- 底部操作 -->
    <div class="footer-actions">
      <el-button @click="handleCancel">取消</el-button>
      <el-button
        type="primary"
        :disabled="totalChanges === 0"
        @click="handleNext"
      >
        下一步
        <el-icon class="el-icon--right"><ArrowRight /></el-icon>
      </el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import {
  CirclePlusFilled,
  RemoveFilled,
  EditPen,
  DataAnalysis,
  Check,
  InfoFilled,
  ArrowRight
} from '@element-plus/icons-vue'
import type { DiffResult } from '@/types'

// ==================== Props & Emits ====================

interface Props {
  diffResult: DiffResult
}

const props = defineProps<Props>()

const emit = defineEmits<{
  (e: 'accept-source'): void
  (e: 'accept-target'): void
  (e: 'next'): void
  (e: 'cancel'): void
}>()

// ==================== Computed ====================

/**
 * 新增记录数量
 */
const addedCount = computed(() => props.diffResult?.added?.length || 0)

/**
 * 删除记录数量
 */
const removedCount = computed(() => props.diffResult?.removed?.length || 0)

/**
 * 修改记录数量
 */
const modifiedCount = computed(() => props.diffResult?.modified?.length || 0)

/**
 * 变更总计
 */
const totalChanges = computed(() => addedCount.value + removedCount.value + modifiedCount.value)

/**
 * 变更摘要文本
 */
const summaryText = computed(() => {
  if (totalChanges.value === 0) {
    return '两个数据源完全一致，无需处理'
  }
  return `共有 ${totalChanges.value} 处变更需要处理`
})

// ==================== Methods ====================

/**
 * 处理全部接受源版本
 */
function handleAcceptAllSource(): void {
  emit('accept-source')
}

/**
 * 处理全部接受目标版本
 */
function handleAcceptAllTarget(): void {
  emit('accept-target')
}

/**
 * 处理下一步
 */
function handleNext(): void {
  emit('next')
}

/**
 * 处理取消
 */
function handleCancel(): void {
  emit('cancel')
}
</script>

<style scoped lang="scss">
.step-overview {
  padding: 20px;
}

.statistics-cards {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 24px;

  @media (max-width: 768px) {
    grid-template-columns: repeat(2, 1fr);
  }

  @media (max-width: 480px) {
    grid-template-columns: 1fr;
  }
}

.stat-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px;
  background: #fff;
  border-radius: 8px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
  border-left: 4px solid transparent;
  transition: transform 0.2s, box-shadow 0.2s;

  &:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
  }

  &.added {
    border-left-color: #67c23a;

    .stat-icon {
      background-color: rgba(103, 194, 58, 0.1);
      color: #67c23a;
    }
  }

  &.removed {
    border-left-color: #f56c6c;

    .stat-icon {
      background-color: rgba(245, 108, 108, 0.1);
      color: #f56c6c;
    }
  }

  &.modified {
    border-left-color: #e6a23c;

    .stat-icon {
      background-color: rgba(230, 162, 60, 0.1);
      color: #e6a23c;
    }
  }

  &.total {
    border-left-color: #409eff;

    .stat-icon {
      background-color: rgba(64, 158, 255, 0.1);
      color: #409eff;
    }
  }
}

.stat-icon {
  width: 48px;
  height: 48px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  font-size: 24px;
}

.stat-content {
  flex: 1;
  min-width: 0;
}

.stat-value {
  font-size: 28px;
  font-weight: 600;
  color: #303133;
  line-height: 1.2;
}

.stat-label {
  font-size: 13px;
  color: #909399;
  margin-top: 4px;
}

.change-summary {
  margin-bottom: 24px;
}

.quick-actions {
  background: #fafafa;
  border-radius: 8px;
  padding: 20px;
  margin-bottom: 24px;
}

.action-title {
  font-size: 14px;
  font-weight: 500;
  color: #303133;
  margin-bottom: 12px;
}

.action-buttons {
  display: flex;
  gap: 12px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.action-hint {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #909399;

  .el-icon {
    font-size: 14px;
  }
}

.guide-section {
  margin-bottom: 24px;
  padding: 16px;
  background: #fff;
  border-radius: 8px;
  border: 1px solid #ebeef5;
}

.footer-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  padding-top: 16px;
  border-top: 1px solid #ebeef5;
}
</style>