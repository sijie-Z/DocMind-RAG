/**
 * T2 登出接线判别测试（第二轮）
 *
 * 现状缺陷：userStore.logout() 只清本地，不调用后端 POST /auth/logout；
 * 服务端令牌（access/refresh）仍在有效期内，登出后被窃取的令牌仍可访问受保护接口。
 *
 * 修法约定（本测试钉死）：
 *  1. logout() 先同步清空本地，再调用 api/auth 的 logout()（携带 refresh token
 *     与此前捕获的 access token），服务端失败仅 console.warn；
 *  2. 服务端 5xx/网络失败不阻断本地登出：本地凭证照常清空、
 *     logout() 正常 resolve（不得产生未处理异常）；
 *  3. 默认跳转登录页（已在登录页时跳过）；导航链路传 navigate: false 时
 *     不自行跳转，由调用方（守卫/拦截器）统一收口。
 *
 * 变异（预计红）：
 *  - logout() 不调后端 → 用例 1/2 红；
 *  - 后端失败时直接抛出/不继续清本地 → 用例 2 红；
 *  - 默认路径不跳登录页 → 用例 1/2 红；无条件重复跳转 → 用例 3 红；
 *  - navigate: false 仍自行跳转 → 用例 4 红。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const routerMocks = vi.hoisted(() => ({
  push: vi.fn(),
  currentRoute: { value: { name: 'Dashboard' } as { name: string } },
}))

vi.mock('@/router', () => ({
  default: { currentRoute: routerMocks.currentRoute, push: routerMocks.push },
}))

vi.mock('@/api/auth', () => ({
  loginWithJson: vi.fn(),
  register: vi.fn(),
  getUserProfile: vi.fn(),
  logout: vi.fn(),
}))

vi.mock('@/api/user', () => ({
  getUserSettings: vi.fn(),
  updateUserSettings: vi.fn(),
}))

vi.mock('@/utils/auth', () => ({
  getToken: vi.fn(() => 'access-token-abc'),
  getRefreshToken: vi.fn(() => 'refresh-token-abc'),
  setToken: vi.fn(),
  setRefreshToken: vi.fn(),
  removeToken: vi.fn(),
}))

vi.mock('@/utils/request', () => ({
  clearDefaultAuth: vi.fn(),
}))

import { useUserStore } from '@/stores/user'
import { logout as apiLogout } from '@/api/auth'
import { removeToken } from '@/utils/auth'

const mockedApiLogout = vi.mocked(apiLogout)
const mockedRemoveToken = vi.mocked(removeToken)

let warnSpy: ReturnType<typeof vi.spyOn>

beforeEach(() => {
  localStorage.clear()
  setActivePinia(createPinia())
  vi.clearAllMocks()
  mockedApiLogout.mockResolvedValue({ data: { message: '登出成功' } } as never)
  routerMocks.currentRoute.value.name = 'Dashboard'
  warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
})

afterEach(() => {
  warnSpy.mockRestore()
})

const seedLoggedIn = () => {
  localStorage.setItem('user_info', JSON.stringify({ id: 1, username: 'u1' }))
  localStorage.setItem('language', 'zh')
  localStorage.setItem('theme', 'dark')
  localStorage.setItem('activeSystemPrompt', 'prompt')
  const store = useUserStore()
  expect(store.isLoggedIn).toBe(true)
  return store
}

describe('userStore.logout · 服务端登出接线', () => {
  it('先调用 /auth/logout 并携带 refresh token，再清空本地并跳登录页', async () => {
    const store = seedLoggedIn()
    await store.logout()

    expect(mockedApiLogout).toHaveBeenCalledTimes(1)
    expect(mockedApiLogout).toHaveBeenCalledWith('refresh-token-abc', 'access-token-abc')
    expect(mockedRemoveToken).toHaveBeenCalledTimes(1)
    expect(store.userInfo).toBeNull()
    expect(store.token).toBe('')
    expect(localStorage.getItem('user_info')).toBeNull()
    expect(localStorage.getItem('language')).toBe('zh')
    expect(localStorage.getItem('theme')).toBe('dark')
    expect(localStorage.getItem('activeSystemPrompt')).toBeNull()
    expect(routerMocks.push).toHaveBeenCalledWith({ name: 'Login' })
  })

  it('后端 5xx 时仍清空本地、不抛未处理异常，并 console.warn 告警', async () => {
    const store = seedLoggedIn()
    mockedApiLogout.mockRejectedValue(
      Object.assign(new Error('Request failed with status code 503'), {
        response: { status: 503, data: { detail: '服务端令牌吊销失败' } },
      })
    )

    await expect(store.logout()).resolves.toBeUndefined()

    expect(warnSpy).toHaveBeenCalled()
    expect(mockedRemoveToken).toHaveBeenCalledTimes(1)
    expect(store.userInfo).toBeNull()
    expect(store.token).toBe('')
    expect(localStorage.getItem('user_info')).toBeNull()
    expect(routerMocks.push).toHaveBeenCalledWith({ name: 'Login' })
  })

  it('已在登录页时不重复跳转', async () => {
    const store = seedLoggedIn()
    routerMocks.currentRoute.value.name = 'Login'

    await store.logout()

    expect(routerMocks.push).not.toHaveBeenCalled()
  })

  it('navigate: false 时清态与吊销照常，但不自行跳转（由调用方收口）', async () => {
    const store = seedLoggedIn()

    await store.logout({ navigate: false })

    expect(mockedApiLogout).toHaveBeenCalledWith('refresh-token-abc', 'access-token-abc')
    expect(mockedRemoveToken).toHaveBeenCalledTimes(1)
    expect(store.userInfo).toBeNull()
    expect(store.token).toBe('')
    expect(routerMocks.push).not.toHaveBeenCalled()
  })
})
