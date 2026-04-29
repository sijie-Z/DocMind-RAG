/// <reference types="vite/client" />
import axios, { type AxiosInstance, type AxiosResponse } from 'axios'
import { useUserStore } from '@/stores/user'
import { useAppStore } from '@/stores/app'
import { createDiscreteApi } from 'naive-ui'
import { getToken, getRefreshToken, setToken, setRefreshToken } from '@/utils/auth'

// 创建独立的 UI API，用于在非组件环境下显示消息
const { message } = createDiscreteApi(['message'])

// 全局 toast 去重：同一内容 1.5s 内不重复弹出
const _activeToasts = new Map<string, { timer: ReturnType<typeof setTimeout> }>()
const DEDUP_MS = 1500

function dedupMessage(type: 'success' | 'error' | 'warning' | 'info', content: string, duration = 5000) {
  const key = `${type}:${content}`
  if (_activeToasts.has(key)) return
  const msgInst = message[type](content, { duration })
  _activeToasts.set(key, { timer: setTimeout(() => _activeToasts.delete(key), DEDUP_MS) })
  return msgInst
}

let router: any = null
let activeRequestCount = 0

const REQUEST_ID_HEADER = 'X-Request-ID'
const SILENT_ERROR_HEADER = 'X-Silent-Error'
const REQUEST_START_TIME_KEY = '__request_start_time'

function emitApiMetricEvent(payload: Record<string, any>) {
  try {
    window.dispatchEvent(new CustomEvent('app:api-metric', { detail: payload }))
  } catch {
    // 非浏览器或事件系统不可用时静默
  }
}

function showLoading() {
  if (activeRequestCount === 0) {
    const appStore = useAppStore()
    appStore.setLoading(true)
  }
  activeRequestCount++
}

function hideLoading() {
  if (activeRequestCount <= 0) return
  activeRequestCount--
  if (activeRequestCount === 0) {
    const appStore = useAppStore()
    appStore.setLoading(false)
  }
}

function generateRequestId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `req_${Date.now()}_${Math.random().toString(16).slice(2, 10)}`
}

function readHeaderValue(headers: any, key: string): string | undefined {
  if (!headers) return undefined
  if (typeof headers.get === 'function') {
    const v = headers.get(key) ?? headers.get(key.toLowerCase())
    return v == null ? undefined : String(v)
  }
  const lower = key.toLowerCase()
  const direct = headers[key] ?? headers[lower]
  if (direct == null) return undefined
  return String(direct)
}

function setHeaderValue(headers: any, key: string, value: string) {
  if (typeof headers?.set === 'function') {
    headers.set(key, value)
    return
  }
  headers[key] = value
}

function shouldSilentError(headers: any): boolean {
  const value = readHeaderValue(headers, SILENT_ERROR_HEADER)
  if (value == null) return false
  const normalized = value.trim().toLowerCase()
  return normalized !== '' && normalized !== '0' && normalized !== 'false' && normalized !== 'no'
}

function getErrorRequestId(error: any): string | undefined {
  return (
    readHeaderValue(error?.response?.headers, REQUEST_ID_HEADER) ||
    (error?.response?.data?.request_id ? String(error.response.data.request_id) : undefined) ||
    readHeaderValue(error?.config?.headers, REQUEST_ID_HEADER)
  )
}

export function setRouter(routerInstance: any) {
  router = routerInstance
}

// 创建axios实例
const request: AxiosInstance = axios.create({
  // 统一基础路径为 /api/v1，避免后续手动拼接
  baseURL: (import.meta as any).env.VITE_API_BASE_URL || '/api/v1',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json'
  }
})

