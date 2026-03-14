/**
 * 命令面板组件
 *
 * 提供类似 VS Code Ctrl+P 的快速搜索功能：
 * - 快捷键唤起搜索面板
 * - 模糊搜索匹配菜单名称
 * - 显示最近访问记录
 * - 键盘导航支持
 */
<template>
  <Teleport to="body">
    <Transition name="fade">
      <div v-if="visible" class="command-palette-overlay" @click.self="handleClose">
        <div class="command-palette">
          <!-- 搜索输入框 -->
          <div class="search-header">
            <el-icon class="search-icon"><Search /></el-icon>
            <input
              ref="searchInputRef"
              v-model="searchQuery"
              type="text"
              class="search-input"
              placeholder="搜索菜单... (Ctrl+K)"
              @keydown="handleKeydown"
            />
            <el-button
              v-if="searchQuery"
              text
              size="small"
              class="clear-btn"
              @click="clearSearch"
            >
              <el-icon><Close /></el-icon>
            </el-button>
          </div>

          <!-- 结果区域 -->
          <div class="results-container" ref="resultsContainerRef">
            <!-- 最近访问 -->
            <div v-if="showRecentPages" class="result-section">
              <div class="section-header">
                <el-icon><Clock /></el-icon>
                <span>最近访问</span>
                <el-button
                  v-if="recentPages.length > 0"
                  text
                  size="small"
                  class="clear-recent-btn"
                  @click="handleClearRecent"
                >
                  清空
                </el-button>
              </div>
              <div class="result-list">
                <div
                  v-for="(page, index) in recentPages"
                  :key="page.path"
                  class="result-item"
                  :class="{ active: activeIndex === index }"
                  @click="handleSelectPage(page)"
                  @mouseenter="activeIndex = index"
                >
                  <el-icon class="item-icon"><Document /></el-icon>
                  <span class="item-name">{{ page.name }}</span>
                  <span class="item-path">{{ page.path }}</span>
                </div>
                <div v-if="recentPages.length === 0" class="empty-hint">
                  暂无最近访问记录
                </div>
              </div>
            </div>

            <!-- 搜索结果 -->
            <div v-if="searchQuery" class="result-section">
              <div class="section-header">
                <el-icon><Search /></el-icon>
                <span>搜索结果</span>
                <span v-if="filteredMenus.length > 0" class="result-count">
                  {{ filteredMenus.length }} 个结果
                </span>
              </div>
              <div class="result-list">
                <div
                  v-for="(menu, index) in filteredMenus"
                  :key="menu.id"
                  class="result-item"
                  :class="{ active: activeIndex === getSearchResultIndex(index) }"
                  @click="handleSelectMenu(menu)"
                  @mouseenter="activeIndex = getSearchResultIndex(index)"
                >
                  <el-icon class="item-icon"><Document /></el-icon>
                  <span class="item-name" v-html="highlightMatch(menu.name)"></span>
                  <span class="item-path">{{ menu.path }}</span>
                </div>
                <div v-if="filteredMenus.length === 0" class="empty-hint">
                  未找到匹配的菜单
                </div>
              </div>
            </div>
          </div>

          <!-- 底部提示 -->
          <div class="footer-hint">
            <span><kbd>↑</kbd> <kbd>↓</kbd> 选择</span>
            <span><kbd>Enter</kbd> 确认</span>
            <span><kbd>Esc</kbd> 关闭</span>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, computed, watch, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { Search, Close, Clock, Document } from '@element-plus/icons-vue'
import { useMenuStore, useTabStore } from '@/stores'
import type { MenuItem } from '@/types'
import type { RecentPage } from '@/stores/tab'

// ==================== Props & Emits ====================

const props = defineProps<{
  visible: boolean
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  (e: 'close'): void
}>()

// ==================== Stores ====================

const menuStore = useMenuStore()
const tabStore = useTabStore()
const router = useRouter()

// ==================== State ====================

const searchQuery = ref('')
const searchInputRef = ref<HTMLInputElement>()
const resultsContainerRef = ref<HTMLElement>()
const activeIndex = ref(0)

// ==================== Computed ====================

/** 最近访问的页面 */
const recentPages = computed(() => tabStore.recentPages)

/** 是否显示最近访问区域 */
const showRecentPages = computed(() => !searchQuery.value)

