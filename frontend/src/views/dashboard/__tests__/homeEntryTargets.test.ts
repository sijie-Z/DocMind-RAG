/**
 * T9 · 首页入口落点校准（issue #416，P2）。
 *
 * 现状：B2 已把「历史记录 / 智能 Agent」从菜单移除（会话列表并入对话页侧栏、
 * Agent 改为对话页「深度分析」模式开关），但首页仍在把用户送回兼容落点：
 *  - 「查看全部」→ /conversations（与 ChatSidebar 重复的兼容路由）；
 *  - 「创建 Agent」→ /agent（独立工作台保留为任务通知/回放深链，且页面并不
 *    提供「创建 Agent」能力；文案冒充创建入口）。
 *
 * 修复口径（本文件钉住）：
 * 1. 「查看全部」→ /chat（聊天页侧栏即会话列表），不再走 /conversations；
 * 2. Agent 入口文案如实（「打开 Agent 工作台」而非「创建 Agent」），四语同步；
 *    跳转保留为 /agent（有效工作台），页面另给「返回对话」出口
 *    （见 views/agent/__tests__/agentBackToChat.test.ts）。
 *
 * 变异点：查看全部改回 /conversations → 对应用例红；把文案改回「创建 Agent」
 * 或漏改任一语种 → 文案/四语用例红；Agent 入口改指对话页 → 目标用例红。
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
import en from '@/locales/en'
import ja from '@/locales/ja'
import fr from '@/locales/fr'

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
  const push = vi.fn()
  const wrapper = mount(UserDashboard, {
    global: {
      plugins: [pinia, i18n],
      mocks: { $router: { push } },
    },
  })
  return { wrapper, push }
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  vi.mocked(getUserStats).mockResolvedValue({
    data: {
      conversation_count: 3,
      message_count: 7,
      file_count: 1,
      knowledge_count: 1,
      storage_used: 0,
      activity_trend: [0, 0, 0, 0, 0, 0, 0],
    },
  } as never)
  vi.mocked(getConversations).mockResolvedValue({
    data: { success: true, data: { data: [], total: 0, page: 1, page_size: 20 } },
  } as never)
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

describe('T9 · 首页入口落点校准', () => {
  it('「查看全部」→ /chat（对话页侧栏即会话列表），不再指向 /conversations 兼容路由', async () => {
    const { wrapper, push } = mountView(501)
    await flushPromises()

    // 测试环境未注册 naive-ui 全局组件，n-button 按自定义元素渲染，
    // 故按标签定位头部第一个按钮（文案同时兜底断言）
    const recent = wrapper.find('[data-testid="home-recent-conversations"]')
    expect(recent.exists()).toBe(true)
    const viewAll = recent.find('n-button')
    expect(viewAll.exists(), '缺少「查看全部」入口').toBe(true)
    expect(viewAll.text()).toContain(zh.dashboard.viewAll)

    await viewAll.trigger('click')
    expect(push).toHaveBeenCalledWith('/chat')
    expect(push).not.toHaveBeenCalledWith('/conversations')
    expect(
      recent.find('[data-testid="home-view-all"]').exists(),
      '「查看全部」应有稳定测试锚点',
    ).toBe(true)
  })

  it('Agent 入口文案如实（打开工作台，而非创建 Agent），跳转仍指向 /agent 工作台', async () => {
    const { wrapper, push } = mountView(502)
    await flushPromises()

    const agent = wrapper.find('[data-testid="home-action-agent"]')
    expect(agent.exists(), '缺少 Agent 入口').toBe(true)
    expect(agent.text()).toContain(zh.dashboard.startAgent)
    expect(agent.text()).not.toContain('创建 Agent')

    await agent.trigger('click')
    expect(push).toHaveBeenCalledWith('/agent')
  })

  it('四语词条同步：不再声称「创建 Agent」，如实描述打开 Agent 工作台', () => {
    expect(zh.dashboard.startAgent).toBe('打开 Agent 工作台')
    expect(en.dashboard.startAgent).toBe('Open Agent Workspace')
    expect(ja.dashboard.startAgent).toBe('Agent ワークベンチを開く')
    expect(fr.dashboard.startAgent).toBe("Ouvrir l'espace de travail Agent")
  })
})
