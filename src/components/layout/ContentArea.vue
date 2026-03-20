/**
 * 内容区域组件
 *
 * 职责：
 * - 渲染标签页栏，支持多标签切换
 * - 作为路由视图的容器
 * - 提供过渡动画效果
 * - 标签页支持拖拽排序和关闭
 */
<template>
  <div class="content-area">
    <!-- 标签栏 -->
    <div class="tab-bar">
      <div class="tab-list" ref="tabListRef">
        <div
          v-for="(tab, index) in tabs"
          :key="tab.path"
          class="tab-item"
          :class="{ active: tab.path === activeTabPath }"
          draggable="true"
          @click="handleTabClick(tab)"
          @dragstart="handleDragStart($event, index)"
          @dragover.prevent="handleDragOver($event, index)"
          @drop="handleDrop($event, index)"
          @dragend="handleDragEnd"
          @contextmenu.prevent="handleContextMenu($event, tab)"
        >
          <span class="tab-name">{{ tab.name }}</span>
          <el-icon
            v-if="tab.closable"
            class="tab-close"
            @click.stop="handleTabClose(tab.path)"
          >
            <Close />
          </el-icon>
        </div>
      </div>
    </div>

    <!-- 右键菜单 -->
    <Teleport to="body">
      <div
        v-if="contextMenuVisible"
        class="tab-context-menu"
        :style="{ left: contextMenuX + 'px', top: contextMenuY + 'px' }"
      >
        <div class="context-menu-item" @click="handleCloseCurrent">关闭当前</div>
        <div class="context-menu-item" @click="handleCloseOthers">关闭其他</div>
        <div class="context-menu-item" @click="handleCloseAll">关闭所有</div>
      </div>
    </Teleport>

    <!-- 路由视图，带过渡动画 -->
    <div class="content-main">
      <router-view v-slot="{ Component, route }">
        <!-- 移除 mode="out-in" 以减少交互延迟（INP） -->
        <!-- 使用 keep-alive 缓存组件状态，避免重复渲染 -->
        <transition name="fade-fast">
          <keep-alive>
            <component :is="Component" :key="route.path" />
          </keep-alive>
        </transition>
      </router-view>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * ContentArea 组件脚本
 *
 * 主要功能：
 * 1. 标签栏渲染与交互
 * 2. 标签拖拽排序（HTML5 Drag & Drop）
 * 3. 右键菜单（关闭当前/其他/所有）
 * 4. 路由视图容器 + keep-alive 缓存
 */
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { Close } from '@element-plus/icons-vue'
import { useTabStore } from '@/stores'
import type { TabItem } from '@/stores/tab'

// ==================== Store & Router ====================

const tabStore = useTabStore()
const router = useRouter()

// ==================== 计算属性 ====================

const tabs = computed(() => tabStore.tabs)
const activeTabPath = computed(() => tabStore.activeTabPath)

// ==================== 标签点击与关闭 ====================

/**
 * 点击标签切换页面
 */
function handleTabClick(tab: TabItem): void {
  if (tab.path !== activeTabPath.value) {
    router.push(tab.path)
  }
}

/**
 * 关闭标签
 */
function handleTabClose(path: string): void {
  const navigateTo = tabStore.removeTab(path)
  if (navigateTo) {
    router.push(navigateTo)
  }
}

// ==================== 拖拽排序 ====================

const dragIndex = ref<number | null>(null)
const tabListRef = ref<HTMLElement>()

function handleDragStart(e: DragEvent, index: number): void {
  dragIndex.value = index
  if (e.dataTransfer) {
    e.dataTransfer.effectAllowed = 'move'
  }
}

function handleDragOver(e: DragEvent, index: number): void {
  if (dragIndex.value === null || dragIndex.value === index) return
  if (e.dataTransfer) {
    e.dataTransfer.dropEffect = 'move'
  }
}