/** 过滤后的菜单列表 */
const filteredMenus = computed(() => {
  if (!searchQuery.value) return []

  const query = searchQuery.value.toLowerCase().trim()
  const menus = menuStore.menuList.filter(menu => {
    // 只显示有路径的菜单
    if (!menu.path) return false

    // 模糊匹配名称
    const nameMatch = menu.name.toLowerCase().includes(query)

    // 模糊匹配路径
    const pathMatch = menu.path.toLowerCase().includes(query)

    // 拼音首字母匹配（简单实现）
    const pinyinMatch = matchPinyinInitials(menu.name, query)

    return nameMatch || pathMatch || pinyinMatch
  })

  return menus
})

/** 可选项总数 */
const totalOptions = computed(() => {
  if (searchQuery.value) {
    return filteredMenus.value.length
  }
  return recentPages.value.length
})

// ==================== Methods ====================

/**
 * 拼音首字母匹配
 *
 * 简单实现：将中文字符转换为拼音首字母
 */
function matchPinyinInitials(name: string, query: string): boolean {
  const initials = getPinyinInitials(name)
  return initials.toLowerCase().includes(query.toLowerCase())
}

/**
 * 获取拼音首字母
 *
 * 简单实现：使用常见汉字拼音映射
 */
function getPinyinInitials(text: string): string {
  const pinyinMap: Record<string, string> = {
    '巡': 'x', '检': 'j', '用': 'y', '例': 'l',
    '计': 'j', '划': 'h', '配': 'p', '置': 'z',
    '数': 's', '据': 'j', '管': 'g', '理': 'l',
    '系': 'x', '统': 't', '设': 's', '备': 'b',
    '人': 'r', '员': 'y', '角': 'j', '色': 's',
    '菜': 'c', '单': 'd', '页': 'y', '面': 'm',
    '首': 's', '登': 'd', '录': 'l',
    '模': 'm', '板': 'b', '报': 'b', '表': 'b',
    '日': 'r', '志': 'z', '份': 'f',
    '还': 'h', '原': 'y', '导': 'd', '出': 'c',
    '入': 'r', '删': 's', '除': 'c', '修': 'x',
    '改': 'g', '添': 't', '加': 'j', '查': 'c',
    '看': 'k', '编': 'b', '辑': 'j', '搜': 's',
    '索': 's', '筛': 's', '选': 'x',
    '测': 'c', '试': 's', '功': 'g', '能': 'n',
    '家': 'j', '庭': 't', '企': 'q', '业': 'y',
    '工': 'g', '作': 'z', '目': 'm', '标': 'b',
    '项': 'x', '务': 'w', '任': 'r'
  }

  let result = ''
  for (const char of text) {
    // 如果是字母，直接使用
    if (/[a-zA-Z]/.test(char)) {
      result += char.toLowerCase()
      continue
    }
    // 如果是中文，查找拼音首字母
    if (pinyinMap[char]) {
      result += pinyinMap[char]
    }
  }
  return result
}

/**
 * 高亮匹配文本
 */
function highlightMatch(text: string): string {
  if (!searchQuery.value) return text

  const query = searchQuery.value.toLowerCase()
  const lowerText = text.toLowerCase()
  const index = lowerText.indexOf(query)

  if (index === -1) return text

  const before = text.slice(0, index)
  const match = text.slice(index, index + query.length)
  const after = text.slice(index + query.length)

  return `${before}<mark class="highlight">${match}</mark>${after}`
}

/**
 * 获取搜索结果的索引（考虑最近访问区域）
 */
function getSearchResultIndex(index: number): number {
  return index
}

/**
 * 处理选择页面
 */
function handleSelectPage(page: RecentPage): void {
  router.push(page.path)
  handleClose()
}

/**
 * 处理选择菜单
 */
function handleSelectMenu(menu: MenuItem): void {
  if (menu.path) {
    router.push(menu.path)
    handleClose()
  }
}

/**
 * 清空搜索
 */
function clearSearch(): void {
  searchQuery.value = ''
  activeIndex.value = 0
}

/**
 * 清空最近访问记录
 */
function handleClearRecent(): void {
  tabStore.clearRecentPages()
}

/**
 * 关闭面板
 */
function handleClose(): void {
  emit('update:visible', false)
  emit('close')
  searchQuery.value = ''
  activeIndex.value = 0
}

/**
 * 键盘事件处理
 */
function handleKeydown(e: KeyboardEvent): void {
  switch (e.key) {
    case 'ArrowDown':
      e.preventDefault()
      activeIndex.value = Math.min(activeIndex.value + 1, totalOptions.value - 1)
      scrollToActive()
      break
    case 'ArrowUp':
      e.preventDefault()
      activeIndex.value = Math.max(activeIndex.value - 1, 0)
      scrollToActive()
      break
    case 'Enter':
      e.preventDefault()
      selectCurrentItem()
      break
    case 'Escape':
      e.preventDefault()
      handleClose()
      break
  }
}

