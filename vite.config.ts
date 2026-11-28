/**
 * Vite 配置文件
 *
 * 配置说明：
 * - 使用 Vue 3 插件
 * - 配置路径别名 @ 指向 src 目录
 * - 配置开发服务器代理，将 /api 请求转发到 Flask Server
 */
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

export default defineConfig({
  // Vue 插件配置
  plugins: [vue()],

  // 路径别名配置
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src')
    }
  },

  // 开发服务器配置
  server: {
    port: 5173,
    watch: {
      // 运行日志与测试产物不在模块图里，但默认会被监听——每次写入都触发
      // vite full-page-reload，把开发中的浏览器页（含 E2E 页面）周期性整页
      // 刷新。2026-11-28 实锤：ai-chat.log / oc-serve.log / proxy-*.log 高频
      // 写入使 agent-action-gate E2E 的 UI 步骤在 15s 等待窗内反复重载，批组
      // 永远渲染不出来（trace 快照：整页 DOM 从完整→空白→半渲染循环）。
      // 「批列表首载偶发空」的老现象与此同源。
      ignored: [
        '**/server/*.log',
        '**/server/logs/**',
        '**/.dev-ai-workspaces/**',
        '**/test-results/**',
        '**/playwright-report/**',
        '**/.mimosa/**',
        '**/.playwright-mcp/**',
        '**/e2e/screenshots/**',
        '**/e2e/.auth/**',
      ],
    },
    // API 代理配置，将 /api 请求转发到 Flask server
    proxy: {
      '/api': {
        target: 'http://localhost:3002',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, '')
      },
      // 智能写作的 Yjs 协作 WebSocket → Hocuspocus Node 服务（suggest-editor-server）
      '/suggest-ws': {
        target: 'ws://localhost:1234',
        ws: true,
        changeOrigin: true
      }
    }
  },

  // 生产构建预览服务器（vite preview）：同样代理 /api，便于本地验证生产包
  preview: {
    port: 4173,
    proxy: {
      '/api': {
        target: 'http://localhost:3002',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, '')
      }
    }
  },

  // CSS 预处理器配置
  css: {
    preprocessorOptions: {
      scss: {
        // 全局引入 SCSS 变量文件
        additionalData: `@use "@/assets/styles/variables.scss" as *;`
      }
    }
  }
})
