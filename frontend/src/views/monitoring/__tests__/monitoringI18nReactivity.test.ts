/**
 * 监控页 i18n 真·响应式判别用例。
 *
 * 修前：副标题硬编码中文；审计列头是 setup 期常量；ECharts 标题/系列名在
 * 挂载时一次性写入，切换语言后画布仍是中文。
 * 修复：副标题走词条、auditColumns 改 computed、locale 变化时 setOption
 * 重设标题/系列名（合并更新，不清空已绘制数据）。
 *
 * 变异点：
 *  - auditColumns 退回常量 → 英文列头断言红；
 *  - 删除 watch(locale) 的 relocalizeCharts → 图表英文标题断言红。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, enableAutoUnmount } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import naive, { NMessageProvider } from 'naive-ui'

import MonitoringView from '../index.vue'
import zh from '@/locales/zh'
import en from '@/locales/en'

const { chartInstances } = vi.hoisted(() => ({
  chartInstances: [] as Array<Record<string, ReturnType<typeof vi.fn>>>,
}))

vi.mock('@/utils/echarts', () => ({
  default: {
    init: vi.fn(() => {
      const chart = { setOption: vi.fn(), dispose: vi.fn(), resize: vi.fn() }
      chartInstances.push(chart)
      return chart
    }),
  },
}))

vi.mock('@/api/monitoring', () => ({
  getMonitoringDashboard: vi.fn(),
  getMonitoringAlerts: vi.fn(),
  getMetrics: vi.fn(),
  getNotificationStatus: vi.fn(),
}))

vi.mock('@/api/user', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/user')>()
  return { ...actual, getAuditLogs: vi.fn() }
})

vi.mock('@/utils/request', () => ({
  default: { get: vi.fn().mockResolvedValue({ data: { status: 'active', enabled_channels: [] } }) },
}))

const i18n = createI18n({
  legacy: false,
  locale: 'zh',
  fallbackLocale: 'en',
  messages: { zh, en },
})

const Host = defineComponent({
  render: () => h(NMessageProvider, null, { default: () => h(MonitoringView) }),
})

const nowSec = Math.floor(Date.now() / 1000)

function mountView() {
  return mount(Host, { global: { plugins: [naive, i18n] } })
}

function headers(wrapper: ReturnType<typeof mount>) {
  return wrapper.findAll('th').map((th) => th.text())
}

enableAutoUnmount(afterEach)

beforeEach(async () => {
  vi.clearAllMocks()
  chartInstances.length = 0
  i18n.global.locale.value = 'zh'

  const { getMonitoringDashboard, getMonitoringAlerts, getMetrics, getNotificationStatus } =
    await import('@/api/monitoring')
  const { getAuditLogs } = await import('@/api/user')

  vi.mocked(getMonitoringDashboard).mockResolvedValue({
    data: {
      current: {
        system: { cpu_percent: 20, memory_percent: 50, disk_usage: { C: 50 }, process_count: 100, load_average: [] },
        application: { active_connections: 1, request_count: 10, error_count: 0, response_time: 12, elasticsearch_health: 'green' },
        knowledge_base: { total_documents: 1, total_chunks: 5, index_size_mb: 0.1, search_requests: 10 },
      },
      trends: { system: [], application: [], knowledge_base: [] },
      alerts: [],
      timestamp: nowSec,
    },
  } as never)
  vi.mocked(getMonitoringAlerts).mockResolvedValue({ data: { alerts: [], total: 0 } } as never)
  vi.mocked(getMetrics).mockResolvedValue({ data: { data: [] } } as never)
  vi.mocked(getNotificationStatus).mockResolvedValue({
    data: { status: 'active', enabled_channels: ['system'] },
  } as never)
  vi.mocked(getAuditLogs).mockResolvedValue({
    data: { success: true, data: { items: [], total: 0 } },
  } as never)
})

describe('监控页 · 语言切换后文案实时更新', () => {
  it('副标题与审计列头随 locale 切换（computed 列）', async () => {
    const wrapper = mountView()
    await flushPromises()
    await nextTick()

    expect(wrapper.text()).toContain('实时监控系统运行状态')
    expect(headers(wrapper)).toEqual(expect.arrayContaining(['用户', '动作', '目标', '详情', '时间']))

    i18n.global.locale.value = 'en'
    await nextTick()
    await flushPromises()

    expect(wrapper.text()).toContain('Real-time system status and performance')
    expect(headers(wrapper)).toEqual(
      expect.arrayContaining(['User', 'Action', 'Target', 'Details', 'Time']),
    )
    expect(headers(wrapper), '切换后审计列头残留中文').not.toContain('用户')
  })

  it('图表标题随 locale 重设（ECharts 不响应 i18n）', async () => {
    mountView()
    await flushPromises()
    await nextTick()

    const titles = () =>
      chartInstances.flatMap((c) =>
        c.setOption.mock.calls.map((call) => (call[0] as { title?: { text?: string } })?.title?.text),
      )
    expect(titles()).toContain('CPU 使用率 (%)')

    i18n.global.locale.value = 'en'
    await nextTick()
    await flushPromises()

    expect(titles(), '切 en 后图表标题未重设').toContain('CPU Usage (%)')
  })
})
