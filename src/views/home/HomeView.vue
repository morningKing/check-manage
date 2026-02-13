/**
 * 首页视图
 *
 * 职责：
 * - 展示系统欢迎信息
 * - 显示系统概览数据
 */
<template>
  <div class="home-view">
    <el-row :gutter="20">
      <!-- 欢迎卡片 -->
      <el-col :span="24">
        <el-card class="welcome-card">
          <div class="welcome-content">
            <el-icon class="welcome-icon"><Monitor /></el-icon>
            <div class="welcome-text">
              <h1>欢迎使用巡检用例管理系统</h1>
              <p>本系统支持动态配置菜单和页面，实现灵活的巡检用例管理。</p>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="20" class="mt-lg">
      <!-- 统计卡片 -->
      <el-col :span="8">
        <el-card class="stat-card">
          <div class="stat-content">
            <el-icon class="stat-icon stat-icon-primary"><Document /></el-icon>
            <div class="stat-info">
              <div class="stat-value">{{ menuCount }}</div>
              <div class="stat-label">菜单数量</div>
            </div>
          </div>
        </el-card>
      </el-col>

      <el-col :span="8">
        <el-card class="stat-card">
          <div class="stat-content">
            <el-icon class="stat-icon stat-icon-success"><Files /></el-icon>
            <div class="stat-info">
              <div class="stat-value">{{ pageCount }}</div>
              <div class="stat-label">页面配置</div>
            </div>
          </div>
        </el-card>
      </el-col>

      <el-col :span="8">
        <el-card class="stat-card">
          <div class="stat-content">
            <el-icon class="stat-icon stat-icon-warning"><Setting /></el-icon>
            <div class="stat-info">
              <div class="stat-value">{{ totalFields }}</div>
              <div class="stat-label">字段配置</div>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="20" class="mt-lg">
      <!-- 快捷入口 -->
      <el-col :span="12">
        <el-card>
          <template #header>
            <div class="card-header">
              <span>快捷入口</span>
            </div>
          </template>
          <div class="quick-links">
            <router-link to="/admin/menu" class="quick-link">
              <el-icon><Menu /></el-icon>
              <span>菜单管理</span>
            </router-link>
            <router-link to="/admin/page-config" class="quick-link">
              <el-icon><Files /></el-icon>
              <span>页面配置</span>
            </router-link>
          </div>
        </el-card>
      </el-col>

      <!-- 系统说明 -->
      <el-col :span="12">
        <el-card>
          <template #header>
            <div class="card-header">
              <span>系统说明</span>
            </div>
          </template>
          <div class="system-info">
            <p><strong>技术栈：</strong>Vue 3 + TypeScript + Element Plus + Pinia</p>
            <p><strong>主要功能：</strong></p>
            <ul>
              <li>支持 1-3 级嵌套菜单配置</li>
              <li>页面字段可视化配置</li>
              <li>多种表单控件类型支持</li>
              <li>动态数据页面渲染</li>
            </ul>
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
/**
 * HomeView 组件脚本
 *
 * 显示系统概览和统计信息
 */
import { computed } from 'vue'
import { Monitor, Document, Files, Setting, Menu } from '@element-plus/icons-vue'
import { useMenuStore, usePageConfigStore } from '@/stores'

// ==================== Store ====================

const menuStore = useMenuStore()
const pageConfigStore = usePageConfigStore()

// ==================== 计算属性 ====================

/**
 * 菜单数量
 */
const menuCount = computed(() => menuStore.menuList.length)

/**
 * 页面配置数量
 */
const pageCount = computed(() => pageConfigStore.pageConfigs.length)

/**
 * 总字段数量
 */
const totalFields = computed(() => {
  return pageConfigStore.pageConfigs.reduce((total, config) => {
    return total + config.fields.length
  }, 0)
})
</script>

<style scoped lang="scss">
.home-view {
  padding: 0;
}

.welcome-card {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);

  .welcome-content {
    display: flex;
    align-items: center;
    gap: 24px;
    padding: 20px;
  }

  .welcome-icon {
    font-size: 64px;
    color: rgba(255, 255, 255, 0.9);
  }

  .welcome-text {
    h1 {
      margin: 0 0 8px 0;
      font-size: 28px;
      color: #fff;
    }

    p {
      margin: 0;
      font-size: 16px;
      color: rgba(255, 255, 255, 0.85);
    }
  }
}

.stat-card {
  .stat-content {
    display: flex;
    align-items: center;
    gap: 16px;
    padding: 8px 0;
  }

  .stat-icon {
    font-size: 48px;
    padding: 12px;
    border-radius: 12px;

    &.stat-icon-primary {
      color: #409eff;
      background-color: #ecf5ff;
    }

    &.stat-icon-success {
      color: #67c23a;
      background-color: #f0f9eb;
    }

    &.stat-icon-warning {
      color: #e6a23c;
      background-color: #fdf6ec;
    }
  }

  .stat-info {
    .stat-value {
      font-size: 32px;
      font-weight: 600;
      color: #303133;
    }

    .stat-label {
      font-size: 14px;
      color: #909399;
      margin-top: 4px;
    }
  }
}

.card-header {
  display: flex;
  align-items: center;
  font-weight: 600;
}

.quick-links {
  display: flex;
  gap: 16px;

  .quick-link {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 8px;
    padding: 20px 32px;
    background-color: #f5f7fa;
    border-radius: 8px;
    color: #606266;
    text-decoration: none;
    transition: all 0.3s ease;

    &:hover {
      background-color: #ecf5ff;
      color: #409eff;
      transform: translateY(-2px);
    }

    .el-icon {
      font-size: 32px;
    }

    span {
      font-size: 14px;
    }
  }
}

.system-info {
  font-size: 14px;
  color: #606266;
  line-height: 1.8;

  p {
    margin: 8px 0;
  }

  ul {
    margin: 8px 0;
    padding-left: 20px;

    li {
      margin: 4px 0;
    }
  }
}

.mt-lg {
  margin-top: 24px;
}
</style>
