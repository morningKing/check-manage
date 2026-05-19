/**
 * 菜单项组件（递归 + 预渲染优化）
 *
 * 职责：
 * - 渲染单个菜单项或子菜单
 * - 支持递归渲染多级菜单
 * - 显示菜单图标和名称
 *
 * 性能优化：
 * - 预渲染策略：鼠标 hover 时提前渲染子菜单（预加载）
 * - 懒加载：未 hover/展开时不渲染深层子菜单
 * - 减少首次展开时的渲染开销
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
    :teleported="false"
    @mouseenter="handleMouseEnter"
  >
    <template #title>
      <el-icon v-if="menu.icon">
        <component :is="menu.icon" />
      </el-icon>
      <span>{{ menu.name }}</span>
    </template>

    <!-- 预渲染：hover 或展开时渲染子菜单 -->
    <template v-if="shouldRenderChildren">
      <template v-for="child in menu.children" :key="child.id">
        <MenuItem :menu="child" :opened-menu-ids="openedMenuIds" :preloaded-ids="preloadedIds" />
      </template>
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
 * - openedMenuIds: 已展开的菜单ID集合
 * - preloadedIds: 已预加载的菜单ID集合（hover预渲染）
 *
 * 预渲染策略：
 * - 鼠标 hover 时将菜单ID加入 preloadedIds
 * - 子菜单在 hover 或展开时才渲染
 * - 减少首次展开时的渲染延迟
 */
import { computed, ref } from 'vue'
import type { MenuItem as MenuItemType } from '@/types'

// ==================== Props ====================

interface Props {
  /** 菜单项数据 */
  menu: MenuItemType
  /** 已展开的菜单ID集合 */
  openedMenuIds?: Set<string>
  /** 已预加载的菜单ID集合（hover预渲染） */
  preloadedIds?: Set<string>
}

const props = defineProps<Props>()

// ==================== 本地状态 ====================

/**
 * 本地 hover 状态（用于预渲染）
 */
const isHovered = ref(false)

// ==================== 计算属性 ====================

/**
 * 是否有子菜单
 */
const hasChildren = computed(() => {
  return props.menu.children && props.menu.children.length > 0
})

/**
 * 是否应该渲染子菜单
 * 条件：已展开 OR 已预加载 OR 本地 hover
 */
const shouldRenderChildren = computed(() => {
  // 兜底：没有传入状态时默认渲染
  if (!props.openedMenuIds) return true

  // 已展开
  if (props.openedMenuIds.has(props.menu.id)) return true

  // 已预加载（父级 hover 传递）
  if (props.preloadedIds?.has(props.menu.id)) return true

  // 本地 hover（当前菜单 hover）
  if (isHovered.value) return true

  return false
})

// ==================== 事件处理 ====================

/**
 * 鼠标进入事件（预渲染触发）
 */
function handleMouseEnter(): void {
  if (!props.preloadedIds) return

  // 添加到预加载集合
  props.preloadedIds.add(props.menu.id)

  // 设置本地 hover 状态
  isHovered.value = true
}
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