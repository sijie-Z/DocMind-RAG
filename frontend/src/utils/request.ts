/// <reference types="vite/client" />
import axios, { type AxiosInstance, type AxiosResponse, type AxiosError, type InternalAxiosRequestConfig, type AxiosRequestHeaders } from 'axios'
import type { Router } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { useAppStore } from '@/stores/app'
import { createDiscreteApi } from 'naive-ui'
import { getToken, getRefreshToken, setToken, setRefreshToken } from '@/utils/auth'

// 创建独立的 UI API，用于在非组件环境下显示消息
const { message } = createDiscreteApi(['message'])

interface ApiMetricPayload {
  kind: 'success' | 'error'
  category?: string
  method: string
  url: string
  status?: number
  requestId?: string
  durationMs?: number
}

interface ExtendedAxiosConfig extends InternalAxiosRequestConfig {
  ['X-Request-ID']?: string
  ['__request_start_time']?: number
  _retry?: boolean
}

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

let router: Router | null = null
let activeRequestCount = 0

const REQUEST_ID_HEADER = 'X-Request-ID'
const SILENT_ERROR_HEADER = 'X-Silent-Error'
const REQUEST_START_TIME_KEY = '__request_start_time'

function emitApiMetricEvent(payload: ApiMetricPayload) {
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

function readHeaderValue(headers: AxiosRequestHeaders | Record<string, string>, key: string): string | undefined {
  if (!headers) return undefined
  if (typeof (headers as AxiosRequestHeaders).get === 'function') {
    const v = (headers as AxiosRequestHeaders).get(key) ?? (headers as AxiosRequestHeaders).get(key.toLowerCase())
    return v == null ? undefined : String(v)
  }
  const lower = key.toLowerCase()
  const direct = (headers as Record<string, string>)[key] ?? (headers as Record<string, string>)[lower]
  if (direct == null) return undefined
  return String(direct)
}

function setHeaderValue(headers: AxiosRequestHeaders | Record<string, string>, key: string, value: string) {
  if (typeof (headers as AxiosRequestHeaders)?.set === 'function') {
    (headers as AxiosRequestHeaders).set(key, value)
    return
  }
  (headers as Record<string, string>)[key] = value
}

function shouldSilentError(headers: AxiosRequestHeaders | Record<string, string>): boolean {
  const value = readHeaderValue(headers, SILENT_ERROR_HEADER)
  if (value == null) return false
  const normalized = value.trim().toLowerCase()
  return normalized !== '' && normalized !== '0' && normalized !== 'false' && normalized !== 'no'
}

function getErrorRequestId(error: AxiosError): string | undefined {
  const respHeaders = error?.response?.headers as AxiosRequestHeaders | undefined
  const reqHeaders = error?.config?.headers as AxiosRequestHeaders | undefined
  const data = error?.response?.data as Record<string, unknown> | undefined
  return (
    (respHeaders ? readHeaderValue(respHeaders, REQUEST_ID_HEADER) : undefined) ||
    (data?.request_id ? String(data.request_id) : undefined) ||
    (reqHeaders ? readHeaderValue(reqHeaders, REQUEST_ID_HEADER) : undefined)
  )
}

export function setRouter(routerInstance: Router) {
  router = routerInstance
}

// 创建axios实例
const request: AxiosInstance = axios.create({
  // 统一基础路径为 /api/v1，避免后续手动拼接
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api/v1',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json'
  }
})

