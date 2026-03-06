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
import { generateRoutesFromMenus, addDynamicRoutes, resetRouter } from './dynamicRoutes'

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
      // 通用动态数据页面（通过 pageId 参数匹配）
      {
        path: 'page/:pageId',
        name: 'DynamicPage',
        component: () => import('@/views/dynamic/DynamicPage.vue'),
        meta: {
          title: '数据页面',
        },
      },
      // 系统配置路由
      {
        path: 'admin/menu',
        name: 'MenuManager',
        component: () => import('@/views/admin/MenuManager.vue'),
        meta: {
          title: '菜单管理',
          icon: 'Menu',
        },
      },
      {
        path: 'admin/page-config',
        name: 'PageConfigManager',
        component: () => import('@/views/admin/PageConfigManager.vue'),
        meta: {
          title: '页面配置',
          icon: 'Files',
        },
      },
      {
        path: 'admin/users',
        name: 'UserManager',
        component: () => import('@/views/admin/UserManager.vue'),
        meta: {
          title: '用户管理',
          icon: 'User',
        },
      },
      {
        path: 'admin/operation-log',
        name: 'OperationLog',
        component: () => import('@/views/admin/OperationLog.vue'),
        meta: {
          title: '操作日志',
          icon: 'Tickets',
        },
      },
      {
        path: 'admin/backup',
        name: 'BackupManager',
        component: () => import('@/views/admin/BackupManager.vue'),
        meta: {
          title: '系统备份',
          icon: 'FolderOpened',
        },
      },
      {
        path: 'admin/export-scripts',
        name: 'ExportScriptManager',
        component: () => import('@/views/admin/ExportScriptManager.vue'),
        meta: {
          title: '导出脚本',
          icon: 'Promotion',
        },
      },
      {
        path: 'admin/api-keys',
        name: 'ApiKeyManager',
        component: () => import('@/views/admin/ApiKeyManager.vue'),
        meta: {
          title: 'Open API',
          icon: 'Key',
        },
      },
      {
        path: 'admin/validation-scripts',
        name: 'ValidationScriptManager',
        component: () => import('@/views/admin/ValidationScriptManager.vue'),
        meta: {
          title: '校验脚本',
          icon: 'CircleCheck',
        },
      },
      {
        path: 'admin/etl-tasks',
        name: 'EtlTaskManager',
        component: () => import('@/views/admin/EtlTaskManager.vue'),
        meta: {
          title: 'ETL 管理',
          icon: 'Connection',
        },
      },
      {
        path: 'admin/query',
        name: 'QueryConsole',
        component: () => import('@/views/admin/QueryConsole.vue'),
        meta: {
          title: '数据查询',
          icon: 'Search',
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
  scrollBehavior(to, from, savedPosition) {
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
