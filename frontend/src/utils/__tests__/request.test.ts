/**
 * request.ts 的单元测试。
 *
 * issue #105 点名要测的四块：token 注入、401 静默刷新、队列重放、去重 toast。
 * 其中**队列重放**最容易出错也最值得测 —— 它决定了「刷新期间并发发出的请求
 * 会不会被丢掉」。
 *
 * 测法：给 request 实例换一个自定义 adapter 来截获每次请求的 config，
 * 用 spy 替换 refresh 调用。走的是公开 API，不碰内部实现。
 */
import axios from 'axios'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import request, { clearDefaultAuth } from '../request'
import { useUserStore } from '@/stores/user'
import { removeToken } from '@/utils/auth'

// 401 刷新失败会触发 userStore.logout()，其内部动态导入 router 做登录页跳转；
// 本文件只测请求层，用最小桩隔离路由/布局模块装配。
vi.mock('@/router', () => ({
  default: { currentRoute: { value: { name: 'Dashboard' } }, push: vi.fn() },
}))

/** 每次请求看到的 config（按顺序） */
let seen: Array<Record<string, any>> = []
/** 让 adapter 按调用次数返回不同结果 */
let responses: Array<{ status: number; data?: unknown }> = []

function installAdapter() {
  request.defaults.adapter = (async (config: any) => {
    seen.push({
      url: config.url,
      headers: config.headers,
      authorization: config.headers?.Authorization ?? config.headers?.authorization,
      _retry: config._retry,
    })
    const next = responses.shift() ?? { status: 200, data: { ok: true } }
    const response = { data: next.data, status: next.status, statusText: '', headers: {}, config }
    // axios 的 adapter 需要在非 2xx 时自行 reject，才能触发响应拦截器的错误分支
    if (next.status >= 200 && next.status < 300) return response
    const err: any = new Error(`Request failed with status code ${next.status}`)
    err.config = config
    err.response = response
    err.isAxiosError = true
    throw err
  }) as any
}

function authHeaderOf(entry: Record<string, any>): string | undefined {
  const h = entry.headers ?? {}
  return h.Authorization ?? h.authorization
}

