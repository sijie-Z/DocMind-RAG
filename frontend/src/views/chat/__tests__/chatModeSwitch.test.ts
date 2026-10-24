/**
 * issue #508（方案 B）· Chat 深度分析入口强制跳 /agent 的判别测试。
 *
 * 原文件（B2/D3）钉的是「快速问答 / 深度分析」二选一开关 + 即使 WS 也强制
 * SSE 走 useAgent。方案 B 后契约反转：
 *  1) Chat 不再有深度分析模式开关；入口文案为「前往 Agent 工作台（支持审批/回放）」，
 *     点击只发出 openAgentWorkbench（由页面 router.push('/agent')）；
 *  2) 发送链路不再携带 useAgent，普通 RAG 的传输开关（SSE/WS）恢复为用户选择；
 *  3) Agent 模式相关的旧控件（agent-mode-control / mode-switch）不复存在。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent, h, ref, type Ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import ChatInput from '../components/ChatInput.vue'
import { useChatSend } from '../composables/useChatSend'
import zh from '@/locales/zh'
import type { ChatMessage } from '@/types/chat'

const { messageSpies, ssePost, wsSend } = vi.hoisted(() => ({
  messageSpies: { error: vi.fn(), info: vi.fn(), success: vi.fn(), warning: vi.fn() },
  ssePost: vi.fn().mockResolvedValue(true),
  wsSend: vi.fn(() => true),
}))

vi.mock('@/utils/message', () => ({
  useDedupedMessage: () => messageSpies,
}))

vi.mock('@/utils/sseService', () => ({
  sseService: {
    on: vi.fn(),
    off: vi.fn(),
    post: ssePost,
    abort: vi.fn(),
  },
}))

vi.mock('@/utils/websocket', () => ({
  wsService: {
    isConnected: vi.fn(() => true),
    send: wsSend,
    sendStop: vi.fn(),
  },
}))

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

const NButtonStub = defineComponent({
  name: 'NButton',
  emits: ['click'],
  setup(_, { slots, emit }) {
    return () => h('button', { type: 'button', onClick: () => emit('click') }, [slots.icon?.(), slots.default?.()])
  },
})

const PassthroughStub = defineComponent({
  name: 'Passthrough',
  setup(_, { slots }) {
    return () => h('span', [slots.trigger?.(), slots.default?.()])
  },
})

function mountInput() {
  const wrapper = mount(ChatInput, {
    props: {
      isLoading: false,
      attachedFiles: [],
      attachedFileIds: [],
      connectionStatus: 'connected',
      connectionStatusText: '已连接',
      inputMessage: '请分析这份财报',
      strictMode: false,
      privacyMode: true,
      useSSE: true,
      useStream: true,
    },
    global: {
      plugins: [i18n],
      stubs: {
        'n-button': NButtonStub,
        'n-input': PassthroughStub,
        'n-tooltip': PassthroughStub,
        'n-popover': PassthroughStub,
        'n-switch': PassthroughStub,
        'n-icon': true,
      },
    },
  })
  return wrapper
}

describe('issue #508 · Chat 深度分析入口（前往 Agent 工作台）', () => {
  it('入口文案为「前往 Agent 工作台（支持审批/回放）」', () => {
    const wrapper = mountInput()
    const entry = wrapper.find('[data-testid="agent-workbench-entry"]')
    expect(entry.exists()).toBe(true)
    expect(entry.text()).toContain('前往 Agent 工作台（支持审批/回放）')
    expect(entry.attributes('data-agent')).toBeUndefined()
  })

  it('点击入口只发出 openAgentWorkbench（导航由页面负责，UI 不再切 Agent 模式）', async () => {
    const wrapper = mountInput()
    await wrapper.find('[data-testid="agent-workbench-entry"]').trigger('click')
    expect(wrapper.emitted('openAgentWorkbench')).toHaveLength(1)
    expect(wrapper.find('[data-testid="agent-mode-control"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="agent-mode-switch"]').exists()).toBe(false)
    expect(wrapper.find('.chat-mode-switch').exists()).toBe(false)
  })
})

describe('issue #508 · Chat 发送链路不再有 Agent 分支', () => {
  beforeEach(() => {
    messageSpies.error.mockClear()
    ssePost.mockClear()
    wsSend.mockClear()
  })

  const mountHost = (useSSERef: Ref<boolean>) => {
    setActivePinia(createPinia())
    const Host = defineComponent({
      setup() {
        const messages = ref<ChatMessage[]>([])
        const attachedFiles = ref([])
        const attachedFileIds = ref<string[]>([])
        const isLoading = ref(false)
        const isRetrieving = ref(false)
        const sseStatus = ref('disconnected')
        const { handleSend, inputMessage } = useChatSend(
          messages,
          attachedFiles,
          attachedFileIds,
          () => {},
          async () => {},
          isLoading,
          isRetrieving,
          sseStatus,
          useSSERef,
          ref(true),
        )
        inputMessage.value = '分析这个问题'
        return { handleSend }
      },
      render() {
        return h('div')
      },
    })
    return mount(Host, { global: { plugins: [i18n] } })
  }

  it('SSE 传输：请求体不含 useAgent（不再以 useAgent=true 发到 /chat/stream）', async () => {
    const wrapper = mountHost(ref(true))
    await (wrapper.vm as unknown as { handleSend: () => Promise<void> }).handleSend()
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(ssePost).toHaveBeenCalledTimes(1)
    const [path, payload] = ssePost.mock.calls[0]
    expect(path).toBe('stream')
    expect('useAgent' in (payload as Record<string, unknown>)).toBe(false)
  })

  it('WS 传输：尊重用户选择走 WebSocket（不再被旧 Agent 分支强制拉回 SSE）', async () => {
    const wrapper = mountHost(ref(false))
    await (wrapper.vm as unknown as { handleSend: () => Promise<void> }).handleSend()
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(wsSend).toHaveBeenCalledTimes(1)
    expect(ssePost).not.toHaveBeenCalled()
  })
})
