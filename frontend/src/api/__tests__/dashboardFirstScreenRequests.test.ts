/**
 * 仪表盘首屏接口 · 重试预算接线判别（E6）。
 *
 * request.ts 支持每请求 __maxRetries 覆盖（默认 3 次重试 → 4 次尝试）。
 * 仪表盘首屏请求必须全部显式降级为 1 次重试（最多 2 次尝试）：
 * 单接口慢/抖动时由所在区块错误态兜底，不再「30s 超时 × 4 次」拖死首屏。
 *
 * 判别：旧实现（无第二参数）→ 本文件逐个接口红。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import request, { FIRST_SCREEN_REQUEST_CONFIG } from '@/utils/request'
import { getAdminSummary, getLLMStats, getRAGStats, getOrgSummary, getMonitoringHealth } from '@/api/monitoring'
import { getAuditLogs, getUserStats } from '@/api/user'
import { getConversations } from '@/api/conversation'

const FIRST_SCREEN = { __maxRetries: 1 }

let getSpy: ReturnType<typeof vi.spyOn>

beforeEach(() => {
  getSpy = vi.spyOn(request, 'get').mockResolvedValue({ data: {} } as never)
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('首屏请求重试预算 · 常量与接线', () => {
  it('FIRST_SCREEN_REQUEST_CONFIG = 1 次重试（默认 3 次）', () => {
    expect(FIRST_SCREEN_REQUEST_CONFIG).toEqual(FIRST_SCREEN)
  })

  it('monitoring 五个仪表盘接口都带首屏预算', async () => {
    await getLLMStats()
    expect(getSpy).toHaveBeenLastCalledWith('/monitoring/llm-stats', FIRST_SCREEN)

    await getAdminSummary()
    expect(getSpy).toHaveBeenLastCalledWith('/monitoring/admin-summary', FIRST_SCREEN)

    await getOrgSummary()
    expect(getSpy).toHaveBeenLastCalledWith('/monitoring/org-summary', FIRST_SCREEN)

    await getRAGStats()
    expect(getSpy).toHaveBeenLastCalledWith('/knowledge/rag-stats', FIRST_SCREEN)

    await getMonitoringHealth()
    expect(getSpy).toHaveBeenLastCalledWith('/monitoring/health', FIRST_SCREEN)
  })

  it('共享接口在仪表盘调用点显式传入首屏预算（不影响其它页面默认行为）', async () => {
    await getAuditLogs({ limit: 10 }, FIRST_SCREEN_REQUEST_CONFIG)
    expect(getSpy).toHaveBeenLastCalledWith('/users/audit-logs', { params: { limit: 10 }, ...FIRST_SCREEN })

    await getUserStats(FIRST_SCREEN_REQUEST_CONFIG)
    expect(getSpy).toHaveBeenLastCalledWith('/users/stats', FIRST_SCREEN)

    await getConversations(undefined, FIRST_SCREEN_REQUEST_CONFIG)
    expect(getSpy).toHaveBeenLastCalledWith('/chat/conversations', { params: undefined, ...FIRST_SCREEN })
  })

  it('不传预算时保持默认（共享接口其它调用点不受影响）', async () => {
    await getAuditLogs({ limit: 10 })
    expect(getSpy).toHaveBeenLastCalledWith('/users/audit-logs', { params: { limit: 10 } })

    await getUserStats()
    expect(getSpy).toHaveBeenLastCalledWith('/users/stats', undefined)

    await getConversations()
    expect(getSpy).toHaveBeenLastCalledWith('/chat/conversations', { params: undefined })
  })
})
