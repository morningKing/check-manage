/**
 * 左侧导航菜单组件
 *
 * 职责：
 * - 渲染多级嵌套菜单（支持1-3级）
 * - 处理菜单点击和路由跳转
 * - 支持菜单折叠/展开
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
      <span v-if="!sidebarCollapsed" class="logo-text">巡检管理</span>
    </div>

    <!-- 菜单列表 -->
    <el-scrollbar class="menu-scrollbar">
      <el-menu
        :default-active="activeMenu"
        :collapse="sidebarCollapsed"
        :collapse-transition="false"
        background-color="#304156"
        text-color="#bfcbd9"
        active-text-color="#409eff"
        router
        class="side-menu-list"
      >
        <!-- 递归渲染菜单项 -->
        <template v-for="menu in menuTree" :key="menu.id">
          <MenuItem :menu="menu" />
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
 * 1. 从 Store 获取菜单树数据
 * 2. 根据当前路由计算激活菜单
 * 3. 响应侧边栏折叠状态
 */
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { Monitor } from '@element-plus/icons-vue'
import { useAppStore, useMenuStore, useAuthStore } from '@/stores'
import MenuItem from './MenuItem.vue'
import type { MenuItem as MenuItemType } from '@/types'
import type { UserRole } from '@/types'

// ==================== Store ====================

const appStore = useAppStore()
const menuStore = useMenuStore()
const authStore = useAuthStore()
const route = useRoute()

// ==================== 计算属性 ====================

/**
 * 侧边栏是否折叠
 */
const sidebarCollapsed = computed(() => appStore.sidebarCollapsed)

/**
 * 根据角色过滤后的菜单树
 */
const menuTree = computed(() => {
  const tree = menuStore.menuTree
  return filterMenusByRole(tree, authStore.userRole)
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

// ==================== 方法 ====================

/**
 * 递归过滤菜单树（基于菜单的 roles 配置）
 */
function filterMenusByRole(menus: MenuItemType[], role: UserRole | null): MenuItemType[] {
  if (!role) return []

  return menus
    .map((menu) => {
      // 检查当前菜单是否对该角色可见
      const menuRoles = menu.roles || ['admin', 'developer', 'guest']
      if (!menuRoles.includes(role)) return null

      // 递归过滤子菜单
      const filteredChildren = menu.children
        ? filterMenusByRole(menu.children, role)
        : []
      return { ...menu, children: filteredChildren }
    })
    .filter(Boolean) as MenuItemType[]
}
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
  height: 60px;
  padding: 0 16px;
  background-color: #263445;
  border-bottom: 1px solid #1f2d3d;
  transition: all 0.3s ease;

  .logo-icon {
    font-size: 28px;
    color: #409eff;
  }

  .logo-text {
    margin-left: 12px;
    font-size: 18px;
    font-weight: 600;
    color: #fff;
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
  &:hover {
    background-color: #263445 !important;
  }

  &.is-active {
    background-color: #409eff !important;
    color: #fff !important;

    .el-icon {
      color: #fff !important;
    }
  }
}

:deep(.el-sub-menu__title) {
  &:hover {
    background-color: #263445 !important;
  }
}

:deep(.el-sub-menu .el-menu-item) {
  background-color: #1f2d3d !important;

  &:hover {
    background-color: #001528 !important;
  }

  &.is-active {
    background-color: #409eff !important;
  }
}
</style>
