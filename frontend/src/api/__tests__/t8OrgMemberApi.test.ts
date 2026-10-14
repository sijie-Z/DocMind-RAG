/**
 * T8 组织成员管理 API 接线判别用例（最小 UI 的请求形状契约）。
 *
 * 后端端点（openapi 实测）：
 * - POST   /organizations/{org_id}/members/{user_id}       （无 body）
 * - PATCH  /organizations/{org_id}/members/{user_id}/role  （role 为 **query** 参数！）
 * - DELETE /organizations/{org_id}/members/{user_id}
 * 误把 role 放进 body 会 422（FastAPI 单标量参数默认 query）。
 * 变异点：role 改发 body → 用例 2 红；URL 前缀/方法错 → 对应用例红。
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
import {
  addOrganizationMember,
  updateOrganizationMemberRole,
  removeOrganizationMember,
} from '@/api/organization'

const mocked = request as unknown as {
  get: Mock
  post: Mock
  put: Mock
  patch: Mock
  delete: Mock
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('organization 成员管理 API · 请求形状', () => {
  it('添加成员：POST /organizations/{org}/members/{user}，无 body', async () => {
    mocked.post.mockResolvedValue({ data: { success: true } } as never)
    await addOrganizationMember(3, 7)
    expect(mocked.post).toHaveBeenCalledTimes(1)
    const [url, body] = mocked.post.mock.calls[0]
    expect(url).toBe('/organizations/3/members/7')
    expect(body === undefined || body === null).toBe(true)
  })

  it('改角色：PATCH .../role 且 role 走 query 参数（后端 openapi: in=query）', async () => {
    mocked.patch.mockResolvedValue({ data: { success: true } } as never)
    await updateOrganizationMemberRole(3, 7, 'admin')
    expect(mocked.patch).toHaveBeenCalledTimes(1)
    const [url, body, cfg] = mocked.patch.mock.calls[0]
    expect(url).toBe('/organizations/3/members/7/role')
    expect(body === undefined || body === null).toBe(true)
    expect((cfg as { params?: { role?: string } }).params).toEqual({ role: 'admin' })
  })

  it('移除成员：DELETE /organizations/{org}/members/{user}', async () => {
    mocked.delete.mockResolvedValue({ data: { success: true } } as never)
    await removeOrganizationMember(3, 7)
    expect(mocked.delete).toHaveBeenCalledTimes(1)
    expect(mocked.delete.mock.calls[0][0]).toBe('/organizations/3/members/7')
  })
})
