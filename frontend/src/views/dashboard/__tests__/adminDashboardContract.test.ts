/**
 * 管理仪表盘数据契约判别用例（UI 验收 round2 · 复用挂载基座）。
 *
 * - D1（B5）：hit_rate_trend_7d 实为 8 个 3h 桶覆盖 24h → 轴标签/标题对齐；
 *   伪造回退（llm [1200,…]、rag [0,…,hitRate]）删除 → 诚实空态
 * - D2（C1）、D3（B6）按提交次序在本文件生长
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, enableAutoUnmount } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'

import AdminDashboard from '../admin.vue'
import { getLLMStats, getRAGStats, getAdminSummary, getOrgSummary } from '@/api/monitoring'
import { getAuditLogs } from '@/api/user'
import zh from '@/locales/zh'

const { chartInstances } = vi.hoisted(() => ({
  chartInstances: [] as Array<Record<string, ReturnType<typeof vi.fn>>>,
}))

vi.mock('@/utils/echarts', () => {
  class LinearGradient { constructor() { /* stub */ } }
  return {
    default: {
      init: vi.fn(() => {
        const chart = { setOption: vi.fn(), dispose: vi.fn(), resize: vi.fn() }
        chartInstances.push(chart)
        return chart
      }),
      graphic: { LinearGradient },
    },
  }
})
vi.mock('@/api/monitoring', () => ({
  getAdminSummary: vi.fn(),
  getLLMStats: vi.fn(),
  getRAGStats: vi.fn(),
  getOrgSummary: vi.fn(),
}))
vi.mock('@/api/user', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/user')>()
  return { ...actual, getAuditLogs: vi.fn() }
})

const SlotStub = (name: string) =>
  defineComponent({ name, setup(_, { slots }) { return () => h('div', slots.default?.()) } })

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

function mountView() {
  return mount(AdminDashboard, {
    global: {
      plugins: [i18n],
      mocks: { $router: { push: vi.fn() } },
      stubs: { Tag: SlotStub('Tag') },
    },
  })
}

// 真实形状 fixture
const llmFixture = (overrides: Record<string, unknown> = {}) => ({
  data: {
    total_tokens_today: 288,
    cost_today_usd: 0.0001,
    avg_latency_ms: 212,
    p95_latency_ms: 367,
    request_count_today: 7,
    cost_warning: false,
    token_trend_7d: [10, 20, 30, 40, 50, 60, 70],
    ...overrides,
  },
})

const ragFixture = (overrides: Record<string, unknown> = {}) => ({
  data: {
    hit_rate: 50,
    total_queries_7d: 14,
    hits_with_documents: 7,
    avg_documents_retrieved: 2,
    // 8 个 3h 桶（knowledge.py rag-stats：range(8)×3h 覆盖 24h）
    hit_rate_trend_7d: [10, 20, 30, 40, 50, 60, 70, 80],
    ...overrides,
  },
})

enableAutoUnmount(afterEach)

beforeEach(() => {
  vi.clearAllMocks()
  chartInstances.length = 0
  vi.mocked(getAdminSummary).mockResolvedValue({
    data: { total_users: 3, total_sessions: 3, total_documents: 2, total_organizations: 2, active_users_24h: 0 },
  } as never)
  vi.mocked(getAuditLogs).mockResolvedValue({ data: { success: true, data: { items: [], total: 0 } } } as never)
  vi.mocked(getLLMStats).mockResolvedValue(llmFixture() as never)
  vi.mocked(getRAGStats).mockResolvedValue(ragFixture() as never)
  vi.mocked(getOrgSummary).mockResolvedValue({ data: { organizations: [] } } as never)
})

describe('AdminDashboard · 趋势轴标签与诚实空态（B5）', () => {
  it('RAG 趋势：8×3h 桶 → 8 个 HH:00 标签（不再套 7 个"周一~周日"）；标题改"近 24 小时"', async () => {
    const wrapper = mountView()
    await flushPromises()
    await nextTick()

    // 实例序：[0]=llm [1]=rag
    expect(chartInstances).toHaveLength(2)
    const ragOpt = chartInstances[1].setOption.mock.calls[0][0] as {
      xAxis: { data: string[] }
      series: Array<{ data: number[] }>
    }
    expect(ragOpt.xAxis.data).toHaveLength(8)
    for (const label of ragOpt.xAxis.data) {
      expect(label).toMatch(/^\d{2}:00$/)
    }
    // 8 点全部保留（旧实现只 7 个标签，第 8 点丢失）
    expect(ragOpt.series[0].data).toEqual([10, 20, 30, 40, 50, 60, 70, 80])

    expect(wrapper.text()).toContain('近 24 小时文档检索效果（3 小时桶）')
    expect(wrapper.text()).not.toContain('近 7 天文档检索效果')
  })

  it('LLM token 趋势（7 日桶）保持 7 标签不受影响', async () => {
    const wrapper = mountView()
    await flushPromises()
    await nextTick()

    const llmOpt = chartInstances[0].setOption.mock.calls[0][0] as { xAxis: { data: string[] } }
    expect(llmOpt.xAxis.data).toEqual(['周一', '周二', '周三', '周四', '周五', '周六', '周日'])
  })

  it('趋势为空：不画伪造回退数据（旧 [1200,…] / [0,…,hitRate] 已删），画布写明「暂无数据」', async () => {
    vi.mocked(getLLMStats).mockResolvedValue(llmFixture({ token_trend_7d: [] }) as never)
    vi.mocked(getRAGStats).mockResolvedValue(ragFixture({ hit_rate: 0, hit_rate_trend_7d: [] }) as never)

    const wrapper = mountView()
    await flushPromises()
    await nextTick()

    const llmOpt = chartInstances[0].setOption.mock.calls[0][0] as {
      series: Array<{ data: number[] }>
      title: { subtext: string }
    }
    expect(llmOpt.series[0].data).toEqual([])
    expect(llmOpt.series[0].data).not.toContain(1200)
    expect(llmOpt.title.subtext).toBe('暂无数据')

    const ragOpt = chartInstances[1].setOption.mock.calls[0][0] as {
      series: Array<{ data: number[] }>
      title: { subtext: string }
      xAxis: { data: string[] }
    }
    expect(ragOpt.series[0].data).toEqual([])
    expect(ragOpt.title.subtext).toBe('暂无数据')
    expect(ragOpt.xAxis.data).toEqual([])
  })
})

describe('AdminDashboard · 今日请求真实口径（C1）', () => {
  it('「今日请求」卡 = llm-stats.request_count_today（不再用 total_sessions 顶替）', async () => {
    vi.mocked(getAdminSummary).mockResolvedValue({
      data: { total_users: 3, total_sessions: 3, total_documents: 2, total_organizations: 2, active_users_24h: 0 },
    } as never)
    vi.mocked(getLLMStats).mockResolvedValue(llmFixture({ request_count_today: 777 }) as never)

    const wrapper = mountView()
    await flushPromises()
    await nextTick()

    // 卡序：totalUsers / totalSessions / totalDocs / activeUsers / todayRequests / systemStatus
    const numbers = wrapper.findAll('.text-2xl')
    expect(numbers[1].text()).toBe('3') // 总会话数 = total_sessions
    expect(numbers[4].text()).toBe('777') // 今日请求 = request_count_today（旧实现 = 3 顶替）
  })
})
