/**
 * 批次 A：空白 Chat 入口不得沿用 Pinia 中的旧会话 ID。
 *
 * 使用真实 Dashboard → Chat 路由与真实 ChatPage，仅替换传输和外部 API。
 * 首轮在会话列表响应前发送，覆盖 onMounted 慢请求窗口；SSE/WS 均验证
 * “首轮无旧 ID → 服务端给新 ID → 第二轮使用新 ID”。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, nextTick, onMounted, type PropType } from 'vue'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import {
  createMemoryHistory,
  createRouter,
  RouterView,
  type Router,
} from 'vue-router'
import { createI18n } from 'vue-i18n'
import { NDialogProvider, NMessageProvider } from 'naive-ui'
import type { ChatMessage } from '@/types/chat'
import zh from '@/locales/zh'

const {
  sseHandlers,
  wsHandlers,
  ssePostMock,
  wsSendMock,
  wsConnectMock,
  getConversationsMock,
  getHistoryMock,
} = vi.hoisted(() => ({
  sseHandlers: new Map<string, (data: never) => void>(),
  wsHandlers: new Map<string, (data: never) => void>(),
  ssePostMock: vi.fn(),
  wsSendMock: vi.fn(),
  wsConnectMock: vi.fn(),
  getConversationsMock: vi.fn(),
  getHistoryMock: vi.fn(),
}))

vi.mock('@/utils/sseService', () => ({
  sseService: {
    on: vi.fn((event: string, handler: (data: never) => void) => {
      sseHandlers.set(event, handler)
    }),
    off: vi.fn((event: string) => sseHandlers.delete(event)),
    post: ssePostMock,
    abort: vi.fn(),
  },
}))

vi.mock('@/utils/websocket', () => ({
  wsService: {
    isConnected: vi.fn(() => true),
    connect: wsConnectMock,
    disconnect: vi.fn(),
    on: vi.fn((event: string, handler: (data: never) => void) => {
      wsHandlers.set(event, handler)
    }),
    off: vi.fn((event: string) => wsHandlers.delete(event)),
    send: wsSendMock,
    sendStop: vi.fn(),
  },
}))

vi.mock('@/utils/message', () => ({
  useDedupedMessage: () => ({
    error: vi.fn(),
    info: vi.fn(),
    success: vi.fn(),
    warning: vi.fn(),
    loading: vi.fn(),
  }),
}))

vi.mock('@/utils/echarts', () => ({
  default: {
    init: vi.fn(() => ({ setOption: vi.fn(), dispose: vi.fn(), resize: vi.fn() })),
    graphic: { LinearGradient: class LinearGradient {} },
  },
}))

vi.mock('@/api/chat', () => ({
  getConversationMessages: getHistoryMock,
  clearConversationMessages: vi.fn().mockResolvedValue({ data: { success: true } }),
  updateMessageFeedback: vi.fn().mockResolvedValue({ data: {} }),
  unbindConversationDocs: vi.fn().mockResolvedValue({ data: { success: true } }),
  parseUpload: vi.fn().mockResolvedValue({ content: 'x', filename: 'f', chunk_count: 1, file_size: 1 }),
}))

vi.mock('@/api/conversation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/conversation')>()
  return {
    ...actual,
    getConversations: getConversationsMock,
    deleteConversation: vi.fn().mockResolvedValue(undefined),
    getConversationMessages: getHistoryMock,
  }
})

vi.mock('@/api/user', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/user')>()
  return { ...actual, getUserStats: vi.fn() }
})

vi.mock('@/api/monitoring', () => ({
  getRAGStats: vi.fn(),
  getLLMStatus: vi.fn(),
}))

vi.mock('@/api/demo', () => ({ seedDemoData: vi.fn() }))

vi.mock('@/api/knowledge', () => ({
  uploadKnowledgeBase: vi.fn(),
  getKnowledgeBase: vi.fn(),
  getDocumentDetail: vi.fn().mockResolvedValue({ data: { data: {} } }),
  getDocumentContent: vi.fn().mockResolvedValue({ data: { data: {} } }),
}))

vi.mock('@/api/organization', () => ({
  getOrganizationSettings: vi.fn().mockResolvedValue({ data: { settings: {} } }),
}))

import ChatPage from '../index.vue'
import UserDashboard from '@/views/dashboard/user.vue'
import { getUserStats } from '@/api/user'
import { getRAGStats } from '@/api/monitoring'
import { useChatStore } from '@/stores/chat'
import { useUserStore } from '@/stores/user'

const i18n = createI18n({
  legacy: false,
  locale: 'zh',
  missingWarn: false,
  fallbackWarn: false,
  messages: { zh },
})

type Transport = 'sse' | 'ws'
let transportUnderTest: Transport = 'sse'

const ChatMessagesStub = defineComponent({
  name: 'ChatMessages',
  props: { messages: { type: Array as PropType<ChatMessage[]>, default: () => [] } },
  emits: ['scroll', 'scrollToBottom', 'useSuggestion', 'feedback', 'copy', 'regenerate', 'openSource'],
  setup(props) {
    return () => h('div', { class: 'messages-probe' }, String(props.messages.length))
  },
})

const ChatInputStub = defineComponent({
  name: 'ChatInput',
  props: {
    inputMessage: { type: String, default: '' },
    useSSE: { type: Boolean, default: true },
  },
  emits: [
    'update:inputMessage',
    'update:strictMode',
    'update:privacyMode',
    'update:useSSE',
    'update:useStream',
    'send',
    'stopGeneration',
    'triggerFileUpload',
    'removeAttachment',
    'openAgentWorkbench',
  ],
  setup(_props, { emit }) {
    onMounted(() => {
      if (transportUnderTest === 'ws') emit('update:useSSE', false)
    })
    return () => h('div', { class: 'input-probe' })
  },
})

const ChatSidebarStub = defineComponent({
  name: 'ChatSidebar',
  emits: ['newConversation', 'refresh', 'selectConversation', 'deleteConversation'],
  setup() {
    return () => h('div')
  },
})

const NButtonStub = defineComponent({
  setup(_props, { slots }) {
    return () => h('button', slots.default?.())
  },
})

const historyPair = [
  {
    id: 'u-old',
    content: 'historic question',
    messageType: 'user',
    createdAt: '2026-10-26T00:00:00',
  },
  {
    id: 'a-old',
    content: 'historic answer',
    messageType: 'assistant',
    createdAt: '2026-10-26T00:00:01',
    sources: [],
  },
]

const conversationListResponse = () => ({
  data: {
    success: true,
    data: {
      data: [
        {
          id: 'old-session',
          title: 'old title',
          created_at: '2026-10-26T00:00:00',
          updated_at: '2026-10-26T00:00:01',
          message_count: 2,
          last_message: 'historic answer',
        },
      ],
      total: 1,
      page: 1,
      page_size: 50,
    },
  },
})

const Root = defineComponent({
  setup() {
    return () => h(NMessageProvider, null, {
      default: () => h(NDialogProvider, null, { default: () => h(RouterView) }),
    })
  },
})

async function mountApp(initialPath: string, options: { history?: unknown[] } = {}) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/dashboard', name: 'Dashboard', component: UserDashboard },
      { path: '/chat', name: 'Chat', component: ChatPage },
      { path: '/login', name: 'Login', component: { template: '<div />' } },
    ],
  })
  await router.push(initialPath)
  await router.isReady()
  const pinia = createPinia()
  setActivePinia(pinia)
  const userStore = useUserStore()
  userStore.userInfo = { id: 7, username: 'tester', organization_id: 1 } as never
  const chatStore = useChatStore()
  chatStore.setCurrentConversation({
    id: 'old-session',
    title: 'stale title',
    userId: 7,
    createdAt: '2026-10-26T00:00:00',
    updatedAt: '2026-10-26T00:00:01',
  })
  getHistoryMock.mockImplementation(async (id: string) => ({
    data: {
      success: true,
      data: {
        id,
        title: `history-${id}`,
        created_at: '2026-10-26T00:00:00',
        updated_at: '2026-10-26T00:00:01',
        messages: options.history ?? [],
      },
    },
  }))
  const wrapper = mount(Root, {
    global: {
      plugins: [pinia, router, i18n],
      stubs: {
        ChatSidebar: ChatSidebarStub,
        ChatMessages: ChatMessagesStub,
        ChatInput: ChatInputStub,
        ChatHeader: true,
        DocumentPreviewModal: true,
        DebugPanel: true,
        OnboardingChecklist: true,
        'n-button': NButtonStub,
        'n-icon': true,
        'n-progress': true,
        'n-skeleton': true,
        'n-tag': true,
      },
    },
  })
  await flushPromises()
  await flushPromises()
  return { wrapper, router, chatStore }
}

async function sendMessage(wrapper: ReturnType<typeof mount>, content: string) {
  const input = wrapper.findComponent(ChatInputStub)
  input.vm.$emit('update:inputMessage', content)
  await nextTick()
  input.vm.$emit('send')
  await flushPromises()
}

function sentConversationIds(transport: Transport): unknown[] {
  if (transport === 'sse') {
    return ssePostMock.mock.calls.map((call) => (call[1] as Record<string, unknown>).conversationId)
  }
  return wsSendMock.mock.calls.map((call) => call[1])
}

async function completeFirstAnswer(transport: Transport, conversationId: string, messageId = 'assistant-1') {
  if (transport === 'sse') {
    const handler = sseHandlers.get('message')
    expect(handler).toBeDefined()
    handler!({
      type: 'message',
      content: 'first answer',
      conversationId,
      messageId,
    } as never)
  } else {
    const chunk = wsHandlers.get('chunk')
    const message = wsHandlers.get('message')
    expect(chunk).toBeDefined()
    expect(message).toBeDefined()
    chunk!({
      type: 'chunk',
      content: 'first answer',
      conversationId,
      messageId,
    } as never)
    message!({
      type: 'message',
      content: 'first answer',
      conversationId,
      messageId,
    } as never)
  }
  await flushPromises()
}

async function openDashboardChat(wrapper: ReturnType<typeof mount>, router: Router) {
  let releaseList!: () => void
  const listGate = new Promise<void>((resolve) => { releaseList = resolve })
  getConversationsMock.mockImplementation(async () => {
    await listGate
    return conversationListResponse()
  })
  await wrapper.find('[data-testid="home-action-chat"]').trigger('click')
  await flushPromises()
  expect(router.currentRoute.value.path).toBe('/chat')
  return () => { releaseList() }
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  localStorage.setItem('docmind_token', 'tok')
  transportUnderTest = 'sse'
  sseHandlers.clear()
  wsHandlers.clear()
  ssePostMock.mockResolvedValue(true)
  wsSendMock.mockReturnValue(true)
  getConversationsMock.mockImplementation(async () => conversationListResponse())
  vi.mocked(getUserStats).mockResolvedValue({
    data: {
      conversation_count: 1,
      message_count: 2,
      file_count: 0,
      knowledge_count: 0,
      storage_used: 0,
      activity_trend: [0, 0, 0, 0, 0, 0, 0],
      degraded: false,
    },
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
})

enableAutoUnmount(afterEach)

describe('Chat blank-entry conversation reset', () => {
  for (const transport of ['sse', 'ws'] as const) {
    it(`Dashboard new chat: ${transport} first turn has no stale id and second uses the new id`, async () => {
      transportUnderTest = transport
      const { wrapper, router, chatStore } = await mountApp('/dashboard')
      const releaseList = await openDashboardChat(wrapper, router)

      expect(chatStore.currentConversation).toBeNull()
      expect(wrapper.findComponent(ChatMessagesStub).props('messages')).toEqual([])

      await sendMessage(wrapper, 'first fresh question')
      expect(sentConversationIds(transport)).toEqual([undefined])

      releaseList()
      await flushPromises()
      if (transport === 'ws') {
        expect(wsConnectMock).toHaveBeenLastCalledWith(7, undefined)
      }
      const newId = `new-${transport}-session`
      await completeFirstAnswer(transport, newId)
      expect(chatStore.currentConversation?.id).toBe(newId)

      await sendMessage(wrapper, 'second fresh question')
      expect(sentConversationIds(transport)).toEqual([undefined, newId])
    })

    it(`sidebar new chat remains correct: ${transport} two-turn id contract`, async () => {
      transportUnderTest = transport
      const { wrapper, router, chatStore } = await mountApp(
        '/chat?conversation_id=deep-session',
        { history: historyPair },
      )
      expect(chatStore.currentConversation?.id).toBe('deep-session')

      wrapper.findComponent(ChatSidebarStub).vm.$emit('newConversation')
      await flushPromises()
      expect(chatStore.currentConversation).toBeNull()
      expect(router.currentRoute.value.query.conversation_id).toBeUndefined()
      if (transport === 'ws') {
        expect(wsConnectMock.mock.calls.map((call) => call[1])).toEqual([
          'deep-session',
          undefined,
        ])
      } else {
        expect(wsConnectMock).not.toHaveBeenCalled()
      }

      await sendMessage(wrapper, 'sidebar first question')
      expect(sentConversationIds(transport)).toEqual([undefined])
      const newId = `sidebar-new-${transport}-session`
      await completeFirstAnswer(transport, newId)
      await sendMessage(wrapper, 'sidebar second question')
      expect(sentConversationIds(transport)).toEqual([undefined, newId])

      // 第一轮新 ID 不会写入 URL；再次新建走的是“无 query、不触发 watcher”分支。
      await completeFirstAnswer(transport, newId, 'assistant-2')
      wrapper.findComponent(ChatSidebarStub).vm.$emit('newConversation')
      await flushPromises()
      expect(chatStore.currentConversation).toBeNull()
      await sendMessage(wrapper, 'sidebar third fresh question')
      expect(sentConversationIds(transport)).toEqual([undefined, newId, undefined])
      if (transport === 'ws') {
        expect(wsConnectMock.mock.calls.map((call) => call[1])).toEqual([
          'deep-session',
          undefined,
          undefined,
        ])
      }
    })
  }

  it('explicit conversation_id deep link still loads history and keeps its id', async () => {
    const { chatStore, wrapper } = await mountApp(
      '/chat?conversation_id=deep-session',
      { history: historyPair },
    )
    expect(getHistoryMock).toHaveBeenCalledWith('deep-session')
    expect(chatStore.currentConversation?.id).toBe('deep-session')
    expect(
      (wrapper.findComponent(ChatMessagesStub).props('messages') as ChatMessage[])
        .map((item) => item.content),
    ).toEqual(['historic question', 'historic answer'])

    await sendMessage(wrapper, 'follow-up in deep-linked session')
    expect(sentConversationIds('sse')).toEqual(['deep-session'])
  })
})
