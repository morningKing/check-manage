/**
 * 主布局组件
 *
 * 职责：
 * - 定义应用的整体布局结构（左侧导航 + 右侧内容）
 * - 管理侧边栏的折叠/展开
 * - 初始化应用数据
 *
 * 布局结构：
 * ┌─────────────────────────────────────┐
 * │  ┌──────┐  ┌─────────────────────┐  │
 * │  │      │  │                     │  │
 * │  │ Side │  │     Content         │  │
 * │  │ Menu │  │      Area           │  │
 * │  │      │  │                     │  │
 * │  └──────┘  └─────────────────────┘  │
 * └─────────────────────────────────────┘
 */
<template>
  <el-container class="app-layout">
    <!-- 左侧导航区域 -->
    <el-aside :width="sidebarWidth + 'px'" class="app-aside">
      <SideMenu />
    </el-aside>

    <!-- 右侧内容区域 -->
    <el-container class="app-main">
      <!-- 顶部工具栏 -->
      <el-header class="app-header">
        <div class="header-left">
          <!-- 侧边栏折叠按钮 -->
          <el-button
            :icon="sidebarCollapsed ? 'Expand' : 'Fold'"
            text
            @click="toggleSidebar"
            class="collapse-btn"
          />
          <!-- 面包屑导航 -->
          <el-breadcrumb separator="/">
            <el-breadcrumb-item :to="{ path: '/' }">首页</el-breadcrumb-item>
            <el-breadcrumb-item v-for="item in breadcrumbs" :key="item.path">
              {{ item.name }}
            </el-breadcrumb-item>
          </el-breadcrumb>
        </div>
        <div class="header-right">
          <el-dropdown @command="handleUserCommand">
            <span class="user-info">
              <el-icon><UserIcon /></el-icon>
              <span class="user-name">{{ authDisplayName }}</span>
              <el-tag size="small" :type="roleTagType">{{ roleLabel }}</el-tag>
              <el-icon class="el-icon--right"><ArrowDown /></el-icon>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="password">修改密码</el-dropdown-item>
                <el-dropdown-item divided command="logout">退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>

      <!-- 主内容区域 -->
      <el-main class="app-content">
        <ContentArea />
      </el-main>
    </el-container>

    <!-- 全局加载遮罩 -->
    <div v-if="globalLoading" class="global-loading">
      <el-icon class="loading-icon"><Loading /></el-icon>
      <span class="loading-text">{{ loadingText }}</span>
    </div>

    <!-- 修改密码对话框 -->
    <el-dialog v-model="passwordDialogVisible" title="修改密码" width="400px" :close-on-click-modal="false">
      <el-form label-width="80px">
        <el-form-item label="旧密码">
          <el-input v-model="passwordForm.oldPassword" type="password" show-password placeholder="请输入旧密码" />
        </el-form-item>
        <el-form-item label="新密码">
          <el-input v-model="passwordForm.newPassword" type="password" show-password placeholder="请输入新密码（至少6位）" />
        </el-form-item>
        <el-form-item label="确认密码">
          <el-input v-model="passwordForm.confirmPassword" type="password" show-password placeholder="请再次输入新密码" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="passwordDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="passwordLoading" @click="handleChangePassword">确定</el-button>
      </template>
    </el-dialog>
  </el-container>
</template>

<script setup lang="ts">
/**
 * AppLayout 组件脚本
 *
 * 主要逻辑：
 * 1. 应用初始化（加载菜单和页面配置）
 * 2. 侧边栏状态管理
 * 3. 面包屑导航生成
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Loading, ArrowDown, User as UserIcon } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useAppStore, useMenuStore, useAuthStore, useTabStore } from '@/stores'
import { ROLE_LABELS } from '@/types'
import { changePassword } from '@/api/auth'
import SideMenu from './SideMenu.vue'
import ContentArea from './ContentArea.vue'

// ==================== Store ====================

const appStore = useAppStore()
const menuStore = useMenuStore()
const authStore = useAuthStore()
const tabStore = useTabStore()
const route = useRoute()
const router = useRouter()

// ==================== 计算属性 ====================

/**
 * 侧边栏是否折叠
 */
const sidebarCollapsed = computed(() => appStore.sidebarCollapsed)

/**
 * 侧边栏宽度
 */
const sidebarWidth = computed(() => appStore.sidebarWidth)

/**
 * 全局加载状态
 */
const globalLoading = computed(() => appStore.globalLoading)

/**
 * 加载提示文本
 */
const loadingText = computed(() => appStore.loadingText)

/**
 * 面包屑导航数据
 *
 * 根据当前路由和菜单配置生成面包屑路径
 */
