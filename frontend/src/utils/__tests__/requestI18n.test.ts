/**
 * 响应拦截器全局错误文案必须跟随界面语言（不再固定中文）。
 *
 * 修前：401/403/429/500/网络错误全部写死中文；en/ja/fr 用户看到中文 toast。
 * 修复后走 errors.* 词条。本文件直接调用拦截器的 rejected 处理器，
 * 断言全局 toast 的英文文案（变异回中文或写死英文都会红）。
 *
 * 变异点：任一分支写死中文/英文 → 对应断言红；请求 ID 后缀格式变化 → 红。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const { messageError } = vi.hoisted(() => ({ messageError: vi.fn() }))

vi.mock('naive-ui', () => ({
  createDiscreteApi: () => ({
    message: { error: messageError, success: vi.fn(), warning: vi.fn(), info: vi.fn(), loading: vi.fn() },
  }),
}))

vi.mock('@/router', () => ({
  default: { currentRoute: { value: { name: 'Dashboard' } }, push: vi.fn() },
}))

import request from '@/utils/request'
import { useUserStore } from '@/stores/user'
import i18n from '@/locales'
import en from '@/locales/en'

function rejectedHandler(): (e: unknown) => Promise<unknown> {
  const handlers = (request.interceptors.response as unknown as {
    handlers: Array<{ rejected: (e: unknown) => Promise<unknown> }>
  }).handlers
  return handlers[0].rejected
}

function axiosError(status: number | undefined, opts: {
  data?: unknown
  headers?: Record<string, string>
  url?: string
} = {}) {
  const config = { url: opts.url || '/documents', method: 'get' as const, headers: {} }
  const error = Object.assign(new Error('boom'), { isAxiosError: true, config })
  if (status !== undefined) {
    ;(error as unknown as Record<string, unknown>).response = {
      status,
      data: opts.data ?? {},
      headers: opts.headers ?? {},
      config,
    }
  }
  return error
}

async function reject(status: number | undefined, opts: Parameters<typeof axiosError>[1] = {}) {
  await expect(rejectedHandler()(axiosError(status, opts))).rejects.toBeTruthy()
}

const lastToast = () => String(messageError.mock.calls.at(-1)?.[0] ?? '')

beforeEach(() => {
  setActivePinia(createPinia())
  messageError.mockClear()
  const userStore = useUserStore()
  userStore.token = 'tok'
  userStore.userInfo = { id: 1, username: 'u' } as never
  i18n.global.setLocaleMessage('en', en as never)
  ;(i18n.global.locale as unknown as { value: string }).value = 'en'
})

afterEach(() => {
  i18n.global.locale.value = 'zh'
})

describe('响应拦截器 · 全局错误 toast 跟随语言（en）', () => {
  it('500 → 英文服务器错误，且不泄露内部 detail', async () => {
    await reject(500, { data: { detail: 'stack trace secret' } })
    expect(lastToast()).toBe('Internal server error. Please try again later.')
  })

  it('429 → 英文限流提示', async () => {
    await reject(429)
    expect(lastToast()).toBe('Too many requests. Please try again later.')
  })

  it('400 后端 detail 原样展示（不被通用文案覆盖）', async () => {
    await reject(400, { data: { detail: 'field is required' } })
    expect(lastToast()).toBe('field is required')
  })

  it('Request ID 后缀按英文格式拼接', async () => {
    await reject(500, { headers: { 'X-Request-ID': 'rid-9' } })
    expect(lastToast()).toBe('Internal server error. Please try again later. (Request ID: rid-9)')
  })

  it('网络错误 → 英文连接失败提示', async () => {
    await reject(undefined)
    expect(lastToast()).toBe('Network connection failed. Check that the backend service is running.')
  })

  it('403（已登录）→ 只弹一条英文权限提示', async () => {
    await reject(403)
    expect(messageError).toHaveBeenCalledTimes(1)
    expect(lastToast()).toBe('Insufficient permissions. Contact your administrator.')
  })
})
