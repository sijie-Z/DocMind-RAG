/**
 * 监控页数据契约判别用例（UI 验收 round2 · 复用挂载基座）。
 *
 * - M1（B3）：活动告警卡秒/毫秒单位漂移 —— 恒 0 且恒「正常」，与下方表格
 *   active 行同屏矛盾（A_sweep A4 实锤）。本提交覆盖。
 * - M2（B2/B4）、M3（B1）按提交次序在本文件生长。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, enableAutoUnmount } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import { NMessageProvider } from 'naive-ui'

import MonitoringView from '../index.vue'
import { getMonitoringDashboard, getMonitoringAlerts, getMetrics } from '@/api/monitoring'
import { getAuditLogs } from '@/api/user'
import request from '@/utils/request'
import zh from '@/locales/zh'

// echarts 实例捕获（jsdom 无 canvas；M2 将断言 setOption 数据映射）
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
}))
vi.mock('@/api/user', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/user')>()
  return { ...actual, getAuditLogs: vi.fn() }
})
vi.mock('@/utils/request', () => ({
  default: {
    get: vi.fn().mockResolvedValue({ data: { status: 'active', enabled_channels: ['email', 'system'] } }),
  },
}))

const SlotStub = (name: string) =>
  defineComponent({ name, setup(_, { slots }) { return () => h('div', slots.default?.()) } })

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

const Host = defineComponent({
  render: () => h(NMessageProvider, null, { default: () => h(MonitoringView) }),
})

function mountView() {
  return mount(Host, {
    global: {
      plugins: [i18n],
      stubs: { Tag: SlotStub('Tag') },
    },
  })
}

// 秒级时间戳（后端真实口径：monitoring.py:175-184 int(time.time())）
const nowSec = Math.floor(Date.now() / 1000)

const dashboardFixture = () => ({
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
})

const alertFixture = (level: 'info' | 'warning' | 'critical', timestamp: number) => ({
  alerts: [{ type: 'system', level, message: 'CPU 使用率过高', timestamp, status: 'active' }],
  total: 1,
})

enableAutoUnmount(afterEach)

beforeEach(() => {
  vi.clearAllMocks()
  chartInstances.length = 0
  vi.mocked(getMonitoringDashboard).mockResolvedValue(dashboardFixture() as never)
  vi.mocked(getMonitoringAlerts).mockResolvedValue({ data: { alerts: [], total: 0 } } as never)
  vi.mocked(getMetrics).mockResolvedValue({ data: { data: [] } } as never)
  vi.mocked(getAuditLogs).mockResolvedValue({ data: { success: true, data: { items: [], total: 0 } } } as never)
})

describe('MonitoringView · 活动告警卡单位（B3）', () => {
  it('秒级时间戳的 1 小时内 critical 告警 → 卡显示 1 +「严重」徽标（不再恒 0/恒正常）', async () => {
    vi.mocked(getMonitoringAlerts).mockResolvedValue({
      data: alertFixture('critical', nowSec - 60),
    } as never)

    const wrapper = mountView()
    await flushPromises()
    await nextTick()

    // 活动告警计数（该卡数字是页面唯一 text-3xl）
    expect(wrapper.find('.text-3xl').text()).toBe('1')
    // 旧实现（毫秒阈值）：恒 0 且只出「正常」；修复后出「严重」
    expect(wrapper.text()).toContain('严重')
    expect(wrapper.text()).not.toContain('正常')
    expect(wrapper.text()).toContain('活动告警')
    // i18n 裸键哨兵（monitoring.* 补键后可渲染中文而非键名）
    expect(wrapper.text()).not.toContain('monitoring.')
  })

  it('超过 1 小时的旧告警（秒级）不计入活动告警 → 显示「正常」', async () => {
    vi.mocked(getMonitoringAlerts).mockResolvedValue({
      data: alertFixture('info', nowSec - 7200),
    } as never)

    const wrapper = mountView()
    await flushPromises()
    await nextTick()

    expect(wrapper.find('.text-3xl').text()).toBe('0')
    expect(wrapper.text()).toContain('正常')
    expect(wrapper.text()).not.toContain('严重')
  })
})

describe('MonitoringView · 趋势数据链与诚实空态（B2/B4）', () => {
  const trendPoint = (offsetSec: number, requestCount: number, responseTime: number) => ({
    timestamp: nowSec - offsetSec,
    request_count: requestCount,
    response_time: responseTime,
    error_count: 0,
    active_connections: 1,
    slow_request_count: 0,
    error_rate_percent: 0,
    p95_response_time_ms: 30,
    p99_response_time_ms: 40,
  })

  it('真实 {"data": history[]} → 请求/响应图映射 timestamp*1000 + request_count/response_time', async () => {
    vi.mocked(getMetrics).mockResolvedValue({
      data: { data: [trendPoint(120, 3, 12.5), trendPoint(60, 5, 20.1)] },
    } as never)

    const wrapper = mountView()
    await flushPromises()
    await nextTick()

    // 实例序：[0]=cpu [1]=memory [2]=request [3]=response（documentChart 死链已删）
    expect(chartInstances).toHaveLength(4)

    const requestCalls = chartInstances[2].setOption.mock.calls
    const requestLast = requestCalls[requestCalls.length - 1][0] as {
      series: Array<{ data: unknown[] }>
      title: { subtext: string }
    }
    expect(requestLast.series[0].data).toEqual([
      [(nowSec - 120) * 1000, 3],
      [(nowSec - 60) * 1000, 5],
    ])
    // 有数据时清除「暂无数据」
    expect(requestLast.title.subtext).toBe('')

    const respCalls = chartInstances[3].setOption.mock.calls
    const respLast = respCalls[respCalls.length - 1][0] as { series: Array<{ data: unknown[] }> }
    expect(respLast.series[0].data).toEqual([
      [(nowSec - 120) * 1000, 12.5],
      [(nowSec - 60) * 1000, 20.1],
    ])
  })

  it('空数据（{"data": []}，服务 <2 快照的真实空响应）：四图画布写明「暂无数据」而非白板', async () => {
    const wrapper = mountView()
    await flushPromises()
    await nextTick()

    // CPU/内存：无样本（trends.system=[]）→ 初始与更新后均为诚实空态 + 空序列
    const cpuCalls = chartInstances[0].setOption.mock.calls
    const cpuLast = cpuCalls[cpuCalls.length - 1][0] as {
      title: { subtext: string }
      series: Array<{ data: unknown[] }>
    }
    expect(cpuLast.title.subtext).toBe('暂无数据')
    expect(cpuLast.series[0].data).toEqual([])

    const memCalls = chartInstances[1].setOption.mock.calls
    const memLast = memCalls[memCalls.length - 1][0] as { title: { subtext: string } }
    expect(memLast.title.subtext).toBe('暂无数据')

    // 请求/响应：空响应后最后一次 setOption 仍为「暂无数据」
    const reqCalls = chartInstances[2].setOption.mock.calls
    expect((reqCalls[reqCalls.length - 1][0] as { title: { subtext: string } }).title.subtext).toBe('暂无数据')
    const respCalls = chartInstances[3].setOption.mock.calls
    expect((respCalls[respCalls.length - 1][0] as { title: { subtext: string } }).title.subtext).toBe('暂无数据')
  })

  it('trends.system 真实采样点（af42e32）→ CPU/内存图映射 timestamp×1000 + cpu/memory_percent', async () => {
    vi.mocked(getMonitoringDashboard).mockResolvedValue({
      data: {
        ...dashboardFixture().data,
        trends: {
          // 采样点字段逐字对照 af42e32 实现报告：timestamp(秒)/cpu_percent/memory_percent
          system: [
            { timestamp: nowSec - 120, cpu_percent: 12.5, memory_percent: 71.0 },
            { timestamp: nowSec - 60, cpu_percent: 20.0, memory_percent: 69.5 },
          ],
          application: [],
          knowledge_base: [],
        },
      },
    } as never)

    const wrapper = mountView()
    await flushPromises()
    await nextTick()

    const cpuCalls = chartInstances[0].setOption.mock.calls
    const cpuLast = cpuCalls[cpuCalls.length - 1][0] as {
      series: Array<{ data: unknown[] }>
      title: { subtext: string }
    }
    expect(cpuLast.series[0].data).toEqual([
      [(nowSec - 120) * 1000, 12.5],
      [(nowSec - 60) * 1000, 20.0],
    ])
    // 有数据时清除「暂无数据」（不画假线也不留空态遮罩）
    expect(cpuLast.title.subtext).toBe('')

    const memCalls = chartInstances[1].setOption.mock.calls
    const memLast = memCalls[memCalls.length - 1][0] as { series: Array<{ data: unknown[] }> }
    expect(memLast.series[0].data).toEqual([
      [(nowSec - 120) * 1000, 71.0],
      [(nowSec - 60) * 1000, 69.5],
    ])
  })

  it('负例：旧自造形状 {metrics:[...]} 不再被消费（防回退到契约漂移）', async () => {
    vi.mocked(getMetrics).mockResolvedValue({
      data: { metrics: [{ timestamp: nowSec - 60, request_count: 99, response_time: 1 }] },
    } as never)

    const wrapper = mountView()
    await flushPromises()
    await nextTick()

    const reqCalls = chartInstances[2].setOption.mock.calls
    const last = reqCalls[reqCalls.length - 1][0] as { series: Array<{ data: unknown[] }> }
    // 不存在的 metrics 键不会被误当数据源（诚实空态），更不会画出 99
    expect(last.series[0].data).toEqual([])
  })
})

describe('MonitoringView · 通知服务状态路径（B1）', () => {
  it('单前缀正确路径 /monitoring/notification/status（旧双前缀 /api/monitoring/... 恒 404）', async () => {
    const wrapper = mountView()
    await flushPromises()
    await nextTick()

    const urls = vi.mocked(request.get).mock.calls.map((c) => String(c[0]))
    expect(urls).toContain('/monitoring/notification/status')
    // 不再有任何自带 /api 前缀的原始路径（axios baseURL 已含 /api/v1）
    expect(urls.some((u) => u.startsWith('/api/'))).toBe(false)
  })
})
