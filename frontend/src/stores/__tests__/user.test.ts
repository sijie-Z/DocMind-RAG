/**
 * T3 判别测试 · admin 判定契约（W0-1 / ADR-001 D3）
 *
 * 契约（本测试钉死）：
 *  - 平台管理员只认显式 is_superuser === true（严格布尔）；
 *  - 组织管理员认当前组织的 is_org_admin === true（per-org 角色，后端下发）；
 *  - isAdmin = is_superuser || is_org_admin；
 *  - users.role 只是展示标签，role === 'admin' 单独不再成立。
 *  - 登录 payload 与 /users/me 里的标志进入 store 后立即生效；
 *  - 菜单过滤与路由守卫消费同一个 isAdmin / isSuperuser。
 *
 * 变异（预计红）：isAdmin 退回 `role === 'admin'`，或漏掉 is_org_admin。
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

  it('role=admin 单独不成立（展示标签不再参与授权）', () => {
    const store = useUserStore()
    store.userInfo = makeInfo({ role: 'admin' })
    expect(store.isAdmin).toBe(false)
  })

  it('role=user + is_superuser=true → isAdmin / isSuperuser 成立', () => {
    const store = useUserStore()
    store.userInfo = makeInfo({ role: 'user', is_superuser: true })
    expect(store.isAdmin).toBe(true)
    expect(store.isSuperuser).toBe(true)
  })

  it('role=user + is_org_admin=true → isAdmin 成立（组织管理菜单可见）', () => {
    const store = useUserStore()
    store.userInfo = makeInfo({ role: 'user', is_org_admin: true })
    expect(store.isAdmin).toBe(true)
    expect(store.isOrgAdmin).toBe(true)
    expect(store.isSuperuser).toBe(false)
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
