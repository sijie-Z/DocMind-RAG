/**
 * B5 · 首页第一屏「开始做事」+ 注册后 4 步 onboarding 清单（issue #416）。
 *
 * 判别点（先红）：
 * 1. 首屏存在「开始做事」三入口（新对话 / 上传文档 / 建 Agent），点击分别
 *    导航到 /chat、/knowledge、/agent；最近会话列表同在首屏；统计卡整体
 *    位于二者之后（下移但不删除）。现状首屏只有统计卡，无这三处锚点。
 * 2. 4 步清单（连模型 → 建知识库 → 传文档 → 首次问答）的完成态由真实数据
 *    推导：LLM 状态（后端 /monitoring/llm-status）、file_count（已上传）、
 *    knowledge_count（已完成索引）、message_count（至少一轮问答）。现状无清单。
 * 3. 跳过不消失：点击跳过后折叠为常驻入口（仍显示进度、可再次展开），
 *    刷新/重登后折叠态仍在；完成态在重新挂载后仍由服务端数据恢复。现状的
 *    「上传一个文件引导即消失」在 file_count>0 时整块消失，无跳过/恢复语义。
 * 4. 跳过状态按用户隔离（localStorage key 带用户 id），换账号登录不继承。
 *
 * 变异点：删除三入口 / 把统计卡移回首屏最前 / 清单仅在 file_count===0 时
 * 显示 / 跳过写内存不落 localStorage / key 不含用户 id → 对应断言红。
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
  data: {
    success: true,
    data: {
      data: [
        {
          id: 'c1',
          title: '会话一',
          created_at: '',
          updated_at: new Date().toISOString(),
          message_count: 2,
        },
      ],
      total: 1,
      page: 1,
      page_size: 20,
    },
  },
})

const llmStatusFixture = (configured: boolean) => ({
  data: {
    success: true,
    data: {
      configured,
      provider: 'deepseek',
      model: 'deepseek-v4-flash',
      source: 'server',
    },
  },
})

/** 用户身份由 store 从 localStorage 恢复；每次挂载前写入，模拟已登录。 */
function setUser(userId: number) {
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
}

function mountView(userId = 101) {
  setUser(userId)
  const pinia = createPinia()
  setActivePinia(pinia)
  const push = vi.fn()
  const wrapper = mount(UserDashboard, {
    global: {
      plugins: [pinia, i18n],
      mocks: { $router: { push } },
    },
  })
  return { wrapper, push }
}

function isBefore(a: Element, b: Element): boolean {
  return Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)
}

const stepDone = (wrapper: ReturnType<typeof mountView>['wrapper'], key: string) =>
  wrapper.find(`[data-testid="onboarding-step-${key}"]`).attributes('data-done')

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
  vi.mocked(getLLMStatus).mockResolvedValue(llmStatusFixture(false) as never)
  vi.mocked(seedDemoData).mockResolvedValue({ data: { success: false } } as never)
})

enableAutoUnmount(afterEach)

describe('B5 · 首页第一屏「开始做事」', () => {
  it('三入口在首屏且可导航；最近会话同在首屏；统计卡下移但不删除', async () => {
    vi.mocked(getUserStats).mockResolvedValue(
      statsFixture({ conversation_count: 3, message_count: 7, file_count: 2, knowledge_count: 1 }) as never,
    )
    vi.mocked(getConversations).mockResolvedValue(conversationsFixture() as never)

    const { wrapper, push } = mountView()
    await flushPromises()

    const quickstart = wrapper.find('[data-testid="home-quickstart"]')
    expect(quickstart.exists(), '首屏缺少「开始做事」区块').toBe(true)

    const chat = wrapper.find('[data-testid="home-action-chat"]')
    const upload = wrapper.find('[data-testid="home-action-upload"]')
    const agent = wrapper.find('[data-testid="home-action-agent"]')
    expect(chat.exists(), '缺少「新对话」入口').toBe(true)
    expect(upload.exists(), '缺少「上传文档」入口').toBe(true)
    expect(agent.exists(), '缺少「建 Agent」入口').toBe(true)
    expect(chat.text()).toContain('新对话')
    expect(upload.text()).toContain('上传文档')
    expect(agent.text()).toContain('Agent')

    await chat.trigger('click')
    expect(push).toHaveBeenCalledWith('/chat')
    await upload.trigger('click')
    expect(push).toHaveBeenCalledWith('/knowledge')
    await agent.trigger('click')
    expect(push).toHaveBeenCalledWith('/agent')

    const docs = wrapper.find('[data-testid="home-action-docs"]')
    expect(docs.exists(), '缺少「使用文档」入口').toBe(true)
    await docs.trigger('click')
    expect(push).toHaveBeenCalledWith('/system-help')
    // 三入口网格结构不变：文档入口是网格外的补充链接，不占第 4 个卡片位
    const grid = wrapper.find('[data-testid="home-quickstart-grid"]')
    expect(grid.findAll('button').length, '三入口网格应恰好 3 个按钮').toBe(3)
    expect(grid.find('[data-testid="home-action-docs"]').exists()).toBe(false)

    const recent = wrapper.find('[data-testid="home-recent-conversations"]')
    expect(recent.exists(), '首屏缺少最近会话列表').toBe(true)
    expect(recent.text()).toContain('会话一')

    const stats = wrapper.find('[data-testid="home-stats"]')
    expect(stats.exists(), '统计卡区块不应被删除').toBe(true)
    expect(isBefore(quickstart.element, stats.element), '「开始做事」应在统计卡之前').toBe(true)
    expect(isBefore(recent.element, stats.element), '最近会话应在统计卡之前').toBe(true)
  })
})

