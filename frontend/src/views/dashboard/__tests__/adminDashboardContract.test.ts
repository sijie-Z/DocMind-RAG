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
import { getLLMStats, getRAGStats, getAdminSummary, getOrgSummary, getMonitoringHealth } from '@/api/monitoring'
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
  getMonitoringHealth: vi.fn(),
}))
vi.mock('@/api/user', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/user')>()
  return { ...actual, getAuditLogs: vi.fn() }
})

const SlotStub = (name: string) =>
  defineComponent({ name, setup(_, { slots }) { return () => h('div', slots.default?.()) } })

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

type I18nInstance = ReturnType<typeof createI18n>

function mountView(i18nInstance: I18nInstance = i18n as unknown as I18nInstance) {
  return mount(AdminDashboard, {
    global: {
      plugins: [i18nInstance],
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
  vi.mocked(getMonitoringHealth).mockResolvedValue({
    data: { status: 'healthy', timestamp: '', services: { database: 'connected', redis: 'connected', elasticsearch: 'green' } },
  } as never)
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

describe('AdminDashboard · 健康卡接真实 ES 状态（B6/J4）', () => {
  it('degraded + ES red → 卡显示「异常」，ES 状态词条化为中文（不裸露 red）', async () => {
    vi.mocked(getMonitoringHealth).mockResolvedValue({
      data: {
        status: 'degraded',
        timestamp: '',
        services: { database: 'connected', redis: 'connected', elasticsearch: 'red' },
      },
    } as never)

    const wrapper = mountView()
    await flushPromises()
    await nextTick()

    const text = wrapper.text()
    expect(text).toContain('异常')
    expect(text).toContain('Elasticsearch: 异常')
    expect(text).not.toMatch(/\b(green|yellow|red|unknown|disconnected)\b/)
    expect(text).not.toContain('ES: ok')
    expect(text).not.toContain('正常')
  })

  it('healthy + ES green →「正常」词条（真值而非硬编码 ok）', async () => {
    const wrapper = mountView()
    await flushPromises()
    await nextTick()

    const text = wrapper.text()
    expect(text).toContain('Elasticsearch: 正常')
    expect(text).not.toMatch(/\b(green|red|unknown|disconnected)\b/)
    expect(text).not.toContain('ES: ok')
  })
})

describe('AdminDashboard · J4 状态词走词条 + RAG 卡填充', () => {
  const sentinelMessages = {
    zh: {
      ...zh,
      dashboard: {
        ...zh.dashboard,
        esLabel: '搜索引擎',
        esHealthYellow: '黄色哨兵',
        esHealthUnknown: '未知哨兵',
        esHealthDisconnected: '断开哨兵',
      },
    },
  }
  const sentinelI18n = createI18n({ legacy: false, locale: 'zh', messages: sentinelMessages })

  it('ES 状态值经 i18n 词条渲染（自定义词条哨兵生效，证明非硬编码映射）', async () => {
    vi.mocked(getMonitoringHealth).mockResolvedValue({
      data: {
        status: 'healthy',
        timestamp: '',
        services: { database: 'connected', redis: 'connected', elasticsearch: 'yellow' },
      },
    } as never)

    const wrapper = mountView(sentinelI18n as unknown as I18nInstance)
    await flushPromises()
    await nextTick()

    expect(wrapper.text()).toContain('搜索引擎: 黄色哨兵')
  })

  it('真实值域逐一映射：green/yellow/red/unknown/disconnected 均无英文状态词裸露', async () => {
    const cases: Array<[string, string]> = [
      ['green', '正常'],
      ['yellow', '告警'],
      ['red', '异常'],
      ['unknown', '未知'],
      ['disconnected', '未连接'],
    ]
    for (const [raw, localized] of cases) {
      vi.mocked(getMonitoringHealth).mockResolvedValue({
        data: {
          status: raw === 'green' ? 'healthy' : 'degraded',
          timestamp: '',
          services: { database: 'connected', redis: 'connected', elasticsearch: raw },
        },
      } as never)

      const wrapper = mountView()
      await flushPromises()
      await nextTick()

      const text = wrapper.text()
      expect(text).toContain(`Elasticsearch: ${localized}`)
      expect(text).not.toMatch(/\b(green|yellow|red|unknown|disconnected)\b/)
    }
  })

  it('RAG 卡纵向填充：卡片与主体为 flex 列、图区 flex-1（消除等高栅格下的空白块）', async () => {
    const wrapper = mountView()
    await flushPromises()
    await nextTick()

    const card = wrapper.get('[data-testid="rag-card"]')
    expect(card.classes()).toContain('flex')
    expect(card.classes()).toContain('flex-col')

    const chart = wrapper.get('[data-testid="rag-chart"]')
    expect(chart.classes()).toContain('flex-1')
    expect(chart.classes()).not.toContain('h-28')
  })
})

describe('AdminDashboard · 总会话环比真值 & 成本费率对齐（W2-T7）', () => {
  it('写死的「↑ +12%（对比上周）」不再出现；无环比基线时显示「上周无数据」', async () => {
    const wrapper = mountView()
    await flushPromises()
    await nextTick()

    // before 实测在屏的假趋势（total_sessions=3>0 → 恒 '+12%'）必须消失
    expect(wrapper.text()).not.toContain('+12%')
    expect(wrapper.text()).toContain('上周无数据')
  })

  it('真实环比（上升）：本周 6 / 上周 4 → ↑ +50% 对比上周', async () => {
    vi.mocked(getAdminSummary).mockResolvedValue({
      data: {
        total_users: 3, total_sessions: 10, total_documents: 2, total_organizations: 2,
        active_users_24h: 0, sessions_this_week: 6, sessions_last_week: 4,
      },
    } as never)

    const wrapper = mountView()
    await flushPromises()
    await nextTick()

    expect(wrapper.text()).toContain('+50%')
    expect(wrapper.text()).toContain('对比上周')
    expect(wrapper.text()).not.toContain('+12%')
    expect(wrapper.text()).not.toContain('上周无数据')
  })

  it('真实环比（下降）：本周 3 / 上周 6 → ↓ -50%', async () => {
    vi.mocked(getAdminSummary).mockResolvedValue({
      data: {
        total_users: 3, total_sessions: 9, total_documents: 2, total_organizations: 2,
        active_users_24h: 0, sessions_this_week: 3, sessions_last_week: 6,
      },
    } as never)

    const wrapper = mountView()
    await flushPromises()
    await nextTick()

    expect(wrapper.text()).toContain('↓')
    expect(wrapper.text()).toContain('-50%')
  })

  it('成本卡费率标签对齐真实栈：DeepSeek（不再是 GPT-4o-mini）', async () => {
    const wrapper = mountView()
    await flushPromises()
    await nextTick()

    expect(wrapper.text()).toContain('DeepSeek 费率')
    expect(wrapper.text()).not.toContain('GPT-4o-mini')
  })
})
