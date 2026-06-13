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
 * ├── /admin/menu     - 菜单管理（仅管理员）
 * ├── /admin/page-config - 页面配置管理（仅管理员）
 * ├── /admin/users    - 用户管理（仅管理员）
 * └── /...            - 动态数据页面（根据菜单配置自动注册）
 */

import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { ElMessage } from 'element-plus'
import { generateRoutesFromMenus, addDynamicRoutes } from './dynamicRoutes'
import { useAuthStore } from '@/stores/auth'
import { firstAccessibleCategoryPath } from '@/views/admin/hub/settingsCatalog'

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
      // 通用动态数据页面（通过 pageId 参数匹配）
      {
        path: 'page/:pageId',
        name: 'DynamicPage',
        component: () => import('@/views/dynamic/DynamicPage.vue'),
        meta: {
          title: '数据页面',
        },
      },
      // 设置中心（管理控制台）：左栏分类 + 右侧 tab 容器
      {
        path: 'admin',
        component: () => import('@/views/admin/hub/SettingsHub.vue'),
        redirect: () => {
          const auth = useAuthStore()
          return firstAccessibleCategoryPath(auth.can)
        },
        children: [
          { path: 'access', name: 'SettingsAccess', component: () => import('@/views/admin/hub/CategoryView.vue'), meta: { title: '访问控制', categoryId: 'access' } },
          { path: 'structure', name: 'SettingsStructure', component: () => import('@/views/admin/hub/CategoryView.vue'), meta: { title: '结构配置', categoryId: 'structure' } },
          { path: 'integration', name: 'SettingsIntegration', component: () => import('@/views/admin/hub/CategoryView.vue'), meta: { title: '集成对接', categoryId: 'integration' } },
          { path: 'ai', name: 'SettingsAi', component: () => import('@/views/admin/hub/CategoryView.vue'), meta: { title: 'AI 能力', categoryId: 'ai' } },
          { path: 'data-ops', name: 'SettingsDataOps', component: () => import('@/views/admin/hub/CategoryView.vue'), meta: { title: '数据运维', categoryId: 'data-ops' } },
          { path: 'sys-ops', name: 'SettingsSysOps', component: () => import('@/views/admin/hub/CategoryView.vue'), meta: { title: '系统运维', categoryId: 'sys-ops' } },
          { path: 'general', name: 'SettingsGeneral', component: () => import('@/views/admin/hub/CategoryView.vue'), meta: { title: '通用设置', categoryId: 'general' } },
        ],
      },
      // 旧管理路径 → 设置中心（保深链/书签）
      { path: 'admin/users', redirect: '/admin/access?tab=users' },
      { path: 'admin/roles', redirect: '/admin/access?tab=roles' },
      { path: 'admin/menu', redirect: '/admin/structure?tab=menu' },
      { path: 'admin/page-config', redirect: '/admin/structure?tab=page-config' },
      { path: 'admin/api-keys', redirect: '/admin/integration?tab=api-keys' },
      { path: 'admin/webhook-settings', redirect: '/admin/integration?tab=webhook' },
      { path: 'admin/ai-settings', redirect: '/admin/ai?tab=ai-settings' },
      { path: 'admin/ai-scan-tasks', redirect: '/admin/ai?tab=ai-scan' },
      { path: 'admin/query', redirect: '/admin/data-ops?tab=query' },
      { path: 'admin/menu-export', redirect: '/admin/data-ops?tab=data-export' },
      { path: 'admin/etl-tasks', redirect: '/admin/data-ops?tab=etl' },
      { path: 'admin/export-scripts', redirect: '/admin/data-ops?tab=export-scripts' },
      { path: 'admin/validation-scripts', redirect: '/admin/data-ops?tab=validation-scripts' },
      { path: 'admin/operation-log', redirect: '/admin/sys-ops?tab=operation-log' },
      { path: 'admin/backup', redirect: '/admin/sys-ops?tab=backup' },
      { path: 'admin/system-settings', redirect: '/admin/general' },
      {
        path: 'admin/trigger-rules',
        name: 'TriggerRuleManager',
        component: () => import('@/views/admin/TriggerRuleManager.vue'),
        meta: {
          title: '联动规则',
          icon: 'Connection',
        },
      },
      {
        path: 'admin/dependency-manager',
        name: 'DependencyManager',
        component: () => import('@/views/admin/DependencyManager.vue'),
        meta: {
          title: '依赖管理',
          icon: 'Share',
        },
      },
      // 隐藏页面：恢复出厂设置（不添加菜单项）
      {
        path: 'admin/factory-reset',
        name: 'FactoryReset',
        component: () => import('@/views/admin/FactoryReset.vue'),
        meta: {
          title: '恢复出厂设置',
          hidden: true, // 隐藏路由，不在菜单中显示
        },
      },
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
