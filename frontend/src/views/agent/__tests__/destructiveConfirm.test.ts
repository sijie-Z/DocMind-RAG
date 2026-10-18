/**
 * E2 · Agent 页破坏性操作二次确认判别用例（清空当前会话 / 删除当前会话）。
 *
 * 验收口径（E2 五处破坏性操作之二、三）：
 *  - 「清空」：未确认 → 不调 agentStore.clearMessages，消息保持；确认后才清空；
 *  - 「删除会话」：未确认 → 不调 agentApi.deleteSession，会话列表保持；确认后才删除；
 *  - 两处均经 Naive UI dialog.warning 弹出，清空文案写明范围（当前会话）。
 *
 * 变异点：去掉确认（直接清空 / 直接删）→ 对应用例红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createI18n } from 'vue-i18n'
import { NMessageProvider, NSpin, NButton, NTooltip, NIcon } from 'naive-ui'
import { createPinia, setActivePinia } from 'pinia'

import AgentView from '../index.vue'
import { agentApi } from '@/api/agent'
import { useAgentStore } from '@/stores/agent'
import zh from '@/locales/zh'

interface DialogOptions {
  title?: string
  content?: string
  onPositiveClick?: () => unknown
  onNegativeClick?: () => unknown
  onClose?: () => unknown
}

const { dialogState, dialogWarning } = vi.hoisted(() => {
  const state = {
    decision: 'cancel' as 'cancel' | 'confirm',
    calls: [] as DialogOptions[],
  }
  const warning = vi.fn((opts: DialogOptions) => {
    state.calls.push(opts)
    if (state.decision === 'confirm') opts.onPositiveClick?.()
    else opts.onNegativeClick?.()
  })
  return { dialogState: state, dialogWarning: warning }
})

vi.mock('naive-ui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('naive-ui')>()
  return {
    ...actual,
    useDialog: () => ({ warning: dialogWarning, info: vi.fn() }),
  }
})

vi.mock('@/api/agent', () => ({
  agentApi: {
    listTools: vi.fn().mockResolvedValue({ data: { success: true, data: [], total: 0 } }),
    listSessions: vi.fn().mockResolvedValue({ data: { success: true, data: { sessions: [], total: 0, page: 1, page_size: 20 } } }),
    getConfig: vi.fn().mockResolvedValue({ data: { success: true, data: {} } }),
    chat: vi.fn(),
    getSession: vi.fn().mockResolvedValue({ data: { success: true, data: null } }),
    deleteSession: vi.fn().mockResolvedValue({}),
    createSession: vi.fn().mockResolvedValue({ data: { success: true, data: null } }),
    updateConfig: vi.fn().mockResolvedValue({}),
    submitFeedback: vi.fn().mockResolvedValue({}),
  },
}))

const mockedDeleteSession = vi.mocked(agentApi.deleteSession)

const Host = defineComponent({
  render: () => h(NMessageProvider, null, { default: () => h(AgentView) }),
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

const SESSION = {
  id: 's1',
  title: '测试会话',
  message_count: 2,
  created_at: '2026-10-18T10:00:00',
  updated_at: '2026-10-18T10:05:00',
}

function seedPinia() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useAgentStore()
  store.addMessage({
    role: 'assistant',
    content: '旧消息',
    events: [],
    loading: false,
    currentTool: '',
    thinkingText: '',
    planSteps: [],
    planId: '',
    progress: 0,
  } as never)
  return { pinia, store }
}

async function mountView(pinia: ReturnType<typeof createPinia>) {
  const wrapper = mount(Host, {
    global: {
      plugins: [pinia, i18n],
      components: { NSpin, NButton, NTooltip, NIcon },
      stubs,
    },
  })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
  dialogState.decision = 'cancel'
  dialogState.calls.length = 0
  dialogWarning.mockClear()
  mockedDeleteSession.mockResolvedValue({} as never)
})

describe('Agent 页 · 破坏性操作二次确认（E2）', () => {
  it('负相（取消）：「清空」不调 clearMessages，消息保持', async () => {
    const { pinia, store } = seedPinia()
    const clearSpy = vi.spyOn(store, 'clearMessages')
    const wrapper = await mountView(pinia)

    dialogState.decision = 'cancel'
    const clearBtn = wrapper.findAll('button').find((b) => b.text() === '清空')
    expect(clearBtn, '未找到「清空」按钮').toBeTruthy()
    await clearBtn!.trigger('click')
    await flushPromises()

    expect(dialogWarning).toHaveBeenCalledTimes(1)
    expect(clearSpy).not.toHaveBeenCalled()
    expect(store.messages.length).toBe(1)
  })

  it('正相（确认）：「清空」文案写明当前会话范围，确认后清空消息', async () => {
    const { pinia, store } = seedPinia()
    const wrapper = await mountView(pinia)

    dialogState.decision = 'confirm'
    const clearBtn = wrapper.findAll('button').find((b) => b.text() === '清空')
    await clearBtn!.trigger('click')
    await flushPromises()

    expect(dialogWarning).toHaveBeenCalledTimes(1)
    expect(String(dialogState.calls[0]?.content)).toContain('当前会话')
    expect(store.messages.length).toBe(0)
  })

  it('负相（取消）：删除会话不调接口，会话列表不变', async () => {
    const { pinia, store } = seedPinia()
    const wrapper = await mountView(pinia)
    // 初始化加载会重写 sessions——在挂载完成后再种入待删会话
    store.setSessions([{ ...SESSION }] as never)
    const vm = wrapper.findComponent(AgentView).vm as unknown as {
      handleSessionDelete: (id: string) => Promise<void>
    }

    dialogState.decision = 'cancel'
    await vm.handleSessionDelete('s1')
    await flushPromises()

    expect(dialogWarning).toHaveBeenCalledTimes(1)
    expect(mockedDeleteSession).not.toHaveBeenCalled()
    expect(store.sessions.map((s) => s.id)).toEqual(['s1'])
  })

  it('正相（确认）：删除会话调用接口并移出列表', async () => {
    const { pinia, store } = seedPinia()
    const wrapper = await mountView(pinia)
    // 初始化加载会重写 sessions——在挂载完成后再种入待删会话
    store.setSessions([{ ...SESSION }] as never)
    const vm = wrapper.findComponent(AgentView).vm as unknown as {
      handleSessionDelete: (id: string) => Promise<void>
    }

    dialogState.decision = 'confirm'
    await vm.handleSessionDelete('s1')
    await flushPromises()

    expect(dialogWarning).toHaveBeenCalledTimes(1)
    expect(mockedDeleteSession).toHaveBeenCalledWith('s1')
    expect(store.sessions).toHaveLength(0)
  })
})
