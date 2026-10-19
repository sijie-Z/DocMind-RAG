/**
 * T8 · onboarding 刷新期间不消失（issue #416，P2）。
 *
 * 现状：父级把 statsState === 'ready' && !statsDegraded 传给 statsReady，
 * 点「重新检查」→ loadStats() 先置 loading → 整块清单被 v-if 卸载；
 * 刷新失败置 error → 清单与块内重试入口一起消失，再也回不来。
 *
 * 修复口径（本文件钉住）：
 * 1. 首次 ready 后引入「曾就绪」记忆位：刷新挂起/失败期间清单保持挂载，
 *    刷新挂起时显示局部 loading，块内「重新检查」仍可用；
 * 2. 首次加载失败（从未 ready）时仍不显示清单（维持现状，含降级数据）；
 * 3. 刷新失败后再次重试成功，清单与进度随真实数据更新。
 *
 * 变异点：把 stats-ready 改回「当前 ready」→ 挂起/失败用例红；
 * 去掉局部 loading 指示 → 对应用例红；首次失败仍挂载 → 首失败用例红。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, enableAutoUnmount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'

import UserDashboard from '../user.vue'
import { getUserStats } from '@/api/user'
import { getConversations } from '@/api/conversation'
import { getRAGStats, getLLMStatus } from '@/api/monitoring'
import { seedDemoData } from '@/api/demo'
import zh from '@/locales/zh'

vi.mock('@/utils/echarts', () => ({
  default: {
    init: vi.fn(() => ({ setOption: vi.fn(), dispose: vi.fn(), resize: vi.fn() })),
    graphic: { LinearGradient: class LinearGradient {} },
  },
}))
vi.mock('@/api/monitoring', () => ({
  getRAGStats: vi.fn(),
  getLLMStatus: vi.fn(),
}))
vi.mock('@/api/user', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/user')>()
  return { ...actual, getUserStats: vi.fn() }
})
vi.mock('@/api/conversation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/conversation')>()
  return { ...actual, getConversations: vi.fn() }
})
vi.mock('@/api/demo', () => ({ seedDemoData: vi.fn() }))
vi.mock('naive-ui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('naive-ui')>()
  return {
    ...actual,
    useMessage: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }),
  }
})

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

const statsFixture = (overrides: Record<string, number> = {}) => ({
  data: {
    conversation_count: 0,
    message_count: 0,
    file_count: 0,
    knowledge_count: 0,
    storage_used: 0,
    activity_trend: [0, 0, 0, 0, 0, 0, 0],
    ...overrides,
  },
})

const conversationsFixture = () => ({
  data: { success: true, data: { data: [], total: 0, page: 1, page_size: 20 } },
})

const llmStatusFixture = (configured: boolean) => ({
  data: { success: true, data: { configured, provider: 'deepseek', model: 'deepseek-v4-flash', source: 'server' } },
})

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

function mountView(userId: number) {
  localStorage.setItem(
    'user_info',
    JSON.stringify({
      id: userId,
      username: `tester-${userId}`,
      email: `tester-${userId}@example.com`,
      isAdmin: false,
      role: 'user',
      createdAt: '',
      organizations: [],
    }),
  )
  const pinia = createPinia()
  setActivePinia(pinia)
  return mount(UserDashboard, {
    global: {
      plugins: [pinia, i18n],
      mocks: { $router: { push: vi.fn() } },
    },
  })
}

const checklist = (wrapper: ReturnType<typeof mountView>) =>
  wrapper.find('[data-testid="onboarding-checklist"]')
const refreshBtn = (wrapper: ReturnType<typeof mountView>) =>
  wrapper.find('[data-testid="onboarding-refresh"]')
const refreshing = (wrapper: ReturnType<typeof mountView>) =>
  wrapper.find('[data-testid="onboarding-refreshing"]')

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  vi.mocked(getUserStats).mockResolvedValue(statsFixture() as never)
  vi.mocked(getConversations).mockResolvedValue(conversationsFixture() as never)
  vi.mocked(getRAGStats).mockResolvedValue({
    data: {
      hit_rate: 0,
      total_queries_7d: 0,
      hits_with_documents: 0,
      avg_documents_retrieved: 0,
      hit_rate_trend_7d: [],
    },
  } as never)
  vi.mocked(getLLMStatus).mockResolvedValue({
    data: { success: true, data: { configured: false, provider: 'deepseek', model: 'deepseek-v4-flash', source: 'server' } },
  } as never)
  vi.mocked(seedDemoData).mockResolvedValue({ data: { success: false } } as never)
})

enableAutoUnmount(afterEach)

describe('T8 · onboarding 刷新期间不消失', () => {
  it('刷新挂起：清单保持挂载、局部 loading 出现、块内重试入口仍可用', async () => {
    vi.mocked(getUserStats).mockResolvedValue(statsFixture({ file_count: 1 }) as never)

    const wrapper = mountView(401)
    await flushPromises()

    expect(checklist(wrapper).exists(), '首次 ready 后应挂载清单').toBe(true)
    expect(wrapper.find('[data-testid="onboarding-progress"]').text()).toContain('1/4')

    // 刷新挂起：模拟「重新检查」后统计接口迟迟不返回
    const pending = deferred<never>()
    vi.mocked(getUserStats).mockReturnValue(pending.promise as never)

    await refreshBtn(wrapper).trigger('click')
    await flushPromises()

    expect(checklist(wrapper).exists(), '刷新挂起期间清单不得卸载').toBe(true)
    expect(refreshing(wrapper).exists(), '刷新挂起期间应显示局部 loading').toBe(true)
    expect(wrapper.find('[data-testid="onboarding-progress"]').text(), '挂起期间应保留上次进度').toContain('1/4')
    expect(refreshBtn(wrapper).exists(), '块内重试入口不得消失').toBe(true)

    // 「仍可用」行为化：统计请求挂起、模型检查已落定时再点一次，确实重新发起统计请求
    const calls = vi.mocked(getUserStats).mock.calls.length
    await refreshBtn(wrapper).trigger('click')
    await flushPromises()
    expect(vi.mocked(getUserStats).mock.calls.length, '统计挂起期间重试按钮应仍可点击').toBe(calls + 1)

    pending.reject(new Error('stats down'))
    await flushPromises()

    expect(checklist(wrapper).exists(), '刷新失败后清单不得消失').toBe(true)
    expect(refreshing(wrapper).exists(), '失败落定后局部 loading 应消失').toBe(false)
    expect(refreshBtn(wrapper).exists(), '失败后块内重试入口应保留').toBe(true)
  })

  it('刷新失败后再次重试成功：清单仍在，进度随真实数据更新', async () => {
    vi.mocked(getUserStats).mockResolvedValue(statsFixture() as never)

    const wrapper = mountView(402)
    await flushPromises()
    expect(checklist(wrapper).exists()).toBe(true)
    expect(wrapper.find('[data-testid="onboarding-progress"]').text()).toContain('0/4')

    vi.mocked(getUserStats).mockRejectedValueOnce(new Error('stats down'))
    await refreshBtn(wrapper).trigger('click')
    await flushPromises()

    expect(checklist(wrapper).exists(), '失败后清单应保留以便重试').toBe(true)
    expect(refreshBtn(wrapper).exists(), '失败后应保留重试入口').toBe(true)

    vi.mocked(getUserStats).mockResolvedValue(statsFixture({ file_count: 2 }) as never)
    await refreshBtn(wrapper).trigger('click')
    await flushPromises()

    expect(checklist(wrapper).exists()).toBe(true)
    expect(wrapper.find('[data-testid="onboarding-progress"]').text()).toContain('1/4')
  })

  it('刷新降级（200 + 全零 + degraded=true）：清单保留上次真实进度，不被伪造 0 覆盖', async () => {
    vi.mocked(getUserStats).mockResolvedValue(statsFixture({ file_count: 1 }) as never)

    const wrapper = mountView(404)
    await flushPromises()
    expect(wrapper.find('[data-testid="onboarding-progress"]').text()).toContain('1/4')

    vi.mocked(getUserStats).mockResolvedValue({
      data: { ...statsFixture().data, degraded: true, reason: '个人统计查询失败，数据暂不可用' },
    } as never)
    await refreshBtn(wrapper).trigger('click')
    await flushPromises()

    expect(checklist(wrapper).exists(), '降级刷新后清单应保持挂载').toBe(true)
    expect(
      wrapper.find('[data-testid="onboarding-progress"]').text(),
      '降级响应的全零不得覆盖上次真实进度',
    ).toContain('1/4')
    expect(refreshing(wrapper).exists(), '降级落定后局部 loading 应消失').toBe(false)
    expect(refreshBtn(wrapper).exists()).toBe(true)
  })

  it('并发统计刷新乱序：旧请求失败不得覆盖新请求的成功', async () => {
    const wrapper = mountView(405)
    await flushPromises()

    const slow = deferred<ReturnType<typeof statsFixture>>()
    const fast = deferred<ReturnType<typeof statsFixture>>()
    vi.mocked(getUserStats)
      .mockReturnValueOnce(slow.promise as never)
      .mockReturnValueOnce(fast.promise as never)

    await refreshBtn(wrapper).trigger('click') // 旧请求
    await refreshBtn(wrapper).trigger('click') // 新请求
    await flushPromises()

    fast.resolve(statsFixture({ file_count: 2 }))
    await flushPromises()
    expect(wrapper.find('[data-testid="kpi-error"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="onboarding-progress"]').text()).toContain('1/4')

    slow.reject(new Error('stale stats failure'))
    await flushPromises()

    expect(
      wrapper.find('[data-testid="kpi-error"]').exists(),
      '旧请求的失败不得覆盖新请求已落定的成功',
    ).toBe(false)
    expect(wrapper.find('[data-testid="onboarding-progress"]').text()).toContain('1/4')
  })

  it('并发模型检查乱序：旧响应不得覆盖新响应', async () => {
    const wrapper = mountView(406)
    await flushPromises()

    const slow = deferred<ReturnType<typeof llmStatusFixture>>()
    const fast = deferred<ReturnType<typeof llmStatusFixture>>()
    vi.mocked(getLLMStatus)
      .mockReturnValueOnce(slow.promise as never)
      .mockReturnValueOnce(fast.promise as never)

    await refreshBtn(wrapper).trigger('click') // 旧检查
    await refreshBtn(wrapper).trigger('click') // 新检查
    await flushPromises()

    fast.resolve(llmStatusFixture(true))
    await flushPromises()
    expect(wrapper.find('[data-testid="onboarding-step-model"]').attributes('data-done')).toBe('true')

    slow.resolve(llmStatusFixture(false))
    await flushPromises()
    expect(
      wrapper.find('[data-testid="onboarding-step-model"]').attributes('data-done'),
      '旧的「未配置」响应不得覆盖新的「已配置」结果',
    ).toBe('true')
  })

  it('首次加载失败（从未 ready）不挂载清单（维持现状）', async () => {
    vi.mocked(getUserStats).mockRejectedValue(new Error('stats down'))

    const wrapper = mountView(403)
    await flushPromises()

    expect(checklist(wrapper).exists(), '从未 ready 时不应显示清单').toBe(false)
    expect(refreshing(wrapper).exists()).toBe(false)
  })
})