// 请求拦截器
request.interceptors.request.use(
  (config) => {
    const headers: any = config.headers ?? {}
    config.headers = headers
    ;(config as any)[REQUEST_START_TIME_KEY] = Date.now()
    const isAuthRequest = /\/auth\/(login|register|refresh)/.test(config.url ?? '')
    if (!isAuthRequest) showLoading()
    const rawToken = localStorage.getItem('docmind_token') || localStorage.getItem('paicongming_token') || getToken()
    if (rawToken) {
      const cleanToken = rawToken.trim().replace(/^["'](.*)["']$/, '$1')
      setHeaderValue(headers, 'Authorization', `Bearer ${cleanToken}`)
    }
    const requestId = readHeaderValue(headers, REQUEST_ID_HEADER) || generateRequestId()
    setHeaderValue(headers, REQUEST_ID_HEADER, requestId)
    return config
  },
  (error) => {
    hideLoading()
    return Promise.reject(error)
  }
)

let isRefreshing = false
type PendingRequest = {
  resolve: (value: any) => void
  reject: (reason?: any) => void
  originalRequest: any
}
let requestsQueue: PendingRequest[] = []

function processQueueWithToken(newAccessToken: string) {
  const queue = [...requestsQueue]
  requestsQueue = []
  queue.forEach(({ resolve, reject, originalRequest }) => {
    try {
      if (!originalRequest.headers) originalRequest.headers = {}
      setHeaderValue(originalRequest.headers, 'Authorization', `Bearer ${newAccessToken}`)
      resolve(request(originalRequest))
    } catch (err) {
      reject(err)
    }
  })
}

function rejectQueue(error: any) {
  const queue = [...requestsQueue]
  requestsQueue = []
  queue.forEach(({ reject }) => reject(error))
}

async function doRefreshAccessToken(): Promise<string> {
  const refreshToken = getRefreshToken()
  if (!refreshToken) {
    throw new Error('refresh_token_missing')
  }
  const refreshResp = await axios.post(
    `${request.defaults.baseURL}/auth/refresh`,
    { refresh_token: refreshToken },
    {
      headers: {
        [SILENT_ERROR_HEADER]: '1',
        [REQUEST_ID_HEADER]: generateRequestId()
      },
      timeout: 10000
    }
  )
  const payload = (refreshResp.data as any)?.data ?? refreshResp.data
  const accessToken = payload?.access_token
  if (!accessToken) {
    throw new Error('refresh_access_token_missing')
  }
  setToken(accessToken, payload?.expires_in ?? 86400)
  if (payload?.refresh_token) {
    setRefreshToken(payload.refresh_token)
  }
  return accessToken
}

// 响应拦截器
request.interceptors.response.use(
  (response: AxiosResponse) => {
    const isAuthRequest = /\/auth\/(login|register|refresh)/.test(response.config?.url ?? '')
    if (!isAuthRequest) hideLoading()
    const startedAt = (response.config as any)?.[REQUEST_START_TIME_KEY]
    if (typeof startedAt === 'number') {
      emitApiMetricEvent({
        kind: 'success',
        method: response.config?.method ?? 'get',
        url: response.config?.url ?? '',
        status: response.status,
        durationMs: Date.now() - startedAt
      })
    }
    return response
  },
  async (error) => {
    const isAuthRequest = error.config?.url && /\/auth\/(login|register|refresh)/.test(error.config.url)
    if (!isAuthRequest) hideLoading()
    const silentError = shouldSilentError(error.config?.headers)
    const requestId = getErrorRequestId(error)

    if (!silentError) {
      console.error(`[Axios Error] 状态码: ${error.response?.status}${requestId ? ` | request_id=${requestId}` : ''}`, error.response?.data)
    }
    if (error.response) {
      const { status, data } = error.response
      const originalRequest = error.config
      const startedAt = (originalRequest as any)?.[REQUEST_START_TIME_KEY]
      
      let errorMsg = '请求失败'
      if (data && (data.detail !== undefined || data.message !== undefined)) {
        const raw = data.detail ?? data.message
        if (Array.isArray(raw)) {
          errorMsg = raw.map((e: any) => e.msg ?? e).join('; ')
        } else {
          errorMsg = String(raw)
        }
      }

      switch (status) {
        case 401: {
          const isLoginRequest = originalRequest?.url?.includes?.('auth/login')
          const isRefreshRequest = originalRequest?.url?.includes?.('auth/refresh')
          
          if (!isLoginRequest && !isRefreshRequest && !originalRequest?._retry) {
            if (!isRefreshing) {
              isRefreshing = true
              originalRequest._retry = true
              try {
                const newToken = await doRefreshAccessToken()
                request.defaults.headers.common['Authorization'] = `Bearer ${newToken}`
                const userStore = useUserStore()
                userStore.token = newToken
                if (!originalRequest.headers) originalRequest.headers = {}
                setHeaderValue(originalRequest.headers, 'Authorization', `Bearer ${newToken}`)
                processQueueWithToken(newToken)
                return request(originalRequest)
              } catch (refreshErr) {
                rejectQueue(refreshErr)
                const userStore = useUserStore()
                userStore.logout()
                if (router) router.push({ name: 'Login' })
                return Promise.reject(refreshErr)
              } finally {
                isRefreshing = false
              }
            } else {
              // 正在刷新时，将后续请求挂起并加入队列
              return new Promise((resolve, reject) => {
                requestsQueue.push({ resolve, reject, originalRequest })
              })
            }
          }
          
          if (!isLoginRequest && errorMsg === '请求失败') {
            errorMsg = '登录已过期或未登录，请重新登录'
            try {
              const userStore = useUserStore()
              userStore.logout()
              if (router) router.push({ name: 'Login' })
            } catch (_) { /* store/router 可能未就绪 */ }
          } else if (isLoginRequest && errorMsg === '请求失败') {
            errorMsg = '身份验证失败，请检查用户名和密码'
          }
          break
        }
        case 403:
          errorMsg = '无权限访问'
          break
        case 500:
          errorMsg = errorMsg === '请求失败' ? `服务器内部错误: ${data?.detail ?? data?.message ?? '未知'}` : `服务器内部错误: ${errorMsg}`
          console.error('[500 Error Details]:', data)
          break
      }
      
      if (requestId) {
        errorMsg = `${errorMsg}（请求ID: ${requestId}）`
      }

      if (!silentError) {
        dedupMessage('error', errorMsg, 5000)
      }
      emitApiMetricEvent({
        kind: 'error',
        category: 'http',
        method: originalRequest?.method ?? 'get',
        url: originalRequest?.url ?? '',
        status,
        requestId,
        durationMs: typeof startedAt === 'number' ? Date.now() - startedAt : undefined
      })
    } else {
      if (!silentError) {
        dedupMessage('error', '网络连接失败，请检查后端服务', 5000)
      }
      emitApiMetricEvent({
        kind: 'error',
        category: 'network',
        method: error.config?.method ?? 'get',
        url: error.config?.url ?? '',
        requestId
      })
    }
    
    return Promise.reject(error)
  }
)

// 设置路由守卫
export function setupInterceptors(routerInstance: any) {
  setRouter(routerInstance)
  
  routerInstance.beforeEach(async (to: any, _from: any, next: any) => {
    const userStore = useUserStore()
    
    // 检查是否需要登录
    if (to.meta.requiresAuth) {
      // 1. 如果没有 token，直接去登录
      if (!userStore.token) {
        next({ name: 'Login' })
        return
      }
      
      // 2. 如果有 token 但没有用户信息，尝试获取用户信息
      if (!userStore.userInfo) {
        try {
          await userStore.getUserInfo()
          // 获取成功，继续执行后续权限检查
        } catch (error) {
          console.error('获取用户信息失败:', error)
          userStore.logout()
          next({ name: 'Login' })
          return
        }
      }
    }
    
    // 检查是否需要管理员权限
    if (to.meta.requiresAdmin && !userStore.isAdmin) {
      next({ name: 'Chat' })
      return
    }
    
    next()
  })
}

export default request
