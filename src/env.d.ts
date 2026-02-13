/// <reference types="vite/client" />

/**
 * TypeScript 环境声明文件
 *
 * 用于声明 Vite 特有的类型和模块
 */

// 声明 .vue 文件的模块类型
declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<{}, {}, any>
  export default component
}

// 声明环境变量类型
interface ImportMetaEnv {
  readonly VITE_API_BASE_URL: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
