/**
 * T3 判别测试 · admin 判定契约（role 与 is_superuser 组合）
 *
 * 现场：后端鉴权普遍按 `is_superuser or role == "admin"` 判管理员，但登录
 * payload 与 /users/me 只下发 role，前端 stores/user.ts 的 isAdmin 也只认
 * `role === 'admin'` —— superuser 账号（role=user）看不到管理菜单
 * （layouts/index.vue 按 isAdmin 过滤 adminOnly 分组），路由守卫
 * （router/index.ts:367）也会把它重定向回 Dashboard。
 *
 * 契约（本测试钉死）：
 *  - isAdmin = role === 'admin' || is_superuser === true（严格布尔）；
 *  - 登录 payload 与 /users/me 里的 is_superuser 进入 store 后立即生效；
 *  - 菜单过滤与路由守卫消费的都是同一个 isAdmin 计算属性，组合判定
 *    即两处同口径（守卫每次进管理路由前还会重新拉取 /users/me）。
 *
 * 变异（预计红）：isAdmin 退回只认 `role === 'admin'`
 * → 「role=user + is_superuser=true」的组合用例、登录回填用例与
 *   getUserInfo（守卫路径）用例全部变红。
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('@/api/auth', () => ({
  loginWithJson: vi.fn(),
  register: vi.fn(),
  getUserProfile: vi.fn(),
}))
vi.mock('@/api/user', () => ({
  getUserSettings: vi.fn(() => Promise.resolve({ data: {} })),
  updateUserSettings: vi.fn(() => Promise.resolve({ data: {} })),
  updateUserProfile: vi.fn(() => Promise.resolve({ data: {} })),
}))
vi.mock('@/utils/auth', () => ({
  getToken: vi.fn(() => ''),
  setToken: vi.fn(),
  setRefreshToken: vi.fn(),
  removeToken: vi.fn(),
}))

import { useUserStore } from '@/stores/user'
import { loginWithJson, getUserProfile } from '@/api/auth'
import { getToken } from '@/utils/auth'
import type { UserInfo } from '@/types/user'

function makeInfo(overrides: Partial<UserInfo> = {}): UserInfo {
  return {
    id: 7,
    username: 'claim_probe',
    email: 'claim_probe@example.com',
    isAdmin: false,
    createdAt: '',
    organizations: [],
    ...overrides,
  }
}

describe('useUserStore · isAdmin 组合判定', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    vi.clearAllMocks()
  })

  it('role=admin 单独成立（原有行为不回归）', () => {
    const store = useUserStore()
    store.userInfo = makeInfo({ role: 'admin' })
    expect(store.isAdmin).toBe(true)
  })

  it('role=user + is_superuser=true → isAdmin 成立（superuser 管理菜单可见）', () => {
    const store = useUserStore()
    store.userInfo = makeInfo({ role: 'user', is_superuser: true })
    expect(store.isAdmin).toBe(true)
  })

  it('role=user + is_superuser=false → isAdmin 不成立', () => {
    const store = useUserStore()
    store.userInfo = makeInfo({ role: 'user', is_superuser: false })
    expect(store.isAdmin).toBe(false)
  })

  it('role 缺失 + is_superuser=true → isAdmin 仍成立', () => {
    const store = useUserStore()
    store.userInfo = makeInfo({ is_superuser: true })
    expect(store.isAdmin).toBe(true)
  })

  it('is_superuser 为字符串 "true" 不算管理员（严格布尔判定）', () => {
    const store = useUserStore()
    store.userInfo = makeInfo({ role: 'user', is_superuser: 'true' as unknown as boolean })
    expect(store.isAdmin).toBe(false)
  })

  it('登录 payload 下发的 is_superuser 进入 store 后 isAdmin 成立', async () => {
    vi.mocked(loginWithJson).mockResolvedValue({
      data: {
        data: {
          access_token: 'access-token',
          token_type: 'bearer',
          expires_in: 60,
          refresh_token: 'refresh-token',
          user_info: makeInfo({ role: 'user', is_superuser: true }),
        },
      },
    } as never)

    const store = useUserStore()
    const result = await store.login({ username: 'claim_probe', password: 'ValidPass123!' })

    expect(result.success).toBe(true)
    expect(store.userInfo?.is_superuser).toBe(true)
    expect(store.isAdmin).toBe(true)
  })

  it('getUserInfo（路由守卫刷新路径）拿到 role=user + is_superuser=true → isAdmin 成立', async () => {
    vi.mocked(getToken).mockReturnValue('valid-token')
    vi.mocked(getUserProfile).mockResolvedValue({
      data: makeInfo({ role: 'user', is_superuser: true }),
    } as never)

    const store = useUserStore()
    await store.getUserInfo()

    expect(store.userInfo?.is_superuser).toBe(true)
    expect(store.isAdmin).toBe(true)
  })
})
