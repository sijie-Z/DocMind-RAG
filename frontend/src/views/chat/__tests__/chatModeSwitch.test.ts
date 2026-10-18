/**
 * B2 · 对话入口合并（决策 D3）—— 聊天页模式开关的判别测试。
 *
 * 契约：
 *  1) 对话页输入区提供「快速问答 / 深度分析（Agent）」二选一开关；
 *  2) 选择「深度分析」= 复用 /agent 的 PER 能力，且强制走 SSE（Agent 仅 SSE 可用）；
 *  3) 即使传输开关停在 WS，只要 Agent 模式开启，发送仍必须走 /chat/stream
 *     （修前 WS 分支完全忽略 useAgent，Agent 模式会被静默降级为普通问答）。
 *
 * 先红：修前只有一枚「火箭」图标 toggle（硬编码中文 tooltip），没有二选一
 * 模式开关；且 handleSend 只看 useSSE，Agent + WS 组合会走 WS 分支。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { defineComponent, h, ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import ChatInput from '../components/ChatInput.vue'
import { useChatSend } from '../composables/useChatSend'
import zh from '@/locales/zh'
import type { ChatMessage } from '@/types/chat'

const { messageSpies, ssePost } = vi.hoisted(() => ({
  messageSpies: { error: vi.fn(), info: vi.fn(), success: vi.fn(), warning: vi.fn() },
  ssePost: vi.fn().mockResolvedValue(true),
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
    isConnected: vi.fn(() => false),
    send: vi.fn(() => true),
    sendStop: vi.fn(),
  },
}))

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

const NButtonStub = defineComponent({
  name: 'NButton',
  emits: ['click'],
  setup(_, { slots }) {
    return () => h('button', { type: 'button' }, [slots.icon?.(), slots.default?.()])
  },
})

const PassthroughStub = defineComponent({
  name: 'Passthrough',
  setup(_, { slots }) {
    return () => h('span', [slots.trigger?.(), slots.default?.()])
  },
})

const NRadioGroupStub = defineComponent({
  name: 'NRadioGroup',
  props: { value: { type: String, default: '' } },
  emits: ['update:value'],
  setup(_, { slots }) {
    return () => h('div', { class: 'mode-switch-stub' }, slots.default?.())
  },
})

const NRadioButtonStub = defineComponent({
  name: 'NRadioButton',
  props: { value: { type: String, default: '' } },
  setup(props, { slots }) {
    return () => h('button', { type: 'button', 'data-mode': props.value }, slots.default?.())
  },
})

function mountInput() {
  // eslint-disable-next-line prefer-const -- 闭包需在 mount 前引用 wrapper
  let wrapper: VueWrapper
  const handlers = {
    'onUpdate:useAgent': (v: boolean) => wrapper.setProps({ useAgent: v }),
    'onUpdate:useSSE': (v: boolean) => wrapper.setProps({ useSSE: v }),
    'onUpdate:inputMessage': (v: string) => wrapper.setProps({ inputMessage: v }),
    'onUpdate:strictMode': (v: boolean) => wrapper.setProps({ strictMode: v }),
    'onUpdate:privacyMode': (v: boolean) => wrapper.setProps({ privacyMode: v }),
    'onUpdate:useStream': (v: boolean) => wrapper.setProps({ useStream: v }),
  }
  wrapper = mount(ChatInput, {
    props: {
      isLoading: false,
      attachedFiles: [],
      attachedFileIds: [],
      connectionStatus: 'connected',
      connectionStatusText: '已连接',
      inputMessage: '',
      strictMode: false,
      privacyMode: true,
      useSSE: false,
      useStream: true,
      useAgent: false,
      ...handlers,
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
        'n-radio-group': NRadioGroupStub,
        'n-radio-button': NRadioButtonStub,
      },
    },
  })
  return wrapper
}

describe('B2 · 聊天页「快速问答 / 深度分析（Agent）」模式开关', () => {
  it('输入区渲染两个模式的四语词条文案', () => {
    const wrapper = mountInput()
    const text = wrapper.text()
    expect(text).toContain(zh.chat.modeQuick)
    expect(text).toContain(zh.chat.modeDeep)
  })

  it('选择深度分析 → useAgent=true 且强制 useSSE=true', async () => {
    const wrapper = mountInput()
    const group = wrapper.findComponent(NRadioGroupStub)
    expect(group.exists()).toBe(true)
    group.vm.$emit('update:value', 'deep')
    await wrapper.vm.$nextTick()

    expect(wrapper.props('useAgent')).toBe(true)
    expect(wrapper.props('useSSE')).toBe(true)
  })

  it('切回快速问答 → useAgent=false（不再走 PER）', async () => {
    const wrapper = mountInput()
    await wrapper.setProps({ useAgent: true, useSSE: true })
    wrapper.findComponent(NRadioGroupStub).vm.$emit('update:value', 'quick')
    await wrapper.vm.$nextTick()
    expect(wrapper.props('useAgent')).toBe(false)
  })
})

describe('B2 · Agent 模式不受传输开关影响（始终走 SSE）', () => {
  beforeEach(() => {
    messageSpies.error.mockClear()
    messageSpies.warning.mockClear()
    ssePost.mockClear()
  })

  it('useSSE=false + useAgent=true 的发送仍走 /chat/stream 且 useAgent=true', async () => {
    setActivePinia(createPinia())
    const Host = defineComponent({
      setup() {
        const messages = ref<ChatMessage[]>([])
        const attachedFiles = ref([])
        const attachedFileIds = ref<string[]>([])
        const isLoading = ref(false)
        const isRetrieving = ref(false)
        const sseStatus = ref('disconnected')
        const useSSE = ref(false) // WS 传输
        const useAgent = ref(true) // 深度分析
        const { handleSend, inputMessage } = useChatSend(
          messages,
          attachedFiles,
          attachedFileIds,
          () => {},
          async () => {},
          isLoading,
          isRetrieving,
          sseStatus,
          useSSE,
          useAgent,
          ref(true),
        )
        inputMessage.value = '分析这个问题'
        return { handleSend }
      },
      render() {
        return h('div')
      },
    })
    const wrapper = mount(Host, { global: { plugins: [i18n] } })
    await (wrapper.vm as unknown as { handleSend: () => Promise<void> }).handleSend()
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(ssePost).toHaveBeenCalledTimes(1)
    const [path, payload] = ssePost.mock.calls[0]
    expect(path).toBe('stream')
    expect(payload.useAgent).toBe(true)
  })
})
