/**
 * 仪表盘分块加载判别用例（E6 · 体验批）。
 *
 * 判别点：
 * - 慢接口下各区块独立渲染：单个 RAG 请求挂起时，KPI/组织/审计（admin）
 *   或 KPI/最近会话（user）已用真实数据渲染，只有 RAG 区块停留在骨架；
 *   旧实现是整页 n-spin，任何一个接口慢 → 整屏空白。
 * - 首屏并行：挂载瞬间六路（admin）/三路（user）请求全部发出。
 *   旧实现把 getRAGStats/getOrgSummary 串行排在 Promise.all 之后，
 *   前四个不返回就永远不会发出后两个。
 * - 区块错误态：单接口失败只让本区块显示「加载失败 + 重试」，其余区块照常渲染。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, enableAutoUnmount } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'

import AdminDashboard from '../admin.vue'
import UserDashboard from '../user.vue'
import { getLLMStats, getRAGStats, getAdminSummary, getOrgSummary, getMonitoringHealth, getLLMStatus } from '@/api/monitoring'
import { getAuditLogs, getUserStats } from '@/api/user'
import { getConversations } from '@/api/conversation'
import { seedDemoData } from '@/api/demo'
import zh from '@/locales/zh'
import en from '@/locales/en'

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
  getLLMStatus: vi.fn(),
  getOrgSummary: vi.fn(),
  getMonitoringHealth: vi.fn(),
}))
vi.mock('@/api/user', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/user')>()
  return { ...actual, getAuditLogs: vi.fn(), getUserStats: vi.fn() }
})
vi.mock('@/api/conversation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/conversation')>()
  return { ...actual, getConversations: vi.fn() }
})
vi.mock('@/api/demo', () => ({ seedDemoData: vi.fn() }))
// user.vue 的 useMessage 需要 message provider；单元测试里用最小桩替换
vi.mock('naive-ui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('naive-ui')>()
  return {
    ...actual,
    useMessage: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }),
  }
})

const SlotStub = (name: string) =>
  defineComponent({ name, setup(_, { slots }) { return () => h('div', slots.default?.()) } })

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })
const i18nEn = createI18n({ legacy: false, locale: 'en', messages: { en } })

function mountAdmin() {
  return mount(AdminDashboard, {
    global: {
      plugins: [i18n],
      mocks: { $router: { push: vi.fn() } },
      stubs: { Tag: SlotStub('Tag') },
    },
  })
}

function mountUser() {
  return mount(UserDashboard, {
    global: {
      plugins: [i18n],
      mocks: { $router: { push: vi.fn() } },
      stubs: { Tag: SlotStub('Tag') },
    },
  })
}

function mountUserEn() {
  return mount(UserDashboard, {
    global: {
      plugins: [i18nEn],
      mocks: { $router: { push: vi.fn() } },
      stubs: { Tag: SlotStub('Tag') },
    },
  })
}

/** 永不 resolve —— 模拟"最慢接口" */
const pendingForever = () => new Promise<never>(() => {})

const llmFixture = () => ({
  data: {
    total_tokens_today: 288,
    cost_today_usd: 0.0001,
    avg_latency_ms: 212,
    p95_latency_ms: 367,
    request_count_today: 7,
    cost_warning: false,
    token_trend_7d: [10, 20, 30, 40, 50, 60, 70],
  },
})
const ragFixture = () => ({
  data: {
    hit_rate: 50,
    total_queries_7d: 14,
    hits_with_documents: 7,
    avg_documents_retrieved: 2,
    hit_rate_trend_7d: [10, 20, 30, 40, 50, 60, 70, 80],
  },
})

enableAutoUnmount(afterEach)

beforeEach(() => {
  vi.clearAllMocks()
  setActivePinia(createPinia())
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
  vi.mocked(getLLMStatus).mockResolvedValue({
    data: { success: true, data: { configured: true, provider: 'deepseek', model: 'deepseek-v4-flash', source: 'server' } },
  } as never)

  vi.mocked(getUserStats).mockResolvedValue({
    data: {
      conversation_count: 12, message_count: 34, file_count: 2,
      knowledge_count: 1, storage_used: 1024, activity_trend: [1, 2, 3, 4, 5, 6, 7],
    },
  } as never)
  vi.mocked(getConversations).mockResolvedValue({
    data: {
      success: true,
      data: {
        data: [{ id: 'c1', title: '会话一', created_at: '', updated_at: new Date().toISOString(), message_count: 2 }],
        total: 1, page: 1, page_size: 20,
      },
    },
  } as never)
  vi.mocked(seedDemoData).mockResolvedValue({ data: { success: false } } as never)
})

