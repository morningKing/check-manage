/**
 * Axios 请求封装
 *
 * 封装 axios 实例，提供统一的请求配置和拦截器处理
 * 包括：
 * - 基础URL配置
 * - 请求/响应拦截器
 * - 统一错误处理
 * - 请求超时设置
 */

import axios, { type AxiosInstance, type AxiosRequestConfig, type AxiosResponse } from 'axios'
import { ElMessage } from 'element-plus'
import { getBatchHeaders } from './batch'

// Per-request opt-out: callers that swallow errors themselves can set `silent`
// so the global toast/401-redirect doesn't fire (e.g. aux loaders polled in the
// background — a transient backend blip would otherwise toast-storm or boot
// the user to /login on a single mistimed 401).
declare module 'axios' {
  export interface AxiosRequestConfig { silent?: boolean }
}

/**
 * 创建 axios 实例
 *
 * 配置基础URL和超时时间
 */
const service: AxiosInstance = axios.create({
  // 基础URL，开发环境通过 vite proxy 转发到 json-server
  baseURL: '/api',
  // 请求超时时间：30秒
  timeout: 30000,
  // 请求头配置
  headers: {
    'Content-Type': 'application/json'
  }
})

/**
 * 请求拦截器
 *
 * 在请求发送前进行处理：
 * - 添加认证token（如有）
 * - 处理请求参数
 */
service.interceptors.request.use(
  (config) => {
    // Inject auth token from localStorage (avoid store import for circular dependency)
    const raw = localStorage.getItem('check-manage:token')
    if (raw) {
      try {
        const parsed = JSON.parse(raw)
        if (parsed) {
          config.headers.Authorization = `Bearer ${parsed}`
        }
      } catch {
        config.headers.Authorization = `Bearer ${raw}`
      }
    }
    // Inject batch context headers if active
    const batchHeaders = getBatchHeaders()
    Object.assign(config.headers, batchHeaders)
    return config
  },
  (error) => {
    console.error('请求错误:', error)
    return Promise.reject(error)
  }
)

/**
 * 响应拦截器
 *
 * 在响应返回后进行处理：
 * - 统一处理响应数据
 * - 处理错误状态码
 */
service.interceptors.response.use(
  (response: AxiosResponse) => {
    // blob 请求返回完整 response 以便获取 headers
    if (response.config.responseType === 'blob') {
      return response
    }
    // json-server 直接返回数据，不包装
    return response.data
  },
  (error) => {
    // Callers can opt out of the global toast/redirect with `{ silent: true }`
    // — meant for auxiliary loaders whose failure the caller swallows on
    // purpose (otherwise a transient backend blip fires a toast storm and a
    // single mistimed 401 wipes the user out to /login).
    const silent = (error.config as AxiosRequestConfig | undefined)?.silent === true
    const message = error.response?.data?.error || error.response?.data?.message || error.message || '请求失败'

    if (!silent) {
      if (error.response) {
        switch (error.response.status) {
          case 400:
            ElMessage.error('请求参数错误')
            break
          case 401:
            ElMessage.error('未授权，请重新登录')
            // Clear auth data and redirect to login
            localStorage.removeItem('check-manage:token')
            localStorage.removeItem('check-manage:userInfo')
            window.location.href = '/login'
            break
          case 403:
            ElMessage.error('拒绝访问')
            break
          case 404:
            ElMessage.error('请求资源不存在')
            break
          case 409:
            // VERSION_CONFLICT is handled by the caller, skip duplicate message
            if (error.response?.data?.code !== 'VERSION_CONFLICT') {
              ElMessage.error(message)
            }
            break
          case 500:
            ElMessage.error('服务器内部错误')
            break
          default:
            ElMessage.error(message)
        }
      } else {
        ElMessage.error('网络连接失败，请检查网络')
      }
    }

    return Promise.reject(error)
  }
)

/**
 * GET 请求
 *
 * @param url - 请求地址
 * @param params - 查询参数
 * @param config - 额外配置
 * @returns Promise<T>
 */
export function get<T = any>(url: string, params?: any, config?: AxiosRequestConfig): Promise<T> {
  return service.get(url, { params, ...config })
}

/**
 * POST 请求
 *
 * @param url - 请求地址
 * @param data - 请求体数据
 * @param config - 额外配置
 * @returns Promise<T>
 */
export function post<T = any>(url: string, data?: any, config?: AxiosRequestConfig): Promise<T> {
  return service.post(url, data, config)
}

/**
 * PUT 请求
 *
 * @param url - 请求地址
 * @param data - 请求体数据
 * @param config - 额外配置
 * @returns Promise<T>
 */
export function put<T = any>(url: string, data?: any, config?: AxiosRequestConfig): Promise<T> {
  return service.put(url, data, config)
}

/**
 * PATCH 请求
 *
 * @param url - 请求地址
 * @param data - 请求体数据
 * @param config - 额外配置
 * @returns Promise<T>
 */
export function patch<T = any>(url: string, data?: any, config?: AxiosRequestConfig): Promise<T> {
  return service.patch(url, data, config)
}

/**
 * DELETE 请求
 *
 * @param url - 请求地址
 * @param config - 额外配置
 * @returns Promise<T>
 */
export function del<T = any>(url: string, config?: AxiosRequestConfig): Promise<T> {
  return service.delete(url, config)
}

// 导出 axios 实例供特殊场景使用
export default service
