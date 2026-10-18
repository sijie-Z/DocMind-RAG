/**
 * E14/D1 判别用例：监控页错误如实 + 双弹收敛。
 *
 * 修前现场：
 * - loadData catch 拼接 error.message（axios 黑话「Request failed with status
 *   code 500」/「Network Error」）同时上屏到 toast 与内联 n-result；
 * - 请求未带 X-Silent-Error → 响应拦截器再弹全局 toast（同一失败三处提示）；
 * - fetchAuditLogs 失败写死中文 toast 且同样被拦截器二次弹。
 *
 * 修复口径：统一 getErrorMessage（5xx/传输层回退 i18n）；加载失败只保留内联
 * 错误态（标题/描述/重试全走词条），不再弹 toast；审计日志失败只保留一条
 * i18n toast，请求带 X-Silent-Error 关闭全局重复提示。
 *
 * 变异点：
 * - 恢复 error.message 拼接 → 「无黑话」用例红；
 * - 加载失败又弹 toast → 「单一表面」用例红；
 * - 审计请求不带 silent 头 → 「X-Silent-Error」用例红。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, enableAutoUnmount } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import naive from 'naive-ui'

import MonitoringView from '../index.vue'
import { getMonitoringDashboard, getMonitoringAlerts, getMetrics } from '@/api/monitoring'
import { getAuditLogs } from '@/api/user'
import zh from '@/locales/zh'

const { messageSpies, chartInstances } = vi.hoisted(() => ({
  messageSpies: { error: vi.fn(), info: vi.fn(), success: vi.fn(), warning: vi.fn(), loading: vi.fn() },
  chartInstances: [] as Array<{ setOption: ReturnType<typeof vi.fn> }>,
}))

vi.mock('@/utils/message', () => ({ useDedupedMessage: () => messageSpies }))

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
  default: { get: vi.fn().mockResolvedValue({ data: { status: 'active', enabled_channels: [] } }) },
  FIRST_SCREEN_REQUEST_CONFIG: {},
}))

const SlotStub = (name: string) =>
  defineComponent({ name, setup(_, { slots }) { return () => h('div', slots.default?.()) } })

const i18n = createI18n({ legacy: false, locale: 'zh', missingWarn: false, fallbackWarn: false, messages: { zh } })

function mountView() {
  return mount(MonitoringView, {
    global: {
      plugins: [naive, i18n],
      // 数据表与 echarts 容器与本用例无关，轻量桩
      stubs: { DataTable: SlotStub('DataTable') },
    },
  })
}

function axiosError(status: number | undefined, msg: string) {
  return Object.assign(new Error(msg), {
    isAxiosError: true,
    config: {},
    ...(status ? { response: { status, data: {} } } : {}),
  })
}

const dashboardFixture = () => ({
  data: {
    current: {
      system: { cpu_percent: 20, memory_percent: 50, disk_usage: {}, process_count: 100, load_average: [] },
      application: { active_connections: 1, request_count: 10, error_count: 0, response_time: 12, elasticsearch_health: 'green' },
      knowledge_base: { total_documents: 1, total_chunks: 5, index_size_mb: 0.1, search_requests: 10 },
    },
    trends: { system: [], application: [], knowledge_base: [] },
    alerts: [],
    timestamp: Math.floor(Date.now() / 1000),
  },
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

describe('MonitoringView · 加载失败如实且单一表面（E14/D1）', () => {
  it('500：不展示 axios 黑话，内联错误态（词条标题/描述/重试）可见，且不弹 toast', async () => {
    vi.mocked(getMonitoringDashboard).mockRejectedValueOnce(
      axiosError(500, 'Request failed with status code 500'),
    )
    const wrapper = mountView()
    await flushPromises()
    await nextTick()

    const text = wrapper.text()
    expect(text).not.toContain('Request failed')
    expect(text).not.toContain('Status code')
    // 内联错误态：标题 + 兜底描述 + 重试按钮
    expect(text).toContain('加载监控数据失败')
    expect(text).toContain('请检查网络或稍后重试')
    expect(text).toContain('重试')
    // 错误时内容区整体隐藏（不带着旧数字装作正常）
    expect(text).not.toContain('系统状态')
    // 单一表面：不再叠加 toast（全局拦截器由 silent 头关闭）
    expect(messageSpies.error).not.toHaveBeenCalled()
  })

  it('Network Error：传输层黑话不上屏，回退 i18n 文案', async () => {
    vi.mocked(getMonitoringDashboard).mockRejectedValueOnce(axiosError(undefined, 'Network Error'))
    const wrapper = mountView()
    await flushPromises()
    await nextTick()

    expect(wrapper.text()).not.toContain('Network Error')
    expect(wrapper.text()).toContain('加载监控数据失败')
  })

  it('点击重试：重新拉取成功 → 错误态消失、内容恢复', async () => {
    vi.mocked(getMonitoringDashboard).mockRejectedValueOnce(axiosError(500, 'Request failed with status code 500'))
    const wrapper = mountView()
    await flushPromises()
    await nextTick()
    expect(wrapper.text()).toContain('加载监控数据失败')

    const retryBtn = wrapper.findAll('button').find((b) => b.text().includes('重试'))
    expect(retryBtn, '重试按钮应存在').toBeTruthy()
    await retryBtn!.trigger('click')
    await flushPromises()
    await flushPromises()

    expect(wrapper.text()).not.toContain('加载监控数据失败')
    expect(wrapper.text()).toContain('系统状态')
  })

  it('审计日志失败：一条 i18n toast，请求带 X-Silent-Error', async () => {
    vi.mocked(getAuditLogs).mockRejectedValueOnce(axiosError(500, 'Request failed with status code 500'))
    mountView()
    await flushPromises()
    await nextTick()

    expect(messageSpies.error).toHaveBeenCalledTimes(1)
    const shown = String(messageSpies.error.mock.calls[0][0])
    expect(shown).toBe('获取审计日志失败')
    expect(shown).not.toContain('Request failed')

    const [, config] = vi.mocked(getAuditLogs).mock.calls[0]
    expect((config as { headers?: Record<string, string> })?.headers?.['X-Silent-Error']).toBe('1')
  })

  it('失败后再次拉取成功（自动刷新/切时间范围）：错误态自动复位，不会永久隐藏内容', async () => {
    vi.mocked(getMonitoringDashboard).mockRejectedValueOnce(axiosError(500, 'Request failed with status code 500'))
    const wrapper = mountView()
    await flushPromises()
    await nextTick()
    expect(wrapper.text()).toContain('加载监控数据失败')

    // 切时间范围 → onTimeRangeChange → loadData()（不经 refreshData 的预复位）
    const select = wrapper.findComponent({ name: 'Select' })
    expect(select.exists(), '时间范围选择器应存在').toBe(true)
    select.vm.$emit('update:value', '6h')
    await flushPromises()
    await nextTick()

    expect(wrapper.text()).not.toContain('加载监控数据失败')
    expect(wrapper.text()).toContain('系统状态')
  })

  it('并发刷新竞态：先发旧请求迟到成功，不得清除后发失败的错误态', async () => {
    let resolveFirst!: (value: unknown) => void
    vi.mocked(getMonitoringDashboard).mockImplementationOnce(
      () => new Promise((resolve) => { resolveFirst = resolve }) as never,
    )
    const wrapper = mountView()
    await flushPromises()
    await nextTick()

    // 第二次拉取（切时间范围）快速失败
    vi.mocked(getMonitoringDashboard).mockRejectedValueOnce(axiosError(500, 'Request failed with status code 500'))
    const select = wrapper.findComponent({ name: 'Select' })
    select.vm.$emit('update:value', '6h')
    await flushPromises()
    await nextTick()
    expect(wrapper.text()).toContain('加载监控数据失败')

    // 旧请求此时才成功返回：不得把内容恢复、也不得清掉新请求的错误态
    resolveFirst(dashboardFixture())
    await flushPromises()
    await nextTick()

    expect(wrapper.text()).toContain('加载监控数据失败')
    expect(wrapper.text()).not.toContain('系统状态')
  })

  it('并发刷新竞态：先发旧请求迟到失败，不得覆盖后发成功的内容', async () => {
    let rejectFirst!: (reason?: unknown) => void
    vi.mocked(getMonitoringDashboard).mockImplementationOnce(
      () => new Promise((_, reject) => { rejectFirst = reject }) as never,
    )
    const wrapper = mountView()
    await flushPromises()
    await nextTick()

    // 第二次拉取（切时间范围）成功（默认 mock 已 resolved）
    const select = wrapper.findComponent({ name: 'Select' })
    select.vm.$emit('update:value', '6h')
    await flushPromises()
    await nextTick()
    expect(wrapper.text()).toContain('系统状态')

    // 旧请求此时才失败：不得把新请求已恢复的内容重新遮住
    rejectFirst(axiosError(500, 'Request failed with status code 500'))
    await flushPromises()
    await nextTick()

    expect(wrapper.text()).toContain('系统状态')
    expect(wrapper.text()).not.toContain('加载监控数据失败')
  })

  it('并发刷新竞态：旧请求在 metrics 等待窗口内过期，迟到的图表数据不得覆盖新结果', async () => {
    let resolveMetricsFirst!: (value: unknown) => void
    vi.mocked(getMetrics).mockImplementationOnce(
      () => new Promise((resolve) => { resolveMetricsFirst = resolve }) as never,
    )
    const wrapper = mountView()
    await flushPromises()
    await nextTick()

    // 第二次拉取成功并写入一组新的趋势点
    vi.mocked(getMetrics).mockResolvedValueOnce({
      data: { data: [{ timestamp: 1700000000, request_count: 2, response_time: 5 }] },
    } as never)
    const select = wrapper.findComponent({ name: 'Select' })
    select.vm.$emit('update:value', '6h')
    await flushPromises()
    await nextTick()

    // 图表初始化顺序：cpu, memory, request, response
    const requestChart = chartInstances[2]
    const callsAfterNew = requestChart.setOption.mock.calls.length
    expect(callsAfterNew).toBeGreaterThan(0)

    // 旧请求的 metrics 此时才返回（不同数据）——不得再写图表
    resolveMetricsFirst({ data: { data: [{ timestamp: 1600000000, request_count: 99, response_time: 99 }] } })
    await flushPromises()
    await nextTick()

    expect(requestChart.setOption.mock.calls.length).toBe(callsAfterNew)
  })
})