/**
 * 选择当前高亮项
 */
function selectCurrentItem(): void {
  if (searchQuery.value) {
    const menu = filteredMenus.value[activeIndex.value]
    if (menu) {
      handleSelectMenu(menu)
    }
  } else {
    const page = recentPages.value[activeIndex.value]
    if (page) {
      handleSelectPage(page)
    }
  }
}

/**
 * 滚动到当前高亮项
 */
function scrollToActive(): void {
  nextTick(() => {
    const container = resultsContainerRef.value
    if (!container) return

    const activeEl = container.querySelector('.result-item.active') as HTMLElement
    if (activeEl) {
      activeEl.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
    }
  })
}

// ==================== Watch ====================

/**
 * 监听搜索词变化，重置选中索引
 */
watch(searchQuery, () => {
  activeIndex.value = 0
})

/**
 * 监听可见性，确保 activeIndex 在有效范围内
 */
watch([searchQuery, filteredMenus, recentPages], () => {
  // 当搜索结果变化时，确保 activeIndex 在有效范围内
  if (activeIndex.value >= totalOptions.value) {
    activeIndex.value = Math.max(0, totalOptions.value - 1)
  }
})

/**
 * 监听 visible 变化，打开时自动聚焦输入框
 */
watch(() => props.visible, (newVal) => {
  if (newVal) {
    // 使用 nextTick 等待 DOM 渲染完成后再聚焦
    nextTick(() => {
      searchInputRef.value?.focus()
    })
  }
})

// ==================== Lifecycle ====================
</script>

<style scoped lang="scss">
.command-palette-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background-color: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding-top: 15vh;
  z-index: 9999;
}

.command-palette {
  width: 560px;
  max-height: 70vh;
  background-color: #fff;
  border-radius: 8px;
  box-shadow: 0 4px 24px rgba(0, 0, 0, 0.2);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.search-header {
  display: flex;
  align-items: center;
  padding: 12px 16px;
  border-bottom: 1px solid #e4e7ed;
  gap: 12px;

  .search-icon {
    font-size: 20px;
    color: #909399;
  }

  .search-input {
    flex: 1;
    border: none;
    outline: none;
    font-size: 16px;
    color: #303133;

    &::placeholder {
      color: #c0c4cc;
    }
  }

  .clear-btn {
    padding: 4px;
  }
}

.results-container {
  flex: 1;
  overflow-y: auto;
  padding: 8px 0;
}

.result-section {
  &:not(:last-child) {
    border-bottom: 1px solid #e4e7ed;
    padding-bottom: 8px;
    margin-bottom: 8px;
  }
}

.section-header {
  display: flex;
  align-items: center;
  padding: 8px 16px;
  font-size: 12px;
  color: #909399;
  gap: 6px;

  .el-icon {
    font-size: 14px;
  }

  .result-count {
    margin-left: auto;
    color: #c0c4cc;
  }

  .clear-recent-btn {
    margin-left: auto;
    font-size: 12px;
    padding: 2px 6px;
  }
}

.result-list {
  padding: 0 8px;
}

.result-item {
  display: flex;
  align-items: center;
  padding: 10px 12px;
  border-radius: 6px;
  cursor: pointer;
  transition: background-color 0.15s;
  gap: 10px;

  &:hover,
  &.active {
    background-color: #f0f7ff;
  }

  &.active {
    background-color: #e6f0fa;
  }

  .item-icon {
    font-size: 18px;
    color: #909399;
  }

  .item-name {
    flex: 1;
    font-size: 14px;
    color: #303133;

    :deep(.highlight) {
      background-color: #ffc107;
      padding: 0 2px;
      border-radius: 2px;
      color: inherit;
    }
  }

  .item-path {
    font-size: 12px;
    color: #c0c4cc;
    max-width: 120px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.empty-hint {
  padding: 20px;
  text-align: center;
  color: #c0c4cc;
  font-size: 14px;
}

.footer-hint {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 16px;
  padding: 10px 16px;
  border-top: 1px solid #e4e7ed;
  font-size: 12px;
  color: #909399;

  kbd {
    display: inline-block;
    padding: 2px 6px;
    background-color: #f5f7fa;
    border: 1px solid #e4e7ed;
    border-radius: 4px;
    font-family: inherit;
    font-size: 11px;
  }
}

/* Transition */
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease;

  .command-palette {
    transition: transform 0.2s ease, opacity 0.2s ease;
  }
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;

  .command-palette {
    transform: scale(0.95) translateY(-20px);
    opacity: 0;
  }
}
</style>