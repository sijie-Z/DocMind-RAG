/**
 * B4：组织设置前端 API 契约。
 *
 * 先红：这三个函数与对应类型当前不存在（组织设置页无数据源）。
 * 钉住 URL / 方法 / 请求体形状，防止页面与后端契约漂移。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/utils/request', () => ({
  default: {
    get: vi.fn().mockResolvedValue({ data: {} }),
    post: vi.fn().mockResolvedValue({ data: {} }),
    put: vi.fn().mockResolvedValue({ data: {} }),
    delete: vi.fn().mockResolvedValue({ data: {} }),
  },
}))

import request from '@/utils/request'
import {
  getOrganizationSettings,
  updateOrganizationSettings,
  getOrganizationSettingsAudit,
} from '../organization'

const mockedGet = vi.mocked(request.get)
const mockedPut = vi.mocked(request.put)

describe('organization settings api', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('GET 组织生效设置', async () => {
    await getOrganizationSettings(7)
    expect(mockedGet).toHaveBeenCalledWith('/organizations/7/settings', undefined)
  })

  it('GET 组织生效设置可要求静默（页面内联错误态唯一呈现）', async () => {
    await getOrganizationSettings(7, { silent: true })
    expect(mockedGet).toHaveBeenCalledWith('/organizations/7/settings', {
      headers: { 'X-Silent-Error': '1' },
    })
  })

  it('PUT 组织设置（部分字段、null 表示清除）', async () => {
    const payload = {
      retrieval_top_k: 3,
      allow_privacy_mode: false,
      monthly_token_quota: null,
    }
    await updateOrganizationSettings(7, payload)
    expect(mockedPut).toHaveBeenCalledWith('/organizations/7/settings', payload, {
      headers: { 'X-Silent-Error': '1' },
    })
  })

  it('GET 组织设置审计', async () => {
    await getOrganizationSettingsAudit(7)
    expect(mockedGet).toHaveBeenCalledWith('/organizations/7/settings/audit', {
      headers: { 'X-Silent-Error': '1' },
    })
  })
})
