/**
 * 个人模型错误码在 WebSocket 传送带上的四语化。
 *
 * 背景：后端 WS error 帧与 SSE 同口径携带 `code: "USER_LLM_REQUEST_FAILED"`，
 * 但修前 useChatConnection 只读 content——英文/日文/法文用户切到 WS 后仍看到
 * 中文诊断。修复：按 code 映射 `chat.userLlmRequestFailed`，未知码回退 content。
 *
 * 变异点：去掉 code 分支 → 首用例红；错误地吞掉未知 code 的 content → 次用例红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent, h, ref, type Ref } from 'vue'
import { createI18n } from 'vue-i18n'

const { messageSpies, wsHandlers, wsMocks } = vi.hoisted(() => ({
  messageSpies: { error: vi.fn(), info: vi.fn(), success: vi.fn(), warning: vi.fn() },
  wsHandlers: {} as Record<string, (data: any) => void>,
  wsMocks: { connect: vi.fn(), disconnect: vi.fn(), sendStop: vi.fn() },
}))

vi.mock('@/utils/websocket', () => ({
  wsService: {
    on: vi.fn((ev: string, fn: (data: any) => void) => { wsHandlers[ev] = fn }),
    off: vi.fn(),
    connect: wsMocks.connect,
    disconnect: wsMocks.disconnect,
    sendStop: wsMocks.sendStop,
  },
}))

vi.mock('@/utils/message', () => ({
  useDedupedMessage: () => messageSpies,
}))

vi.mock('@/stores/chat', () => ({
  useChatStore: () => ({
    currentConversation: null,
    setCurrentConversation: vi.fn(),
  }),
}))

vi.mock('@/stores/user', () => ({
  useUserStore: () => ({ isLoggedIn: true, userInfo: { id: 1 } }),
}))

import { useChatConnection } from '../composables/useChatConnection'
import type { ChatMessage } from '@/types/chat'

const i18n = createI18n({
  legacy: false,
  locale: 'zh',
  messages: {
    zh: {
      chat: {
        userLlmRequestFailed: '个人模型配置错误提示',
        newConversation: '新对话',
      },
    },
  },
})

let messages!: Ref<ChatMessage[]>
let connect!: () => Promise<void>

const Host = defineComponent({
  setup() {
    messages = ref<ChatMessage[]>([])
    const connection = useChatConnection(
      messages,
      () => {},
      async () => {},
      () => 'gen-test',
    )
    connection.useSSE.value = false
    connect = () => connection.connectWebSocket()
    return () => h('div')
  },
})

beforeEach(() => {
  messageSpies.error.mockClear()
  for (const key of Object.keys(wsHandlers)) delete wsHandlers[key]
  mount(Host, { global: { plugins: [i18n] } })
})

describe('useChatConnection · 个人模型错误码', () => {
  it('code=USER_LLM_REQUEST_FAILED 时使用四语词条而非后端中文', async () => {
    await connect()

    wsHandlers.error({
      type: 'error',
      code: 'USER_LLM_REQUEST_FAILED',
      content: '后端中文文案',
    })

    expect(messageSpies.error).toHaveBeenCalledWith('个人模型配置错误提示')
  })

  it('无 code 的普通 error 仍展示后端 content', async () => {
    await connect()

    wsHandlers.error({ type: 'error', content: '普通错误' })

    expect(messageSpies.error).toHaveBeenCalledWith('普通错误')
  })

  it('未知 code 且无 content 时不弹空 toast', async () => {
    await connect()

    wsHandlers.error({ type: 'error', code: 'SOMETHING_ELSE' })

    expect(messageSpies.error).not.toHaveBeenCalled()
  })
})