beforeEach(() => {
  setActivePinia(createPinia())
  seen = []
  responses = []
  installAdapter()
  localStorage.clear()
  clearDefaultAuth()
  vi.restoreAllMocks()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('请求拦截器 · token 注入', () => {
  it('有 token 时带上 Authorization 头', async () => {
    localStorage.setItem('docmind_token', 'tok-abc')

    await request.get('/documents')

    expect(authHeaderOf(seen[0])).toBe('Bearer tok-abc')
  })

  it('无 token 时不带 Authorization 头', async () => {
    await request.get('/documents')

    expect(authHeaderOf(seen[0])).toBeUndefined()
  })

  it('剔除历史脏数据里的包裹引号', async () => {
    // 注释里写明「保留剥引号逻辑以兼容历史脏数据」
    localStorage.setItem('docmind_token', '"tok-quoted"')

    await request.get('/documents')

    expect(authHeaderOf(seen[0])).toBe('Bearer tok-quoted')
  })

  it('每个请求都带 X-Request-ID', async () => {
    await request.get('/documents')

    const headers = seen[0].headers ?? {}
    const rid = headers['X-Request-ID'] ?? headers['x-request-id']
    expect(rid).toBeTruthy()
  })
})

describe('401 · 静默刷新与重放', () => {
  it('401 后用新 token 重试同一个请求', async () => {
    localStorage.setItem('docmind_token', 'old-token')
    localStorage.setItem('docmind_refresh_token', 'refresh-ok')

    // 第一次 401，重试时 200
    responses = [{ status: 401 }, { status: 200, data: { ok: true } }]
    vi.spyOn(axios, 'post').mockResolvedValue({
      data: { data: { access_token: 'new-token', expires_in: 3600 } },
    } as any)

    const resp = await request.get('/documents')

    expect(resp.status).toBe(200)
    expect(seen).toHaveLength(2)
    expect(authHeaderOf(seen[1])).toBe('Bearer new-token')
    expect(localStorage.getItem('docmind_token')).toBe('new-token')
  })

  it('401 refresh retry returns the current user profile and keeps the new token', async () => {
    removeToken()
    localStorage.setItem('docmind_token', 'old-token')
    localStorage.setItem('docmind_refresh_token', 'refresh-ok')
    localStorage.setItem('user_info', JSON.stringify({
      id: 1, username: 'cached-user', role: 'user', organization_id: 1,
    }))
    responses = [
      { status: 401 },
      {
        status: 200,
        data: { data: { id: 1, username: 'fresh-user', role: 'user', organization_id: 1 } },
      },
    ]
    const refreshSpy = vi.spyOn(axios, 'post').mockResolvedValue({
      data: {
        data: {
          access_token: 'new-token', expires_in: 3600, refresh_token: 'refresh-rotated',
        },
      },
    } as any)
    const userStore = useUserStore()

    const result = await userStore.getUserInfo()

    expect(refreshSpy).toHaveBeenCalledTimes(1)
    expect(seen).toHaveLength(2)
    expect(seen[0].url).toBe('/users/me')
    expect(seen[1].url).toBe('/users/me')
    expect(seen[1]._retry).toBe(true)
    expect(seen[0].authorization).toBe('Bearer old-token')
    expect(seen[1].authorization).toBe('Bearer new-token')
    expect(result).toEqual({ data: expect.objectContaining({ id: 1, username: 'fresh-user' }) })
    expect(userStore.userInfo?.username).toBe('fresh-user')
    expect(userStore.token).toBe('new-token')
    expect(userStore.authServiceUnavailable).toBe(false)
    expect(localStorage.getItem('docmind_token')).toBe('new-token')
    expect(localStorage.getItem('docmind_refresh_token')).toBe('refresh-rotated')
    removeToken()
  })

  it('刷新失败时不再重试，并清掉登录态', async () => {
    localStorage.setItem('docmind_token', 'old-token')
    localStorage.setItem('docmind_refresh_token', 'refresh-bad')

    responses = [{ status: 401 }]
    vi.spyOn(axios, 'post').mockRejectedValue(new Error('refresh failed'))

    await expect(request.get('/documents')).rejects.toBeTruthy()

    // 原请求没有重试；登出接线会额外发出一条 POST /auth/logout，本地凭证随后清空
    await vi.waitFor(() => {
      expect(seen.filter((entry) => entry.url === '/documents')).toHaveLength(1)
      expect(seen.some((entry) => entry.url === '/auth/logout')).toBe(true)
      expect(localStorage.getItem('docmind_token')).toBeFalsy()
    })
  })

  it('刷新期间并发发出的请求会被挂起，刷新完成后一起重放', async () => {
    localStorage.setItem('docmind_token', 'old-token')
    localStorage.setItem('docmind_refresh_token', 'refresh-ok')

    // 两个请求都先 401；重放时都成功
    responses = [{ status: 401 }, { status: 401 }, { status: 200 }, { status: 200 }]

    let resolveRefresh: (v: any) => void = () => {}
    const refreshGate = new Promise((res) => { resolveRefresh = res })
    vi.spyOn(axios, 'post').mockImplementation((() => refreshGate) as any)

    const p1 = request.get('/a')
    const p2 = request.get('/b')

    // 让两个请求都走到 401 分支
    await new Promise((r) => setTimeout(r, 10))

    // 放行刷新
    resolveRefresh({ data: { data: { access_token: 'new-token', expires_in: 3600 } } })

    const [r1, r2] = await Promise.all([p1, p2])

    expect(r1.status).toBe(200)
    expect(r2.status).toBe(200)
    // 两个原始请求 + 两个重放 = 4 次
    expect(seen.length).toBeGreaterThanOrEqual(4)
    // 重放的请求都带上了新 token
    const replayed = seen.slice(1).map(authHeaderOf).filter(Boolean)
    expect(replayed.every((h) => h === 'Bearer new-token')).toBe(true)
  })
})

describe('瞬态失败重试预算 · 首屏请求降级', () => {
  it('默认 GET：503 最多 4 次尝试（30s 超时 × 4，旧行为不回归）', async () => {
    vi.useFakeTimers()
    try {
      responses = Array.from({ length: 10 }, () => ({ status: 503 }))

      const p = request.get('/monitoring/llm-stats')
      const assertion = expect(p).rejects.toBeTruthy()
      await vi.runAllTimersAsync()
      await assertion

      expect(seen.length).toBe(4)
    } finally {
      vi.useRealTimers()
    }
  })

  it('__maxRetries=1：首屏请求最多 2 次尝试即报错（交给区块错误态）', async () => {
    vi.useFakeTimers()
    try {
      responses = Array.from({ length: 10 }, () => ({ status: 503 }))

      const p = request.get('/monitoring/llm-stats', { __maxRetries: 1 })
      const assertion = expect(p).rejects.toBeTruthy()
      await vi.runAllTimersAsync()
      await assertion

      expect(seen.length).toBe(2)
    } finally {
      vi.useRealTimers()
    }
  })
})
