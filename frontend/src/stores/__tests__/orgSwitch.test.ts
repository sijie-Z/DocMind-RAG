/**
 * B1：组织切换动作（先红 —— 当前 store 只有写入 ref 的 setCurrentOrganization，
 * 没有任何服务端切换动作，也没有组织上下文版本号）。
 *
 * 契约：
 *  - switchOrganization(orgId) 调用 POST /users/me/organizations/{id}/switch；
 *  - 成功后 currentOrgId / userInfo.organization_id 立即变为新组织；
 *  - organizationVersion 递增 —— 布局层以它为路由页面 key 的一部分，
 *    强制当前页面重挂载，从而让文档/会话等 onMounted 拉取用新组织重新请求；
 *  - 失败时状态不变、版本号不涨（不得出现"前端切了、服务端没切"的假成功）；
 *  - 切到当前组织是幂等空操作，不发请求。
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('@/api/auth', () => ({
  loginWithJson: vi.fn(),
  register: vi.fn(),
  getUserProfile: vi.fn(),
  logout: vi.fn(),
}))
vi.mock('@/api/user', () => ({
  getUserSettings: vi.fn(() => Promise.resolve({ data: {} })),
  updateUserSettings: vi.fn(() => Promise.resolve({ data: {} })),
  updateUserProfile: vi.fn(() => Promise.resolve({ data: {} })),
  getMyOrganizations: vi.fn(() =>
    Promise.resolve({
      data: {
        success: true,
        data: {
          organizations: [
            { id: 1, name: 'Alpha' },
            { id: 2, name: 'Beta' },
          ],
          current_organization_id: 1,
        },
      },
    }),
  ),
  switchOrganization: vi.fn(),
}))
vi.mock('@/utils/auth', () => ({
  getToken: vi.fn(() => 'token'),
  setToken: vi.fn(),
  setRefreshToken: vi.fn(),
  removeToken: vi.fn(),
  getRefreshToken: vi.fn(() => ''),
}))

import { useUserStore } from '@/stores/user'
import { switchOrganization as switchOrganizationApi, getMyOrganizations } from '@/api/user'
import type { UserInfo } from '@/types/user'

function makeInfo(overrides: Partial<UserInfo> = {}): UserInfo {
  return {
    id: 7,
    username: 'org_probe',
    email: 'org_probe@example.com',
    isAdmin: false,
    createdAt: '',
    organizations: [],
    organization_id: 1,
    ...overrides,
  }
}

const mockedSwitch = vi.mocked(switchOrganizationApi)
const mockedList = vi.mocked(getMyOrganizations)

describe('useUserStore · organization switch', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    vi.clearAllMocks()
    mockedSwitch.mockResolvedValue({
      data: { success: true, data: { organization_id: 2 } },
    } as never)
    mockedList.mockResolvedValue({
      data: {
        success: true,
        data: {
          organizations: [
            { id: 1, name: 'Alpha' },
            { id: 2, name: 'Beta' },
          ],
          current_organization_id: 1,
        },
      },
    } as never)
  })

  it('exposes a switchOrganization action and an organization version', () => {
    const store = useUserStore()
    expect(typeof store.switchOrganization).toBe('function')
    expect(store.organizationVersion).toBe(0)
  })

  it('switches the server-side context and refreshes the local organization state', async () => {
    const store = useUserStore()
    store.userInfo = makeInfo({ organization_id: 1 })
    store.currentOrganizationId = 1

    await store.switchOrganization(2)

    expect(mockedSwitch).toHaveBeenCalledWith(2)
    expect(store.currentOrgId).toBe(2)
    expect(store.userInfo?.organization_id).toBe(2)
    expect(store.organizationVersion).toBe(1)
  })

  it('applies per-org is_org_admin from the switch response (W0-1)', async () => {
    const store = useUserStore()
    store.userInfo = makeInfo({ organization_id: 1, is_org_admin: false })
    store.currentOrganizationId = 1
    mockedSwitch.mockResolvedValueOnce({
      data: {
        success: true,
        data: { organization_id: 2, is_org_admin: true },
      },
    } as never)

    await store.switchOrganization(2)

    expect(store.userInfo?.is_org_admin).toBe(true)
    expect(store.isOrgAdmin).toBe(true)
    expect(store.isAdmin).toBe(true)
  })

  it('keeps previous admin flag when response omits is_org_admin (old server)', async () => {
    const store = useUserStore()
    store.userInfo = makeInfo({ organization_id: 1, is_org_admin: true })
    store.currentOrganizationId = 1

    await store.switchOrganization(2)

    expect(store.userInfo?.is_org_admin).toBe(true)
  })

  it('keeps state unchanged when the server rejects the switch', async () => {
    const store = useUserStore()
    store.userInfo = makeInfo({ organization_id: 1 })
    store.currentOrganizationId = 1
    mockedSwitch.mockRejectedValueOnce(new Error('forbidden'))

    await expect(store.switchOrganization(2)).rejects.toThrow('forbidden')

    expect(store.currentOrgId).toBe(1)
    expect(store.userInfo?.organization_id).toBe(1)
    expect(store.organizationVersion).toBe(0)
  })

  it('is a no-op when switching to the current organization', async () => {
    const store = useUserStore()
    store.userInfo = makeInfo({ organization_id: 1 })
    store.currentOrganizationId = 1

    await store.switchOrganization(1)

    expect(mockedSwitch).not.toHaveBeenCalled()
    expect(store.organizationVersion).toBe(0)
  })

  it('aligns the local organization with the server current organization', async () => {
    const store = useUserStore()
    store.userInfo = makeInfo({ organization_id: 1 })
    store.currentOrganizationId = 1
    mockedList.mockResolvedValueOnce({
      data: {
        success: true,
        data: {
          organizations: [
            { id: 1, name: 'Alpha' },
            { id: 2, name: 'Beta' },
          ],
          current_organization_id: 2,
        },
      },
    } as never)

    const organizations = await store.fetchMyOrganizations()

    expect(organizations.map((org) => org.id)).toEqual([1, 2])
    expect(store.currentOrgId).toBe(2)
    expect(store.userInfo?.organization_id).toBe(2)
  })
})
