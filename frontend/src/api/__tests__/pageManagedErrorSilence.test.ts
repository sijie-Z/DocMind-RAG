/**
 * E14/D1 · 页面自理错误的请求静默头判别。
 *
 * 响应拦截器默认对失败请求弹一次全局 toast；页面再在 catch 里弹一次 → 双 toast。
 * 修复口径（与 api/auth.ts、api/scheduledTask.ts 既有先例一致）：凡页面自己呈现
 * 错误（唯一 toast 或页内错误态 + 重试）的调用点，请求带 `X-Silent-Error: 1`，
 * 全局拦截器闭嘴。
 *
 * 变异点：删掉某个调用点的静默头 / 静默参数未透传 → 对应用例红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

const mocks = vi.hoisted(() => ({
  get: vi.fn().mockResolvedValue({ data: {} }),
  post: vi.fn().mockResolvedValue({ data: {} }),
  put: vi.fn().mockResolvedValue({ data: {} }),
  delete: vi.fn().mockResolvedValue({ data: {} }),
}))
vi.mock('@/utils/request', () => ({ default: mocks }))

import { getKnowledgeBases, deleteKnowledgeBase, batchDeleteKnowledgeBases, rebuildKnowledgeBase } from '../knowledge'
import { getWorkflows, deleteWorkflow } from '../workflow'
import { getPrompts, deletePrompt, createPrompt } from '../prompt'
import { getNotifications, getNotificationSummary, deleteNotification, batchDeleteNotifications } from '../notification'
import { getMonitoringDashboard, getMonitoringAlerts, getMetrics, getNotificationStatus } from '../monitoring'
import { getOrganizationSettingsAudit, updateOrganizationSettings } from '../organization'

const silentHeader = (config: unknown) =>
  (config as { headers?: Record<string, unknown> } | undefined)?.headers?.['X-Silent-Error']

beforeEach(() => {
  vi.clearAllMocks()
})

describe('知识库页：页面管理器错误 → 请求静默', () => {
  it('列表加载（页内错误态 + 重试）带 X-Silent-Error', async () => {
    await getKnowledgeBases({ page: 1, search: 'x' }, { silent: true })
    const [url, config] = mocks.get.mock.calls[0]
    expect(url).toBe('/knowledge/')
    expect(config.params).toEqual({ page: 1, search: 'x' })
    expect(silentHeader(config)).toBe('1')
  })

  it('未要求静默时保持默认（其他调用方仍走全局拦截器提示）', async () => {
    await getKnowledgeBases({ page: 1 })
    expect(silentHeader(mocks.get.mock.calls[0][1])).toBeUndefined()
  })

  it('删除/批删/重建（页面唯一 toast）带 X-Silent-Error', async () => {
    await deleteKnowledgeBase('doc-1', { silent: true })
    expect(silentHeader(mocks.delete.mock.calls[0][1])).toBe('1')

    await batchDeleteKnowledgeBases(['doc-1'], 3, { silent: true })
    const [url, payload, config] = mocks.post.mock.calls[0]
    expect(url).toBe('/knowledge/batch-delete')
    expect(payload).toEqual({ document_ids: ['doc-1'], organization_id: 3 })
    expect(silentHeader(config)).toBe('1')

    await rebuildKnowledgeBase('doc-1', { silent: true })
    expect(silentHeader(mocks.post.mock.calls[1][2])).toBe('1')
  })
})

describe('工作流页：页面管理器错误 → 请求静默', () => {
  it('列表加载 / 删除带 X-Silent-Error', async () => {
    await getWorkflows(undefined, { silent: true })
    expect(silentHeader(mocks.get.mock.calls[0][1])).toBe('1')

    await deleteWorkflow(7, { silent: true })
    expect(silentHeader(mocks.delete.mock.calls[0][1])).toBe('1')
  })
})

describe('提示词页：页面管理器错误 → 请求始终静默（单一错误通道）', () => {
  it('列表 / 创建 / 删除均带 X-Silent-Error', async () => {
    await getPrompts()
    expect(silentHeader(mocks.get.mock.calls[0][1])).toBe('1')

    await createPrompt({ name: 'n', content: 'c' })
    expect(silentHeader(mocks.post.mock.calls[0][2])).toBe('1')

    await deletePrompt(9)
    expect(silentHeader(mocks.delete.mock.calls[0][1])).toBe('1')
  })
})

describe('通知页：页面管理器错误 → 请求静默', () => {
  it('列表 / 汇总 / 删除 / 批删带 X-Silent-Error；默认调用保持拦截器提示', async () => {
    await getNotifications({ limit: 10 }, { silent: true })
    expect(silentHeader(mocks.get.mock.calls[0][1])).toBe('1')

    await getNotificationSummary({ silent: true })
    expect(silentHeader(mocks.get.mock.calls[1][1])).toBe('1')

    await deleteNotification(5, { silent: true })
    expect(silentHeader(mocks.delete.mock.calls[0][1])).toBe('1')

    await batchDeleteNotifications([5, 6], { silent: true })
    const [url, config] = mocks.delete.mock.calls[1]
    expect(url).toBe('/notifications/')
    expect(config.data).toEqual({ ids: [5, 6] })
    expect(silentHeader(config)).toBe('1')

    // 布局铃铛等未要求静默的调用方仍由全局拦截器兜底提示
    await getNotifications()
    expect(silentHeader(mocks.get.mock.calls[2][1])).toBeUndefined()
  })
})

describe('监控页：内联错误态 + 重试 → 主数据请求静默', () => {
  it('dashboard / alerts 带 X-Silent-Error（否则内联错误态与全局 toast 双提示）', async () => {
    await getMonitoringDashboard()
    expect(mocks.get.mock.calls[0][0]).toBe('/monitoring/dashboard')
    expect(silentHeader(mocks.get.mock.calls[0][1])).toBe('1')

    await getMonitoringAlerts()
    expect(mocks.get.mock.calls[1][0]).toBe('/monitoring/alerts')
    expect(silentHeader(mocks.get.mock.calls[1][1])).toBe('1')
  })

  it('metrics 趋势 / 通知服务状态带 X-Silent-Error（失败由页面空态/降级徽标呈现）', async () => {
    await getMetrics('application', '6h')
    const [metricsUrl, metricsConfig] = mocks.get.mock.calls[0]
    expect(metricsUrl).toBe('/monitoring/metrics/application')
    expect(metricsConfig.params).toEqual({ time_range: '6h' })
    expect(silentHeader(metricsConfig)).toBe('1')

    await getNotificationStatus()
    const [statusUrl, statusConfig] = mocks.get.mock.calls[1]
    expect(statusUrl).toBe('/monitoring/notification/status')
    expect(silentHeader(statusConfig)).toBe('1')
  })
})

describe('组织设置页：保存/审计错误由页面自行呈现 → 请求静默', () => {
  it('更新设置 PUT 带 X-Silent-Error（页面唯一 toast）', async () => {
    await updateOrganizationSettings(7, { retrieval_top_k: 3 })
    const [url, payload, config] = mocks.put.mock.calls[0]
    expect(url).toBe('/organizations/7/settings')
    expect(payload).toEqual({ retrieval_top_k: 3 })
    expect(silentHeader(config)).toBe('1')
  })

  it('审计拉取 GET 带 X-Silent-Error（审计区内联降级提示）', async () => {
    await getOrganizationSettingsAudit(7)
    expect(mocks.get.mock.calls[0][0]).toBe('/organizations/7/settings/audit')
    expect(silentHeader(mocks.get.mock.calls[0][1])).toBe('1')
  })
})
