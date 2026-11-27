/**
 * 批次十四：Agent 聊天入口的配额 429 文案。
 *
 * 契约：`ORG_TOKEN_QUOTA_EXCEEDED` 必须给出明确四语文案（含 used/quota），
 * 不能被当成网络错误或普通限流；非配额 429 保留既有状态码错误。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

import { agentApi } from '@/api/agent'
import i18n from '@/locales'

describe('agentApi.chat · 组织配额 429', () => {
  beforeEach(() => {
    localStorage.setItem('docmind_token', 'tok')
    ;(i18n.global.locale as unknown as { value: string }).value = 'zh'
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    localStorage.clear()
  })

  it('配额用尽：抛出含 used/quota 的本地化文案，不触发事件回调', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 429,
      json: async () => ({
        success: false,
        error_code: 'ORG_TOKEN_QUOTA_EXCEEDED',
        detail: { used: 150, quota: 100, reset_at: '2026-12-01T00:00:00+00:00' },
      }),
    })
    vi.stubGlobal('fetch', fetchMock)
    const onEvent = vi.fn()

    await expect(agentApi.chat('你好', onEvent)).rejects.toThrow(/150/)
    await expect(agentApi.chat('你好', onEvent)).rejects.toThrow(/100/)
    expect(fetchMock).toHaveBeenCalled()
    expect(onEvent).not.toHaveBeenCalled()
  })

  it('非配额 429 仍按状态码报错（不误标为配额）', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 429,
        json: async () => ({ success: false, error_code: 'RATE_LIMIT_EXCEEDED' }),
      }),
    )
    await expect(agentApi.chat('你好', vi.fn())).rejects.toThrow(/429/)
  })
})