// 请求拦截器
request.interceptors.request.use(
  (config) => {
    const headers: AxiosRequestHeaders = config.headers ?? {} as AxiosRequestHeaders
    config.headers = headers
    ;(config as ExtendedAxiosConfig)[REQUEST_START_TIME_KEY] = Date.now()
    const isAuthRequest = /\/auth\/(login|register|refresh)/.test(config.url ?? '')
    if (!isAuthRequest) showLoading()
    const rawToken = localStorage.getItem('docmind_token') || getToken()
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
  resolve: (_value: Promise<AxiosResponse>) => void
  reject: (_reason?: unknown) => void
  originalRequest: InternalAxiosRequestConfig
}
let requestsQueue: PendingRequest[] = []

function processQueueWithToken(newAccessToken: string) {
  const queue = [...requestsQueue]
  requestsQueue = []
  queue.forEach(({ resolve, reject, originalRequest }) => {
    try {
      if (!originalRequest.headers) originalRequest.headers = {} as AxiosRequestHeaders
      setHeaderValue(originalRequest.headers, 'Authorization', `Bearer ${newAccessToken}`)
      resolve(request(originalRequest))
    } catch (err) {
      reject(err)
    }
  })
}

function rejectQueue(error: unknown) {
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
  const refreshData = refreshResp.data as Record<string, unknown>
  const payload = (refreshData?.data as Record<string, unknown>) ?? refreshData
  const accessToken = payload?.access_token as string | undefined
  if (!accessToken) {
    throw new Error('refresh_access_token_missing')
  }
  setToken(accessToken, (payload?.expires_in as number) ?? 86400)
  if (payload?.refresh_token) {
    setRefreshToken(payload.refresh_token as string)
  }
  return accessToken
}

// 响应拦截器
request.interceptors.response.use(
  (response: AxiosResponse) => {
    const isAuthRequest = /\/auth\/(login|register|refresh)/.test(response.config?.url ?? '')
    if (!isAuthRequest) hideLoading()
    const startedAt = (response.config as ExtendedAxiosConfig)?.[REQUEST_START_TIME_KEY]
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
      console.warn('[request] HTTP error', {
        status: error.response?.status,
        url: error.config?.url,
        method: error.config?.method,
        responseData: error.response?.data,
      })
    }
    if (error.response) {
      const { status, data } = error.response
      const originalRequest = error.config as ExtendedAxiosConfig | undefined
      if (!originalRequest) return Promise.reject(error)
      const startedAt = originalRequest[REQUEST_START_TIME_KEY]
      
      let errorMsg = '请求失败'
      if (data && (data.detail !== undefined || data.message !== undefined)) {
        const raw = data.detail ?? data.message
        if (Array.isArray(raw)) {
          errorMsg = raw.map((e: unknown) => (e && typeof e === 'object' && 'msg' in e) ? String((e as Record<string, unknown>).msg) : String(e)).join('; ')
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
                if (!originalRequest.headers) originalRequest.headers = {} as AxiosRequestHeaders
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
          try {
            const userStore = useUserStore()
            if (userStore.isLoggedIn) {
              dedupMessage('error', '权限不足，请联系管理员', 5000)
            }
          } catch (_) { /* store 未就绪 */ }
          break
        case 429:
          errorMsg = '请求过于频繁，请稍后再试'
          break
        case 500:
          errorMsg = errorMsg === '请求失败' ? `服务器内部错误: ${data?.detail ?? data?.message ?? '未知'}` : `服务器内部错误: ${errorMsg}`
          // 500 Error Details
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

// ---------------------------------------------------------------------------
// Retry interceptor for transient failures (network errors, 5xx)
// ---------------------------------------------------------------------------
const MAX_RETRIES = 3
const RETRY_BASE_DELAY_MS = 1000
const RETRYABLE_STATUS = new Set([502, 503, 504])

request.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const config = error.config as ExtendedAxiosConfig | undefined
    if (!config) return Promise.reject(error)

    // Don't retry if already retried by token-refresh logic
    if (config._retry) return Promise.reject(error)

    // Only retry idempotent methods by default
    const method = (config.method ?? 'get').toLowerCase()
    const isIdempotent = method === 'get' || method === 'head'
    if (!isIdempotent) return Promise.reject(error)

    // Check if the error is retryable
    const isNetworkError = !error.response
    const isRetryableStatus = error.response ? RETRYABLE_STATUS.has(error.response.status) : false
    if (!isNetworkError && !isRetryableStatus) return Promise.reject(error)

    // Track retry count
    const retryCount = (config as ExtendedAxiosConfig & { __retryCount?: number }).__retryCount ?? 0
    if (retryCount >= MAX_RETRIES) return Promise.reject(error)

    // Exponential backoff with jitter
    const delay = RETRY_BASE_DELAY_MS * Math.pow(2, retryCount) + Math.random() * 500
    await new Promise((resolve) => setTimeout(resolve, delay))

    ;(config as ExtendedAxiosConfig & { __retryCount: number }).__retryCount = retryCount + 1
    return request(config)
  }
)

export default request