describe('AdminDashboard · 分块骨架（E6）', () => {
  it('慢 RAG 接口不锁整屏：KPI/组织/审计先渲染，仅 RAG 区块保持骨架', async () => {
    vi.mocked(getRAGStats).mockReturnValue(pendingForever() as never)

    const wrapper = mountAdmin()
    await flushPromises()

    // RAG 区块还在骨架（本接口未返回）
    expect(wrapper.find('[data-testid="rag-skeleton"]').exists()).toBe(true)
    // 其它区块已用真实数据替换
    expect(wrapper.find('[data-testid="kpi-skeleton"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="org-skeleton"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="audit-skeleton"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('总用户数')
    expect(wrapper.text()).toContain('DeepSeek 费率')
    // 旧实现整页 spinner：任何内容都不可见（判别基线）
    expect(wrapper.find('.n-spin').exists()).toBe(false)
  })

  it('串行→并行：挂载时首屏六路请求全部发出（RAG/组织概览不再等前四个返回）', async () => {
    // 全部挂起：旧实现里 getRAGStats/getOrgSummary 永远等不到 Promise.all 完成
    vi.mocked(getAdminSummary).mockReturnValue(pendingForever() as never)
    vi.mocked(getAuditLogs).mockReturnValue(pendingForever() as never)
    vi.mocked(getLLMStats).mockReturnValue(pendingForever() as never)
    vi.mocked(getMonitoringHealth).mockReturnValue(pendingForever() as never)
    vi.mocked(getRAGStats).mockReturnValue(pendingForever() as never)
    vi.mocked(getOrgSummary).mockReturnValue(pendingForever() as never)

    mountAdmin()
    await nextTick()

    expect(getAdminSummary).toHaveBeenCalledTimes(1)
    expect(getAuditLogs).toHaveBeenCalledTimes(1)
    expect(getLLMStats).toHaveBeenCalledTimes(1)
    expect(getMonitoringHealth).toHaveBeenCalledTimes(1)
    expect(getRAGStats).toHaveBeenCalledTimes(1)
    expect(getOrgSummary).toHaveBeenCalledTimes(1)
  })

  it('单区块失败只影响本区块：RAG 显示错误态与重试，其余区块正常', async () => {
    vi.mocked(getRAGStats).mockRejectedValue(new Error('boom'))

    const wrapper = mountAdmin()
    await flushPromises()

    expect(wrapper.find('[data-testid="rag-error"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="rag-error"]').text()).toContain('重试')
    expect(wrapper.find('[data-testid="kpi-skeleton"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('总用户数')
  })

  it('RAG 区块重试按钮只重发本区块请求', async () => {
    vi.mocked(getRAGStats).mockRejectedValue(new Error('boom'))

    const wrapper = mountAdmin()
    await flushPromises()
    expect(getRAGStats).toHaveBeenCalledTimes(1)

    await wrapper.find('[data-testid="rag-retry"]').trigger('click')
    await flushPromises()

    expect(getRAGStats).toHaveBeenCalledTimes(2)
    // 其余区块不被重新请求
    expect(getAdminSummary).toHaveBeenCalledTimes(1)
  })
})

describe('UserDashboard · 分块骨架（E6）', () => {
  it('慢 RAG 接口不锁整屏：KPI 与最近会话先渲染，仅 RAG 区块骨架', async () => {
    vi.mocked(getRAGStats).mockReturnValue(pendingForever() as never)

    const wrapper = mountUser()
    await flushPromises()

    expect(wrapper.find('[data-testid="rag-skeleton"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="kpi-skeleton"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="conv-skeleton"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="chart-skeleton"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('12')
    expect(wrapper.text()).toContain('会话一')
    expect(wrapper.find('.n-spin').exists()).toBe(false)
  })

  it('慢用户统计不阻塞 RAG/最近会话：仅 KPI 与趋势图保持骨架', async () => {
    vi.mocked(getUserStats).mockReturnValue(pendingForever() as never)

    const wrapper = mountUser()
    await flushPromises()

    expect(wrapper.find('[data-testid="kpi-skeleton"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="chart-skeleton"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="rag-skeleton"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="conv-skeleton"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('会话一')
  })

  it('首屏三路请求挂载时并行发出', async () => {
    vi.mocked(getUserStats).mockReturnValue(pendingForever() as never)
    vi.mocked(getConversations).mockReturnValue(pendingForever() as never)
    vi.mocked(getRAGStats).mockReturnValue(pendingForever() as never)

    mountUser()
    await nextTick()

    expect(getUserStats).toHaveBeenCalledTimes(1)
    expect(getConversations).toHaveBeenCalledTimes(1)
    expect(getRAGStats).toHaveBeenCalledTimes(1)
  })

  it('en 语言下活动趋势星期为英文、会话计数走词条（旧实现硬编码中文，先红）', async () => {
    const wrapper = mountUserEn()
    await flushPromises()

    const opt = chartInstances[chartInstances.length - 1].setOption.mock.calls[0][0] as {
      xAxis: { data: string[] }
    }
    expect(opt.xAxis.data).toEqual(['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'])
    expect(wrapper.text()).toContain('Recent conversations: 1')
    expect(wrapper.text()).not.toContain('最近')
  })
})
