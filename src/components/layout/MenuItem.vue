/**
 * 菜单项组件（递归）
 *
 * 职责：
 * - 渲染单个菜单项或子菜单
 * - 支持递归渲染多级菜单
 * - 显示菜单图标和名称
 *
 * 递归逻辑：
 * - 如果菜单有 children，渲染为 el-sub-menu（可展开）
 * - 如果菜单没有 children，渲染为 el-menu-item（可点击）
 */
<template>
  <!-- 有子菜单时渲染为 sub-menu -->
  <el-sub-menu
    v-if="hasChildren"
    :index="menu.id"
    :popper-class="'side-menu-popper'"
  >
    <template #title>
      <el-icon v-if="menu.icon">
        <component :is="menu.icon" />
      </el-icon>
      <span>{{ menu.name }}</span>
    </template>

    <!-- 递归渲染子菜单 -->
    <template v-for="child in menu.children" :key="child.id">
      <MenuItem :menu="child" />
    </template>
  </el-sub-menu>

  <!-- 没有子菜单时渲染为 menu-item -->
  <el-menu-item
    v-else
    :index="menu.path || menu.id"
    :route="menu.path ? { path: menu.path } : undefined"
  >
    <el-icon v-if="menu.icon">
      <component :is="menu.icon" />
    </el-icon>
    <template #title>
      <span>{{ menu.name }}</span>
    </template>
  </el-menu-item>
</template>

<script setup lang="ts">
/**
 * MenuItem 组件脚本
 *
 * Props：
 * - menu: 菜单项数据
 *
 * 递归实现：
 * - 组件内部引用自身来渲染子菜单
 */
import { computed } from 'vue'
import type { MenuItem as MenuItemType } from '@/types'

// ==================== Props ====================

interface Props {
  /** 菜单项数据 */
  menu: MenuItemType
}

const props = defineProps<Props>()

// ==================== 计算属性 ====================

/**
 * 是否有子菜单
 */
const hasChildren = computed(() => {
  return props.menu.children && props.menu.children.length > 0
})
</script>

<style lang="scss">
/* 子菜单弹出层样式（全局） */
.side-menu-popper {
  .el-menu {
    background-color: #1f2d3d !important;
  }

  .el-menu-item {
    background-color: #1f2d3d !important;

    &:hover {
      background-color: #001528 !important;
    }

    &.is-active {
      background-color: #409eff !important;
      color: #fff !important;
    }
  }
}
</style>
