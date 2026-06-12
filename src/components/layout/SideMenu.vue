/**
 * 左侧导航菜单组件
 *
 * 职责：
 * - 渲染多级嵌套菜单（支持1-3级）
 * - 处理菜单点击和路由跳转
 * - 支持菜单折叠/展开
 *
 * 性能优化：
 * - 懒加载：只渲染已展开的子菜单，减少初始 DOM 数量
 * - 追踪展开状态：通过 @open/@close 事件维护 openedMenuIds
 * - 使用 Set 存储展开状态，查找性能 O(1)
 *
 * 特性：
 * - 使用 Element Plus Menu 组件
 * - 递归渲染子菜单
 * - 自动根据路由高亮当前菜单
 */
<template>
  <div class="side-menu">
    <!-- Logo 区域 -->
    <div class="menu-logo" :class="{ collapsed: sidebarCollapsed }">
      <el-icon class="logo-icon"><Monitor /></el-icon>
      <span v-if="!sidebarCollapsed" class="logo-text">{{ systemShortName }}</span>
    </div>

    <!-- 菜单列表 -->
    <el-scrollbar class="menu-scrollbar">
      <el-menu
        :default-active="activeMenu"
        :collapse="sidebarCollapsed"
        :collapse-transition="false"
        background-color="#f7f8fa"
        text-color="#5c606b"
        active-text-color="#1a1d21"
        router
        class="side-menu-list"
        @open="handleMenuOpen"
        @close="handleMenuClose"
      >
        <!-- 递归渲染菜单项（传递展开状态实现懒加载 + 预渲染） -->
        <template v-for="menu in menuTree" :key="menu.id">
          <MenuItem :menu="menu" :opened-menu-ids="openedMenuIds" :preloaded-ids="preloadedIds" />
        </template>
      </el-menu>
    </el-scrollbar>
  </div>
</template>

<script setup lang="ts">
/**
 * SideMenu 组件脚本
 *
 * 主要逻辑：
 * 1. 从 Store 获取菜单树数据（使用缓存）
 * 2. 根据当前路由计算激活菜单
 * 3. 响应侧边栏折叠状态
 * 4. 追踪菜单展开状态（用于懒加载）
 *
 * 性能优化：
 * - 使用 store 中的 getFilteredMenuTree 方法获取缓存结果
 * - 避免本地重复计算菜单过滤
 * - 使用 Set 存储展开菜单ID，查找性能 O(1)
 */
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { Monitor } from '@element-plus/icons-vue'
import { useAppStore, useMenuStore, useAuthStore, useSystemConfigStore } from '@/stores'
import MenuItem from './MenuItem.vue'

// ==================== Store ====================

const appStore = useAppStore()
const menuStore = useMenuStore()
const authStore = useAuthStore()
const systemConfigStore = useSystemConfigStore()
const route = useRoute()

// ==================== 展开状态追踪（懒加载关键） ====================

/**
 * 已展开的菜单ID集合
 * 使用 Set 实现 O(1) 查找性能
 */
const openedMenuIds = ref(new Set<string>())

/**
 * 已预加载的菜单ID集合（hover预渲染）
 * 用于在鼠标 hover 时提前渲染子菜单
 */
const preloadedIds = ref(new Set<string>())

/**
 * 菜单展开事件处理
 * 将展开的菜单ID加入集合
 */
function handleMenuOpen(index: string): void {
  openedMenuIds.value.add(index)
}

/**
 * 菜单折叠事件处理
 * 将折叠的菜单ID从集合移除
 */
function handleMenuClose(index: string): void {
  openedMenuIds.value.delete(index)
}

// ==================== 计算属性 ====================

/**
 * 侧边栏是否折叠
 */
const sidebarCollapsed = computed(() => appStore.sidebarCollapsed)

/**
 * 系统简称（Logo 文字）
 */
const systemShortName = computed(() => systemConfigStore.systemShortName)

/**
 * 根据角色过滤后的菜单树
 *
 * 使用 store 中的缓存方法，避免重复计算
 */
const menuTree = computed(() => {
  return menuStore.getFilteredMenuTree(authStore.userRole, authStore.isAdmin)
})

/**
 * 当前激活的菜单
 *
 * 根据当前路由路径确定激活的菜单项
 */
const activeMenu = computed(() => {
  const currentMenu = menuStore.getMenuByPath(route.path)
  return currentMenu?.id || ''
})
</script>

<style scoped lang="scss">
.side-menu {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

/* Logo 区域 */
.menu-logo {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 52px;
  padding: 0 16px;
  background-color: #f7f8fa;
  border-bottom: 1px solid #ebecf0;
  transition: all 0.3s ease;

  .logo-icon {
    font-size: 28px;
    color: #1a1d21;
  }

  .logo-text {
    margin-left: 12px;
    font-size: 18px;
    font-weight: 600;
    color: #1a1d21;
    white-space: nowrap;
  }

  &.collapsed {
    padding: 0;

    .logo-icon {
      font-size: 24px;
    }
  }
}

/* 菜单滚动区域 */
.menu-scrollbar {
  flex: 1;
  overflow: hidden;
}

/* 菜单列表 */
.side-menu-list {
  border-right: none;

  &:not(.el-menu--collapse) {
    width: 240px;
  }
}

/* 覆盖 Element Plus 菜单样式 */
:deep(.el-menu-item) {
  height: 40px;
  line-height: 40px;

  &:hover {
    background-color: #f0f1f4 !important;
    color: #1a1d21 !important;
  }

  &.is-active {
    background-color: #eceef5 !important;
    color: #1a1d21 !important;
    font-weight: 500;

    .el-icon {
      color: #1a1d21 !important;
    }
  }
}

:deep(.el-sub-menu__title) {
  height: 40px;
  line-height: 40px;

  &:hover {
    background-color: #f0f1f4 !important;
    color: #1a1d21 !important;
  }
}

:deep(.el-sub-menu .el-menu-item) {
  background-color: #f7f8fa !important;

  &:hover {
    background-color: #f0f1f4 !important;
    color: #1a1d21 !important;
  }

  &.is-active {
    background-color: #eceef5 !important;
    color: #1a1d21 !important;
    font-weight: 500;
  }
}

/* 子菜单展开动画优化 - 加速动画 */
:deep(.el-sub-menu__title) {
  transition: all 0.1s ease !important;
}

:deep(.el-sub-menu .el-menu) {
  transition: all 0.1s ease !important;
}

:global(html.dark) {
  .menu-logo {
    background-color: #1a1d23;
    border-bottom-color: #282c34;
    .logo-icon, .logo-text { color: #e6e8eb; }
  }
  :deep(.el-menu) { background-color: #1a1d23 !important; }
  :deep(.el-menu-item), :deep(.el-sub-menu__title) { color: #c2c6cd !important; }
  :deep(.el-menu-item):hover, :deep(.el-sub-menu__title):hover {
    background-color: #23262e !important; color: #fff !important;
  }
  :deep(.el-menu-item.is-active) { background-color: #2a2e36 !important; color: #fff !important; }
  :deep(.el-sub-menu .el-menu-item) { background-color: #1a1d23 !important; }
}
</style>