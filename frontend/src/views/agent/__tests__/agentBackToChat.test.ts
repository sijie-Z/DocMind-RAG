/**
 * T9 · Agent 工作台「返回对话」出口（issue #416，P2）。
 *
 * 背景：首页的 Agent 入口保留跳转到 /agent（有效工作台），但 B2 之后该页
 * 只作任务通知/回放深链保留，从首页/深链进入的用户没有回到对话页的清晰路径。
 * 本用例钉住：页头常驻「返回对话」按钮，点击回到 /chat。
 *
 * 变异点：删除按钮 / 改为 push('/conversations') 等兼容落点 → 本用例红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createI18n } from 'vue-i18n'
import { createRouter, createMemoryHistory } from 'vue-router'
import { NMessageProvider, NDialogProvider, NSpin, NButton, NTooltip, NIcon } from 'naive-ui'
import { createPinia, setActivePinia } from 'pinia'

import AgentView from '../index.vue'
import zh from '@/locales/zh'

const { agentApiMock } = vi.hoisted(() => ({
  agentApiMock: {
    listTools: vi.fn(),
    listSessions: vi.fn(),
    getConfig: vi.fn(),
    chat: vi.fn(),
    getSession: vi.fn(),
    deleteSession: vi.fn(),
    createSession: vi.fn(),
    updateConfig: vi.fn(),
    submitFeedback: vi.fn(),
  },
}))

vi.mock('@/api/agent', () => ({ agentApi: agentApiMock }))

const Host = defineComponent({
  render: () => h(NMessageProvider, null, { default: () => h(NDialogProvider, null, { default: () => h(AgentView) }) }),
})

const stubs = {
  PlanTree: true,
  ThinkingStream: true,
  ToolCallCard: true,
  ReflectionBanner: true,
  AgentConfigPanel: true,
  SessionSelector: true,
  AgentInput: true,
  Markdown: true,
  RecentTaskList: true,
  ReplayTimeline: true,
  NSpin: true,
  NButton: true,
  NTooltip: true,
  NIcon: true,
}

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

async function setupView() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<div />' } },
      { path: '/chat', name: 'Chat', component: { template: '<div />' } },
      { path: '/agent', name: 'Agent', component: { template: '<div />' } },
    ],
  })
  await router.push('/agent')
  await router.isReady()
  const wrapper = mount(Host, {
    global: { plugins: [pinia, i18n, router], components: { NSpin, NButton, NTooltip, NIcon }, stubs },
  })
  await flushPromises()
  return { wrapper, router }
}

beforeEach(() => {
  vi.clearAllMocks()
  agentApiMock.listTools.mockResolvedValue({ data: { success: true, data: [], total: 0 } })
  agentApiMock.listSessions.mockResolvedValue({
    data: { success: true, data: { sessions: [], total: 0, page: 1, page_size: 20 } },
  })
  agentApiMock.getConfig.mockResolvedValue({ data: { success: true, data: {} } })
  agentApiMock.getSession.mockResolvedValue({ data: { success: true, data: null } })
  agentApiMock.createSession.mockResolvedValue({ data: { success: true, data: null } })
})

describe('T9 · Agent 工作台返回对话出口', () => {
  it('页头常驻「返回对话」按钮，点击回到 /chat', async () => {
    const { wrapper, router } = await setupView()

    const back = wrapper.find('[data-testid="agent-back-to-chat"]')
    expect(back.exists(), 'Agent 工作台缺少清晰的「返回对话」出口').toBe(true)
    expect(back.text()).toContain(zh.agent.backToChat)

    await back.trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.path).toBe('/chat')
  })
})
