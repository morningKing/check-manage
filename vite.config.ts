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
    // API 代理配置，将 /api 请求转发到 Flask server
    proxy: {
      '/api': {
        target: 'http://localhost:3002',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, '')
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
