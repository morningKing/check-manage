/**
 * 应用入口文件
 *
 * 职责：
 * - 创建 Vue 应用实例
 * - 注册全局插件（Pinia、Router、Element Plus）
 * - 注册 Element Plus 图标
 * - 引入全局样式
 * - 挂载应用到 DOM
 */
import { createApp } from 'vue'
import { createPinia } from 'pinia'

// Element Plus 组件库
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
// @ts-ignore - Element Plus locale module lacks type declaration
import zhCn from 'element-plus/dist/locale/zh-cn.mjs'

// Element Plus 图标
import * as ElementPlusIconsVue from '@element-plus/icons-vue'

// 路由配置
import router from './router'

// 全局样式
import './assets/styles/global.scss'
import './assets/styles/theme.scss'   // 中性灰阶主题，覆盖 EP 变量（须在 EP css 之后）

// 根组件
import App from './App.vue'

// 创建 Vue 应用实例
const app = createApp(App)

// 创建 Pinia 状态管理实例
const pinia = createPinia()

// 注册 Pinia
app.use(pinia)

// 注册路由
app.use(router)

// 注册 Element Plus，配置中文语言
app.use(ElementPlus, {
  locale: zhCn
})

// 全局注册 Element Plus 图标组件
// 图标使用方式：<el-icon><IconName /></el-icon>
for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(key, component)
}

// 挂载应用到 DOM
app.mount('#app')