function handleDrop(_e: DragEvent, index: number): void {
  if (dragIndex.value === null || dragIndex.value === index) return
  tabStore.moveTab(dragIndex.value, index)
  dragIndex.value = null
}

function handleDragEnd(): void {
  dragIndex.value = null
}

// ==================== 右键菜单 ====================

const contextMenuVisible = ref(false)
const contextMenuX = ref(0)
const contextMenuY = ref(0)
const contextMenuTab = ref<TabItem | null>(null)

function handleContextMenu(e: MouseEvent, tab: TabItem): void {
  contextMenuX.value = e.clientX
  contextMenuY.value = e.clientY
  contextMenuTab.value = tab
  contextMenuVisible.value = true
}

function closeContextMenu(): void {
  contextMenuVisible.value = false
  contextMenuTab.value = null
}

function handleCloseCurrent(): void {
  if (contextMenuTab.value?.closable) {
    handleTabClose(contextMenuTab.value.path)
  }
  closeContextMenu()
}

function handleCloseOthers(): void {
  if (contextMenuTab.value) {
    const navigateTo = tabStore.removeOtherTabs(contextMenuTab.value.path)
    if (navigateTo) {
      router.push(navigateTo)
    }
  }
  closeContextMenu()
}

function handleCloseAll(): void {
  const navigateTo = tabStore.removeAllTabs()
  router.push(navigateTo)
  closeContextMenu()
}

/**
 * 点击页面其他区域关闭右键菜单
 */
function handleDocumentClick(): void {
  closeContextMenu()
}

onMounted(() => {
  document.addEventListener('click', handleDocumentClick)
})

onUnmounted(() => {
  document.removeEventListener('click', handleDocumentClick)
})
</script>

<style scoped lang="scss">
.content-area {
  height: 100%;
  width: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* 标签栏 */
.tab-bar {
  flex-shrink: 0;
  background-color: #fff;
  border-bottom: 1px solid #e4e7ed;
  padding: 6px 12px 0;
}

.tab-list {
  display: flex;
  gap: 4px;
  overflow-x: auto;
  scrollbar-width: thin;

  &::-webkit-scrollbar {
    height: 3px;
  }
  &::-webkit-scrollbar-thumb {
    background: #c0c4cc;
    border-radius: 2px;
  }
}

.tab-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border: 1px solid #e4e7ed;
  border-bottom: none;
  border-radius: 4px 4px 0 0;
  background-color: #f5f7fa;
  cursor: pointer;
  white-space: nowrap;
  font-size: 13px;
  color: #606266;
  user-select: none;
  transition: background-color 0.2s, color 0.2s;

  &:hover {
    background-color: #ecf5ff;
    color: #409eff;
  }

  &.active {
    background-color: #fff;
    color: #409eff;
    font-weight: 500;
    border-bottom: 1px solid #fff;
    margin-bottom: -1px;
  }

  .tab-close {
    font-size: 12px;
    border-radius: 50%;
    padding: 1px;
    transition: background-color 0.2s, color 0.2s;

    &:hover {
      background-color: #f56c6c;
      color: #fff;
    }
  }
}

/* 路由内容区域 */
.content-main {
  flex: 1;
  overflow: auto;
}

/* 页面切换过渡动画 - 优化性能 */
.fade-fast-enter-active,
.fade-fast-leave-active {
  transition: opacity 0.15s ease;
}

.fade-fast-enter-from,
.fade-fast-leave-to {
  opacity: 0;
}
</style>

<!-- 右键菜单样式（全局，不加 scoped） -->
<style lang="scss">
.tab-context-menu {
  position: fixed;
  z-index: 9999;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 4px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.12);
  padding: 4px 0;
  min-width: 100px;

  .context-menu-item {
    padding: 6px 16px;
    font-size: 13px;
    color: #606266;
    cursor: pointer;
    white-space: nowrap;

    &:hover {
      background-color: #ecf5ff;
      color: #409eff;
    }
  }
}
</style>
