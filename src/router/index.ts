/**
 * 路由配置主文件
 *
 * 职责：
 * - 定义应用的静态路由结构
 * - 根据菜单配置动态注册数据页路由
 * - 配置路由守卫（认证 + 权限）
 *
 * 路由结构：
 * /login               - 登录页（公开）
 * /                    - 主布局（需认证）
 * ├── /home           - 首页
 * ├── /admin/<功能>    - 设置中心（21 个功能各自独立路由，由目录数据生成）
 * ├── /admin          - 重定向到首个有权限的设置功能
 * ├── /admin/<分类|老路径> - 兼容重定向
 * └── /...            - 动态数据页面（根据菜单配置自动注册）
 */

import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { ElMessage } from 'element-plus'
import { generateRoutesFromMenus, addDynamicRoutes } from './dynamicRoutes'
import { useAuthStore } from '@/stores/auth'
import { buildSettingsRoutes, buildSettingsRedirects } from './settingsRoutes'

/**
 * 静态路由配置
 *
 * 仅包含固定页面，数据页路由在应用初始化后根据菜单配置动态注册
 */
const staticRoutes: RouteRecordRaw[] = [
  // 登录页（公开）
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/login/LoginView.vue'),
    meta: {
      title: '登录',
      public: true,
    },
  },
  // 访客客服页（公开，无需登录）
  {
    path: '/kefu/:slug',
    name: 'KefuChat',
    component: () => import('@/views/kefu/KefuChatPage.vue'),
    props: true,
    meta: { title: '在线客服', public: true },
  },
  {
    path: '/',
    name: 'Layout',
    component: () => import('@/components/layout/AppLayout.vue'),
    redirect: '/home',
    children: [
      // 首页
      {
        path: 'home',
        name: 'Home',
        component: () => import('@/views/home/HomeView.vue'),
        meta: {
          title: '首页',
          icon: 'House',
        },
      },
      // AI 助手（全屏对话页）
      {
        path: 'ai-chat',
        name: 'AiChat',
        component: () => import('@/views/ai-chat/AiChatView.vue'),
        meta: {
          title: 'AI 助手',
          icon: 'ChatDotRound',
        },
      },
      // 我的待办（工作流收件箱）
      { path: 'workflow/inbox', name: 'WorkflowInbox', component: () => import('@/views/workflow/WorkflowInbox.vue'), meta: { title: '我的待办' } },
      // 工作流全屏编辑页（新建 / 按 id 编辑）
      { path: 'workflow/new', name: 'WorkflowEditorNew', component: () => import('@/views/workflow/WorkflowEditor.vue'), meta: { title: '新建工作流' } },
      { path: 'workflow/:id', name: 'WorkflowEditor', component: () => import('@/views/workflow/WorkflowEditor.vue'), meta: { title: '编辑工作流' } },
      // 通用动态数据页面（通过 pageId 参数匹配）
      {
        path: 'page/:pageId',
        name: 'DynamicPage',
        component: () => import('@/views/dynamic/DynamicPage.vue'),
        meta: {
          title: '数据页面',
        },
      },
      // 设置中心：21 条功能路由（由目录数据生成） + 兼容重定向
      ...buildSettingsRoutes(),
      ...buildSettingsRedirects(() => {
        const auth = useAuthStore()
        return auth.can
      }),
      {
        path: 'dashboard/:id?',
        name: 'Dashboard',
        component: () => import('@/views/dashboard/DashboardView.vue'),
        meta: {
          title: '仪表盘',
          icon: 'DataLine',
        },
      },
    ],
  },
  // 404 页面
  {
    path: '/:pathMatch(.*)*',
    name: 'NotFound',
    component: () => import('@/views/error/NotFound.vue'),
  },
]

/**
 * 创建路由实例
 */
const router = createRouter({
  history: createWebHistory(),
  routes: staticRoutes,
  // 滚动行为配置
  scrollBehavior(_to, _from, savedPosition) {
    if (savedPosition) {
      return savedPosition
    } else {
      return { top: 0 }
    }
  },
})

/**
 * 动态路由是否已注册
 */
let dynamicRoutesReady = false

/**
 * 全局前置守卫
 *
 * 用于：
 * - 认证检查（未登录跳转登录页）
 * - 确保应用初始化完成（菜单和页面配置加载）
 * - 首次导航时注册动态路由
 * - 权限检查（基于角色的路由访问控制）
 * - 设置页面标题
 */
router.beforeEach(async (to) => {
  // 1. 公开页面直接放行
  if (to.meta.public) {
    // 已登录用户访问登录页时跳转首页
    const { useAuthStore } = await import('@/stores/auth')
    const authStore = useAuthStore()
    if (authStore.isLoggedIn && to.path === '/login') {
      return '/home'
    }
    return
  }

  // 2. 检查登录状态
  const { useAuthStore } = await import('@/stores/auth')
  const authStore = useAuthStore()

  if (!authStore.isLoggedIn) {
    return '/login'
  }

  // 3. 确保应用初始化
  const { useAppStore } = await import('@/stores/app')
  const appStore = useAppStore()

  if (!appStore.initialized) {
    // 应用加载时刷新当前用户，确保拿到最新的权限集合（permissions）。
    // 这能让旧会话/权限变更在刷新后即时生效，并避免依赖本地缓存里过期的权限。
    await authStore.fetchCurrentUser()
    if (!authStore.isLoggedIn) {
      return '/login'
    }
    await appStore.initializeApp()
  }

  // 4. 首次初始化后注册动态路由
  if (!dynamicRoutesReady) {
    const { useMenuStore } = await import('@/stores/menu')
    const menuStore = useMenuStore()
    const routes = generateRoutesFromMenus(menuStore.menuTree)
    addDynamicRoutes(router, routes)
    dynamicRoutesReady = true
    // 重新导航，让新注册的路由生效
    return to.fullPath
  }

  // 5. 权限检查
  if (!authStore.hasRoutePermission(to.path)) {
    ElMessage.warning('您没有权限访问该页面')
    return '/home'
  }

  // 5.5 记住最后一个业务路由，供设置中心的「返回工作区」使用
  if (to.meta.shell !== 'settings' && to.path !== '/login') {
    appStore.setLastBusinessPath(to.fullPath)
  }

  // 6. 设置页面标题
  const title = to.meta.title as string
  document.title = title ? `${title} - 巡检用例管理系统` : '巡检用例管理系统'
})

/**
 * 全局后置钩子
 */
router.afterEach(() => {
  // 可以在这里关闭加载进度条
})

export default router