describe('B5 · 注册后 4 步清单（真实数据推导 + 可恢复 + 跳过不消失）', () => {
  it('完成态由真实数据推导：LLM 配置 / 已上传 / 已索引 / 首次问答', async () => {
    // 空账号：0/4
    const first = mountView(301)
    await flushPromises()

    expect(first.wrapper.find('[data-testid="onboarding-checklist"]').exists(), '缺少 onboarding 清单').toBe(true)
    expect(first.wrapper.find('[data-testid="onboarding-steps"]').exists()).toBe(true)
    for (const key of ['model', 'knowledge', 'upload', 'chat']) {
      expect(stepDone(first.wrapper, key), `空账号步骤 ${key} 不应完成`).toBe('false')
    }
    expect(first.wrapper.find('[data-testid="onboarding-progress"]').text()).toContain('0/4')
    first.wrapper.unmount()

    // 只上传了 1 个文件（尚未完成索引）：建知识库 ✓，传文档（索引完成）✗
    vi.mocked(getUserStats).mockResolvedValue(statsFixture({ file_count: 1 }) as never)
    const second = mountView(301)
    await flushPromises()
    expect(stepDone(second.wrapper, 'knowledge')).toBe('true')
    expect(stepDone(second.wrapper, 'upload')).toBe('false')
    expect(stepDone(second.wrapper, 'chat')).toBe('false')
    expect(second.wrapper.find('[data-testid="onboarding-progress"]').text()).toContain('1/4')
    second.wrapper.unmount()

    // 全部达成：LLM 已配置 + 文档已索引 + 至少一轮问答
    vi.mocked(getUserStats).mockResolvedValue(
      statsFixture({ file_count: 1, knowledge_count: 1, message_count: 2 }) as never,
    )
    vi.mocked(getLLMStatus).mockResolvedValue(llmStatusFixture(true) as never)
    const third = mountView(301)
    await flushPromises()
    for (const key of ['model', 'knowledge', 'upload', 'chat']) {
      expect(stepDone(third.wrapper, key), `步骤 ${key} 应完成`).toBe('true')
    }
    expect(third.wrapper.find('[data-testid="onboarding-progress"]').text()).toContain('4/4')
    third.wrapper.unmount()

    // 重新挂载（刷新/重登）后完成态仍由服务端数据恢复
    const fourth = mountView(301)
    await flushPromises()
    for (const key of ['model', 'knowledge', 'upload', 'chat']) {
      expect(stepDone(fourth.wrapper, key), `重新挂载后步骤 ${key} 应仍完成`).toBe('true')
    }
  })

  it('已有文档后清单不消失（替代旧引导 file_count>0 即隐藏）', async () => {
    vi.mocked(getUserStats).mockResolvedValue(
      statsFixture({ file_count: 3, knowledge_count: 2, message_count: 5 }) as never,
    )
    vi.mocked(getLLMStatus).mockResolvedValue(llmStatusFixture(true) as never)

    const { wrapper } = mountView(302)
    await flushPromises()

    expect(wrapper.find('[data-testid="onboarding-checklist"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="onboarding-steps"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="onboarding-skip"]').exists()).toBe(true)
  })

  it('跳过不消失：折叠为常驻入口，刷新后仍在，且可再次展开', async () => {
    const { wrapper } = mountView(303)
    await flushPromises()

    expect(wrapper.find('[data-testid="onboarding-steps"]').exists()).toBe(true)
    await wrapper.find('[data-testid="onboarding-skip"]').trigger('click')

    expect(wrapper.find('[data-testid="onboarding-steps"]').exists(), '跳过后应折叠').toBe(false)
    const collapsed = wrapper.find('[data-testid="onboarding-collapsed"]')
    expect(collapsed.exists(), '跳过后清单不应消失，应保留常驻入口').toBe(true)
    expect(collapsed.text()).toContain('0/4')
    expect(localStorage.getItem('docmind.onboarding.skipped.303')).toBe('1')
    wrapper.unmount()

    // 刷新/重登（同一浏览器、同一用户）后折叠态恢复
    const again = mountView(303)
    await flushPromises()
    expect(again.wrapper.find('[data-testid="onboarding-collapsed"]').exists(), '刷新后跳过态应恢复').toBe(true)
    expect(again.wrapper.find('[data-testid="onboarding-steps"]').exists()).toBe(false)

    // 常驻入口可再次查看完整清单
    await again.wrapper.find('[data-testid="onboarding-expand"]').trigger('click')
    expect(again.wrapper.find('[data-testid="onboarding-steps"]').exists()).toBe(true)
    expect(again.wrapper.find('[data-testid="onboarding-collapsed"]').exists()).toBe(false)
  })

  it('跳过状态按用户隔离：换账号登录不继承上一账号的跳过态', async () => {
    localStorage.setItem('docmind.onboarding.skipped.304', '1')

    const { wrapper } = mountView(305)
    await flushPromises()

    expect(wrapper.find('[data-testid="onboarding-steps"]').exists(), '新用户不应继承他人跳过态').toBe(true)
    expect(wrapper.find('[data-testid="onboarding-collapsed"]').exists()).toBe(false)
  })

  it('模型状态检查失败与「未配置」区分展示；重新检查成功后打勾', async () => {
    vi.mocked(getLLMStatus).mockRejectedValueOnce(new Error('network down'))

    const { wrapper } = mountView(309)
    await flushPromises()

    const modelStep = () =>
      wrapper.find('[data-testid="onboarding-step-model"]')
    expect(modelStep().attributes('data-done')).toBe('false')
    expect(modelStep().text(), '网络失败不应误报为「未检测到模型」').toContain('检查失败')

    vi.mocked(getLLMStatus).mockResolvedValue(llmStatusFixture(true) as never)
    await wrapper.find('[data-testid="onboarding-refresh"]').trigger('click')
    await flushPromises()

    expect(modelStep().attributes('data-done')).toBe('true')
  })

  it('重新检查：重拉模型状态与统计（步骤 2-4 的真实数据）', async () => {
    const { wrapper } = mountView(310)
    await flushPromises()

    expect(getLLMStatus).toHaveBeenCalledTimes(1)
    expect(getUserStats).toHaveBeenCalledTimes(1)

    await wrapper.find('[data-testid="onboarding-refresh"]').trigger('click')
    await flushPromises()

    expect(getLLMStatus).toHaveBeenCalledTimes(2)
    expect(getUserStats).toHaveBeenCalledTimes(2)
  })

  it('清单步骤动作可导航到知识库与对话', async () => {
    const { wrapper, push } = mountView(311)
    await flushPromises()

    await wrapper.find('[data-testid="onboarding-action-knowledge"]').trigger('click')
    expect(push).toHaveBeenCalledWith('/knowledge')
    await wrapper.find('[data-testid="onboarding-action-upload"]').trigger('click')
    expect(push).toHaveBeenCalledWith('/knowledge')
    await wrapper.find('[data-testid="onboarding-action-chat"]').trigger('click')
    expect(push).toHaveBeenCalledWith('/chat')
  })

  it('模型状态在途显示「正在检查」，不提前误报未配置', async () => {
    let resolveStatus!: (value: unknown) => void
    vi.mocked(getLLMStatus).mockReturnValue(
      new Promise((resolve) => { resolveStatus = resolve }) as never,
    )

    const { wrapper } = mountView(312)
    await flushPromises()

    const modelStep = () => wrapper.find('[data-testid="onboarding-step-model"]')
    expect(modelStep().text()).toContain('正在检查')

    resolveStatus(llmStatusFixture(false))
    await flushPromises()
    expect(modelStep().text()).toContain('尚未检测到可用模型')
  })

  it('统计降级（degraded=true）时不把全零当作 0/4 展示', async () => {
    // 守卫用例（基线无清单，故不构成红证据）：防止将来把降级数据当真实 0 渲染
    vi.mocked(getUserStats).mockResolvedValue({
      data: {
        ...statsFixture().data,
        degraded: true,
        reason: '个人统计查询失败，数据暂不可用',
      },
    } as never)

    const { wrapper } = mountView(306)
    await flushPromises()

    expect(wrapper.find('[data-testid="onboarding-checklist"]').exists(), '降级数据不应驱动清单').toBe(false)
  })
})