const breadcrumbs = computed(() => {
  const currentMenu = menuStore.getMenuByPath(route.path)
  if (!currentMenu) return []

  const result: Array<{ name: string; path: string }> = []
  let menu = currentMenu

  // 向上遍历获取完整路径
  while (menu) {
    result.unshift({ name: menu.name, path: menu.path || '' })
    if (menu.parentId) {
      menu = menuStore.getMenuById(menu.parentId) as any
    } else {
      break
    }
  }

  return result
})

// ==================== 方法 ====================

/**
 * 认证相关显示名称
 */
const authDisplayName = computed(() => authStore.displayName)

/**
 * 角色标签
 */
const roleLabel = computed(() => {
  const role = authStore.userRole
  return role ? ROLE_LABELS[role] : ''
})

/**
 * 角色标签类型
 */
const roleTagType = computed(() => {
  switch (authStore.userRole) {
    case 'admin': return 'danger'
    case 'developer': return ''
    case 'guest': return 'info'
    default: return 'info'
  }
})

/**
 * 修改密码对话框
 */
const passwordDialogVisible = ref(false)
const passwordForm = ref({ oldPassword: '', newPassword: '', confirmPassword: '' })
const passwordLoading = ref(false)

/**
 * 处理用户下拉命令
 */
function handleUserCommand(command: string): void {
  switch (command) {
    case 'logout':
      authStore.logout()
      router.push('/login')
      break
    case 'password':
      passwordForm.value = { oldPassword: '', newPassword: '', confirmPassword: '' }
      passwordDialogVisible.value = true
      break
  }
}

/**
 * 处理修改密码
 */
async function handleChangePassword(): Promise<void> {
  const { oldPassword, newPassword, confirmPassword } = passwordForm.value
  if (!oldPassword || !newPassword) {
    ElMessage.warning('请填写密码')
    return
  }
  if (newPassword.length < 6) {
    ElMessage.warning('新密码不能少于6个字符')
    return
  }
  if (newPassword !== confirmPassword) {
    ElMessage.warning('两次输入的密码不一致')
    return
  }
  passwordLoading.value = true
  try {
    await changePassword({ oldPassword, newPassword })
    ElMessage.success('密码修改成功')
    passwordDialogVisible.value = false
  } catch {
    // Error shown by interceptor
  } finally {
    passwordLoading.value = false
  }
}

/**
 * 切换侧边栏折叠状态
 */
function toggleSidebar(): void {
  appStore.toggleSidebar()
}

// ==================== 生命周期 ====================

/**
 * 组件挂载时初始化应用
 */
onMounted(async () => {
  await appStore.initializeApp()

  // 注册路由后置钩子，自动添加标签页
  router.afterEach((to) => {
    // 排除登录页等公开页面
    if (to.meta.public || to.path === '/login') return

    // 获取页面名称：优先从菜单获取，其次从 route.meta.title
    const menu = menuStore.getMenuByPath(to.path)
    const name = menu?.name || (to.meta.title as string) || '未命名页面'

    tabStore.addTab({
      path: to.path,
      name,
      icon: menu?.icon,
      closable: to.path !== '/home'
    })
  })

  // 首次加载时，为当前路由添加标签
  if (route.path !== '/login') {
    const menu = menuStore.getMenuByPath(route.path)
    const name = menu?.name || (route.meta.title as string) || '未命名页面'
    tabStore.addTab({
      path: route.path,
      name,
      icon: menu?.icon,
      closable: route.path !== '/home'
    })
  }
})
</script>

<style scoped lang="scss">
.app-layout {
  height: 100vh;
  width: 100vw;
  overflow: hidden;
}

.app-aside {
  background-color: #304156;
  transition: width 0.3s ease;
  overflow: hidden;
}

.app-main {
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 60px;
  padding: 0 20px;
  background-color: #fff;
  border-bottom: 1px solid #e4e7ed;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.08);

  .header-left {
    display: flex;
    align-items: center;
    gap: 16px;
  }

  .collapse-btn {
    font-size: 20px;
    color: #606266;

    &:hover {
      color: #409eff;
    }
  }

  .header-right {
    .user-info {
      display: flex;
      align-items: center;
      gap: 8px;
      cursor: pointer;
      color: #606266;

      &:hover {
        color: #409eff;
      }

      .user-name {
        font-size: 14px;
      }
    }
  }
}

.app-content {
  flex: 1;
  padding: 20px;
  background-color: #f5f7fa;
  overflow: auto;
}

/* 全局加载遮罩 */
.global-loading {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background-color: rgba(255, 255, 255, 0.9);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  z-index: 9999;

  .loading-icon {
    font-size: 48px;
    color: #409eff;
    animation: rotate 1.5s linear infinite;
  }

  .loading-text {
    margin-top: 16px;
    font-size: 16px;
    color: #606266;
  }
}

@keyframes rotate {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}
</style>
