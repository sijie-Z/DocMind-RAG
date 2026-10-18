/**
 * 个人设置 providers 请求路径契约（T10：统一客户端接线）。
 *
 * 后端真源：router.py:51 prefix="/user" + user_settings.py:117
 * @router.get("/settings/providers")。request baseURL=/api/v1，
 * 因此 api 层只需 /user/settings/providers；旧写法 /api/v1/settings/providers 恒 404。
 *
 * 变异点：URL 改回缺 /user 前缀（或写死 /api/v1 前缀造成双前缀）→ 本用例红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import type { Mock } from 'vitest'

vi.mock('@/utils/request', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}))

import request from '@/utils/request'
import { getUserSettingsProviders } from '@/api/user'

const mocked = request as unknown as { get: Mock }

beforeEach(() => {
  vi.clearAllMocks()
})

describe('user settings providers API · 请求路径', () => {
  it('GET /user/settings/providers（不得回退缺 /user 的旧路径）', async () => {
    mocked.get.mockResolvedValue({ data: { success: true, providers: [], current: null } } as never)

    await getUserSettingsProviders()

    expect(mocked.get).toHaveBeenCalledTimes(1)
    expect(mocked.get.mock.calls[0][0]).toBe('/user/settings/providers')
  })
})
