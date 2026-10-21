/**
 * E14/D1 判别：X-Silent-Error 必须覆盖响应拦截器 403 分支的附加全局 toast。
 *
 * 背景：403 分支在通用提示之外还额外弹「权限不足，请联系管理员」；该调用
 * 此前不受 silent 头控制 —— 页面已用 silent + 自有错误提示时，403 仍会双弹。
 * 约定：silent = 错误由调用方自行呈现，拦截器不得再弹任何 toast。
 *
 * 变异点：去掉 403 分支的 silent 守卫 → 「silent 下零全局 toast」用例红
 * （附加提示仍会弹出）；非 silent 的既有行为由第二用例钉住。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const { messageError } = vi.hoisted(() => ({ messageError: vi.fn() }))

vi.mock('naive-ui', () => ({
  createDiscreteApi: () => ({
    message: { error: messageError, success: vi.fn(), warning: vi.fn(), info: vi.fn(), loading: vi.fn() },
  }),
}))

import request from '@/utils/request'
import { useUserStore } from '@/stores/user'

function reject403(headers: Record<string, string>, respHeaders: Record<string, string> = {}) {
  const handlers = (request.interceptors.response as unknown as {
    handlers: Array<{ rejected: (e: unknown) => Promise<unknown> }>
  }).handlers
  const config = { url: '/users/', method: 'post' as const, headers }
  const error = Object.assign(new Error('Request failed with status code 403'), {
    isAxiosError: true,
    config,
    response: { status: 403, data: {}, headers: respHeaders, config },
  })
  return handlers[0].rejected(error)
}

beforeEach(() => {
  messageError.mockClear()
  setActivePinia(createPinia())
  const userStore = useUserStore()
  userStore.token = 'tok'
  userStore.userInfo = { id: 1, username: 'u' } as never
})

describe('响应拦截器 · 403 全局提示尊重 X-Silent-Error', () => {
  it('带 X-Silent-Error：不弹任何全局 toast（错误由调用方唯一呈现）', async () => {
    await expect(reject403({ 'X-Silent-Error': '1' })).rejects.toBeTruthy()
    expect(messageError).not.toHaveBeenCalled()
  })

  it('不带 silent：403 只保留一条全局提示（不再叠加两条不同文案）', async () => {
    await expect(reject403({})).rejects.toBeTruthy()
    expect(messageError).toHaveBeenCalledTimes(1)
    expect(String(messageError.mock.calls[0][0])).toBe('权限不足，请联系管理员')
  })

  it('不带 silent：单条提示仍携带 Request ID（排障线索不因去重丢失）', async () => {
    await expect(reject403({}, { 'X-Request-ID': 'rid-403-1' })).rejects.toBeTruthy()
    expect(messageError).toHaveBeenCalledTimes(1)
    expect(String(messageError.mock.calls[0][0])).toContain('rid-403-1')
  })
})
