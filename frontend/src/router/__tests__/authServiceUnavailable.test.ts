/**
 * E4 判别测试 · 服务端 5xx 不清会话 + 首屏全局进度（真实守卫链路）
 *
 * 现场：/users/me 返回 503（或网络错误）时，路由守卫与 store 一律清号跳登录；
 * 服务端一次过载 = 全员掉线。且首屏校验期间页面无任何可见加载反馈。
 *
 * 期望契约：
 *  1. 有本地缓存 userInfo：守卫不等待 /users/me 校验，直接渲染目标页（非白屏），
 *     校验转后台；503 到达后 token/缓存保留、authServiceUnavailable 置 true；
 *  2. 无本地缓存 + 5xx：进入具名 ServiceUnavailable 页（非白屏），token 保留；
 *  3. 401：仍清号并回 Login（防回退）；
 *  4. 导航期间全局进度条可见（路由守卫驱动），Layout 挂载前即可感知加载。
 *
 * 变异（预计红）：守卫/ store 退回「失败即 logout」→ 用例 1/2 的 token 与落点断言红；
 * 删除进度条驱动 → 用例 1 的进度断言红。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const CACHED_USER = { id: 5, username: 'cached_user', role: 'user' }

function seedLogin(opts: { cached: boolean }) {
  localStorage.setItem('docmind_token', 'live-token')
  localStorage.setItem('docmind_token_expires', String(Date.now() + 3_600_000))
  if (opts.cached) {
    localStorage.setItem('user_info', JSON.stringify(CACHED_USER))
  }
}

/**
 * 每个用例独立模块注册表（守卫内的 sessionValidated 是模块级状态），
 * 并安装真实 request 链路的 adapter：所有请求以指定状态码拒绝。
 */
async function boot(status: number) {
  vi.resetModules()
  const { createPinia, setActivePinia } = await import('pinia')
  setActivePinia(createPinia())
  const { AxiosError } = await import('axios')
  const requestMod = await import('@/utils/request')
  const request = requestMod.default
  request.defaults.adapter = (async (config: never) => {
    const response = { data: { detail: 'down' }, status, statusText: String(status), headers: {}, config }
    throw new AxiosError(`Request failed with status ${status}`, 'ERR_BAD_RESPONSE', config, {}, response as never)
  }) as never
  const { default: router } = await import('../index')
  // 与 main.ts 同口径：把 router 注册给拦截器（401 自动回登录页依赖它）
  requestMod.setRouter(router)
  const { useUserStore } = await import('@/stores/user')
  const progress = await import('@/utils/routeProgress')
  return { router, useUserStore, progress }
}

describe('E4 · 5xx 不清会话 + 首屏进度（真实守卫链路）', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('/users/me 503 + 本地缓存 → 不踢登录、非白屏、出现服务不可用状态', async () => {
    seedLogin({ cached: true })
    const { router, useUserStore, progress } = await boot(503)

    const nav = router.push({ name: 'Knowledge' })
    await vi.waitFor(() => expect(progress.routeProgressActive.value).toBe(true), { timeout: 2000 })
    await nav

    // 非白屏：目标路由保持，缓存用户信息立即参与渲染
    expect(router.currentRoute.value.name).toBe('Knowledge')
    const store = useUserStore()
    expect(store.userInfo?.id).toBe(5)

    // 导航完成 → 进度条收尾（可见反馈有始有终）
    await vi.waitFor(() => expect(progress.routeProgressActive.value).toBe(false), { timeout: 2000 })

    // 后台校验 503 最终到达（retry 3 次退避后）：保留会话并标记服务不可用
    await vi.waitFor(() => expect(store.authServiceUnavailable).toBe(true), { timeout: 20_000 })
    expect(localStorage.getItem('docmind_token')).toBe('live-token')
    expect(localStorage.getItem('user_info')).toContain('cached_user')
    expect(router.currentRoute.value.name).toBe('Knowledge')
  }, 30_000)

  it('无本地缓存 + 5xx → 进入服务不可用页（非白屏）且 token 保留', async () => {
    seedLogin({ cached: false })
    const { router, useUserStore } = await boot(500)

    await router.push({ name: 'Knowledge' })

    const cur = router.currentRoute.value
    expect(cur.name).toBe('ServiceUnavailable')
    expect(cur.query.redirect).toBe('/knowledge')
    expect(localStorage.getItem('docmind_token')).toBe('live-token')
    expect(useUserStore().userInfo).toBeNull()
  }, 30_000)

  it('401 → 仍走原清号逻辑并回登录页（防回退）', async () => {
    seedLogin({ cached: true })
    const { router } = await boot(401)

    await router.push({ name: 'Knowledge' })

    // 缓存优先放行后，401 由拦截器/stores 收口：清号 + 回 Login
    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('Login'), { timeout: 10_000 })
    expect(localStorage.getItem('docmind_token')).toBeFalsy()
    expect(localStorage.getItem('docmind_refresh_token')).toBeFalsy()
    expect(localStorage.getItem('user_info')).toBeFalsy()
  }, 30_000)
})
