/**
 * E4 判别测试 · /users/me 失败分级：仅 401 清号，5xx/网络保留会话
 *
 * 现状缺陷：stores/user.ts 的 getUserInfo 在 catch 里无条件 logout()，
 * 后端一次 503（或网络抖动）就会把本地 token 与缓存用户信息全部清掉，
 * 用户被踢回登录页——服务端一过载，所有人掉线。
 *
 * 期望契约：
 *  - 5xx / 网络错误（无 response）：保留 token 与本地缓存的 userInfo，
 *    authServiceUnavailable 置 true（供全局横幅/不可用页消费），错误继续上抛；
 *  - 401：仍走原清号逻辑（removeToken + userInfo 清空），authServiceUnavailable 复位；
 *  - 拦截器已判定续期失败的标记错误（authSessionExpired，可能无 response.status）：
 *    同样按会话失效清号，避免被误判为服务不可用。
 *
 * 变异（预计红）：catch 退回无条件 logout() → 503/网络用例的用户信息与 token 断言红；
 * 401 判定被删除 → 401 用例的 removeToken 断言红。
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('@/api/auth', () => ({
  loginWithJson: vi.fn(),
  register: vi.fn(),
  getUserProfile: vi.fn(),
  logout: vi.fn(() => Promise.resolve({ data: {} })),
}))
vi.mock('@/api/user', () => ({
  getUserSettings: vi.fn(() => Promise.resolve({ data: {} })),
  updateUserSettings: vi.fn(() => Promise.resolve({ data: {} })),
  updateUserProfile: vi.fn(() => Promise.resolve({ data: {} })),
}))
vi.mock('@/utils/auth', () => ({
  getToken: vi.fn(() => 'token-1'),
  setToken: vi.fn(),
  setRefreshToken: vi.fn(),
  removeToken: vi.fn(),
  getRefreshToken: vi.fn(() => undefined),
}))

import { useUserStore } from '@/stores/user'
import { getUserProfile } from '@/api/auth'
import { removeToken } from '@/utils/auth'

const CACHED_USER = { id: 9, username: 'cached_user', role: 'user' }

function httpError(status: number): Error {
  return Object.assign(new Error(`HTTP ${status}`), { response: { status } })
}

describe('E4 · getUserInfo 失败分级（仅 401 清号）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    localStorage.setItem('user_info', JSON.stringify(CACHED_USER))
    vi.clearAllMocks()
  })

  it('503 → 保留 token 与缓存 userInfo，并标记服务不可用', async () => {
    const err = httpError(503)
    vi.mocked(getUserProfile).mockRejectedValue(err)

    const store = useUserStore()
    await expect(store.getUserInfo()).rejects.toBe(err)

    expect(store.authServiceUnavailable).toBe(true)
    expect(store.userInfo?.id).toBe(9)
    expect(store.token).toBe('token-1')
    expect(removeToken).not.toHaveBeenCalled()
    expect(localStorage.getItem('user_info')).toContain('cached_user')
  })

  it('网络错误（无 response）→ 同样保留会话，不踢登录', async () => {
    const err = new Error('Network Error')
    vi.mocked(getUserProfile).mockRejectedValue(err)

    const store = useUserStore()
    await expect(store.getUserInfo()).rejects.toBe(err)

    expect(store.authServiceUnavailable).toBe(true)
    expect(store.userInfo?.id).toBe(9)
    expect(removeToken).not.toHaveBeenCalled()
  })

  it('401 → 仍走原清号逻辑（防回退）', async () => {
    const err = httpError(401)
    vi.mocked(getUserProfile).mockRejectedValue(err)

    const store = useUserStore()
    await expect(store.getUserInfo()).rejects.toBe(err)

    expect(removeToken).toHaveBeenCalled()
    expect(store.userInfo).toBeNull()
    expect(store.authServiceUnavailable).toBe(false)
  })

  it('拦截器标记的续期失败（无 response.status）→ 按会话失效清号', async () => {
    const err = Object.assign(new Error('refresh_token_missing'), { authSessionExpired: true })
    vi.mocked(getUserProfile).mockRejectedValue(err)

    const store = useUserStore()
    await expect(store.getUserInfo()).rejects.toBe(err)

    expect(removeToken).toHaveBeenCalled()
    expect(store.userInfo).toBeNull()
    expect(store.authServiceUnavailable).toBe(false)
  })
})
