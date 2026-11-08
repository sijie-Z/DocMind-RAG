/**
 * 聊天页「前往 Agent 工作台」接力：?prompt= 必须进入 Agent 输入框，
 * 且消费后从 URL 移除（避免刷新/后退重复填充）。
 */
import { describe, it, expect, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, ref } from 'vue'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import naive, { NMessageProvider, NDialogProvider } from 'naive-ui'
import zh from '@/locales/zh'

vi.mock('@/api/agent', () => ({
  agentApi: {
    listTools: vi.fn().mockResolvedValue({ data: { success: true, data: [], total: 0 } }),
    listSessions: vi.fn().mockResolvedValue({
      data: { success: true, data: { sessions: [], total: 0, page: 1, page_size: 20 } },
    }),
    getConfig: vi.fn().mockResolvedValue({ data: { success: true, data: {} } }),
    chat: vi.fn(),
    getSession: vi.fn().mockResolvedValue({ data: { success: true, data: null } }),
    deleteSession: vi.fn().mockResolvedValue({}),
    createSession: vi.fn().mockResolvedValue({ data: { success: true, data: null } }),
    updateConfig: vi.fn().mockResolvedValue({}),
    submitFeedback: vi.fn().mockResolvedValue({}),
  },
}))

import AgentView from '../index.vue'

const captured = ref<string | null>(null)

const AgentInputStub = defineComponent({
  props: { initialText: { type: String, default: '' } },
  setup(props) {
    captured.value = props.initialText
    return () => h('div', { 'data-testid': 'agent-input-stub' })
  },
})

const Dummy = defineComponent({ render: () => h('div') })

const i18n = createI18n({
  legacy: false,
  locale: 'zh',
  fallbackLocale: 'zh',
  messages: { zh },
})

const stubs = {
  PlanTree: true,
  ThinkingStream: true,
  ToolCallCard: true,
  ReflectionBanner: true,
  AgentConfigPanel: true,
  SessionSelector: true,
  AgentInput: AgentInputStub,
  Markdown: true,
  TerminalPanel: true,
  NSpin: true,
  NButton: true,
  NTooltip: true,
  NIcon: true,
}

describe('Agent 页 · 聊天接力提示词', () => {
  it('?prompt= 填入输入框并从 URL 移除', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/agent', component: Dummy }],
    })
    await router.push('/agent?prompt=' + encodeURIComponent('请核验这两份合同'))
    await router.isReady()
    setActivePinia(createPinia())

    const Host = defineComponent({
      render: () =>
        h(NMessageProvider, null, {
          default: () => h(NDialogProvider, null, { default: () => h(AgentView) }),
        }),
    })
    mount(Host, {
      global: { plugins: [naive, i18n, router], stubs },
    })
    await flushPromises()

    expect(captured.value).toBe('请核验这两份合同')
    expect(router.currentRoute.value.query.prompt).toBeUndefined()
  })
})
