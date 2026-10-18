/**
 * 登录态持久化：token 过期不再直接清号
 *
 * 现状缺陷：路由守卫本地判定 token 过期即 removeToken()+logout()，
 * 即使本地持有 refresh_token 也会把用户踢回登录页 —— 刷新页面即丢登录态。
 *
 * 期望契约：
 *   1. 过期 + refresh 成功 → 停留在目标路由，localStorage 更新为新 token，
 *      且续期后 refresh_token 仍在（轮换后的新值）；
 *   2. refresh 失败 → 先尝试过续期，再清号跳 Login，并带上原目标地址（redirect）；
 *   3. 并发三个守卫同时遇到过期 → 共享同一次 /auth/refresh，只发 1 次。
 *
 * 守卫的续期走 utils/request 的共享刷新入口（与 401 拦截器同一份队列/单飞状态），
 * 因此这里用真实 request 模块 + axios.post 打桩，从守卫全链路验证。
 */
import axios from 'axios'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import request from '@/utils/request'

const REFRESH_OK = {
  data: {
    data: {
      access_token: 'new-token',
      expires_in: 3600,
      refresh_token: 'refresh-rotated',
    },
  },
}

/** /users/me 走 request 实例：给守卫续期后的 getUserInfo 校验提供成功响应 */
function installUsersMeAdapter() {
  request.defaults.adapter = (async (config: any) => ({
    data: { data: { id: 1, username: 'tester', role: 'user' } },
    status: 200,
    statusText: 'OK',
    headers: {},
    config,
  })) as any
}

function seedExpiredLoginState() {
  localStorage.setItem('docmind_token', 'expired-token')
  localStorage.setItem('docmind_token_expires', (Date.now() - 60_000).toString())
  localStorage.setItem('docmind_refresh_token', 'refresh-ok')
}

describe('登录态持久化 · token 过期静默续期', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    installUsersMeAdapter()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('过期 + refresh 成功 → 停留在目标路由，token 已更新，refresh_token 未被删除', async () => {
    seedExpiredLoginState()
    const postSpy = vi.spyOn(axios, 'post').mockResolvedValue(REFRESH_OK as any)

    const { default: router } = await import('../index')
    await router.push({ name: 'Knowledge' })

    expect(router.currentRoute.value.name).toBe('Knowledge')
    expect(postSpy).toHaveBeenCalledTimes(1)
    expect(localStorage.getItem('docmind_token')).toBe('new-token')
    expect(localStorage.getItem('docmind_refresh_token')).toBe('refresh-rotated')
  }, 30_000)

  it('refresh 失败 → 续期尝试后清号跳 Login，redirect 指向原目标', async () => {
    seedExpiredLoginState()
    const postSpy = vi.spyOn(axios, 'post').mockRejectedValue(new Error('refresh rejected'))

    const { default: router } = await import('../index')
    await router.push({ name: 'Chat' })

    const cur = router.currentRoute.value
    expect(cur.name).toBe('Login')
    expect(cur.query.redirect).toBe('/chat')
    // 判别点：失败路径也必须先尝试过续期（旧实现直接清号、不发 refresh）
    expect(postSpy).toHaveBeenCalledTimes(1)
    expect(localStorage.getItem('docmind_token')).toBeFalsy()
    expect(localStorage.getItem('docmind_refresh_token')).toBeFalsy()
  }, 30_000)

  it('三个守卫并发遇到过期 → 只发一次 refresh（共享同一续期）', async () => {
    seedExpiredLoginState()
    let releaseRefresh: (v: unknown) => void = () => {}
    const refreshGate = new Promise((resolve) => {
      releaseRefresh = resolve
    })
    const postSpy = vi.spyOn(axios, 'post').mockImplementation((() => refreshGate) as any)

    const { default: router } = await import('../index')
    const navigations = [
      router.push({ name: 'Prompts' }),
      router.push({ name: 'Search' }),
      router.push({ name: 'Workflow' }),
    ]
    // 让三个守卫都走到续期等待点（刷新被闸门挂起）
    await new Promise((r) => setTimeout(r, 20))
    releaseRefresh(REFRESH_OK)
    await Promise.allSettled(navigations)

    expect(postSpy).toHaveBeenCalledTimes(1)
    expect(localStorage.getItem('docmind_token')).toBe('new-token')
    expect(router.currentRoute.value.name).toBe('Workflow')
  }, 30_000)
})
