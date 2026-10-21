/**
 * B5 · LLM 配置状态 API 契约（onboarding「连模型」的数据源）。
 *
 * 判别点：请求路径必须是 /monitoring/llm-status（baseURL=/api/v1），且沿用
 * 首屏重试预算（FIRST_SCREEN_REQUEST_CONFIG）——被仪表盘首屏直接消费。
 *
 * 先红：基线无 getLLMStatus 导出 / 无对应后端端点。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

const { get } = vi.hoisted(() => ({ get: vi.fn() }))

vi.mock('@/utils/request', () => ({
  default: { get },
  FIRST_SCREEN_REQUEST_CONFIG: { __maxRetries: 1 },
}))

import { getLLMStatus } from '../monitoring'

beforeEach(() => {
  get.mockReset()
})

describe('getLLMStatus', () => {
  it('GET /monitoring/llm-status，并携带首屏重试预算', async () => {
    get.mockResolvedValue({
      data: {
        success: true,
        data: { configured: true, provider: 'deepseek', model: 'deepseek-v4-flash', source: 'server' },
      },
    })

    const res = await getLLMStatus()

    expect(get).toHaveBeenCalledWith('/monitoring/llm-status', { __maxRetries: 1 })
    expect(res.data.data.configured).toBe(true)
  })
})
