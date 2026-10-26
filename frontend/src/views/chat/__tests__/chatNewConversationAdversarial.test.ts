import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, nextTick, onMounted, type PropType } from 'vue'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { createI18n } from 'vue-i18n'
import { NDialogProvider, NMessageProvider } from 'naive-ui'
import type { ChatMessage } from '@/types/chat'
import zh from '@/locales/zh'

const {
  sseHandlers,
  ssePostMock,
  getConversationsMock,
  getHistoryMock,
  switchOrganizationMock,
  loginWithJsonMock,
  getUserSettingsMock,
} = vi.hoisted(() => ({
  sseHandlers: new Map<string, (data: never) => void>(),
  ssePostMock: vi.fn(),
  getConversationsMock: vi.fn(),
  getHistoryMock: vi.fn(),
  switchOrganizationMock: vi.fn(),
  loginWithJsonMock: vi.fn(),
  getUserSettingsMock: vi.fn(),
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

vi.mock('@/utils/message', () => ({
  useDedupedMessage: () => ({
    error: vi.fn(),
    info: vi.fn(),
    success: vi.fn(),
    warning: vi.fn(),
    loading: vi.fn(),
  }),
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

vi.mock('@/api/knowledge', () => ({
  uploadKnowledgeBase: vi.fn(),
  getKnowledgeBase: vi.fn(),
  getDocumentDetail: vi.fn().mockResolvedValue({ data: { data: {} } }),
  getDocumentContent: vi.fn().mockResolvedValue({ data: { data: {} } }),
}))

vi.mock('@/api/organization', () => ({
  getOrganizationSettings: vi.fn().mockResolvedValue({ data: { settings: {} } }),
}))

vi.mock('@/api/user', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/user')>()
  return {
    ...actual,
    switchOrganization: switchOrganizationMock,
    getUserSettings: getUserSettingsMock,
    getMyOrganizations: vi.fn().mockResolvedValue({ data: { data: { organizations: [] } } }),
  }
})

vi.mock('@/api/auth', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/auth')>()
  return { ...actual, loginWithJson: loginWithJsonMock }
})

import ChatPage from '@/views/chat/index.vue'
import { useChatStore } from '@/stores/chat'
import { useUserStore } from '@/stores/user'
import { wsService } from '@/utils/websocket'

const i18n = createI18n({
  legacy: false,
  locale: 'zh',
  missingWarn: false,
  fallbackWarn: false,
  messages: { zh },
})

let transportUnderTest: 'sse' | 'ws' = 'sse'
let wsTransportMounts = 0

const ChatMessagesStub = defineComponent({
  name: 'ChatMessages',
  props: { messages: { type: Array as PropType<ChatMessage[]>, default: () => [] } },
  emits: ['scroll', 'scrollToBottom', 'useSuggestion', 'feedback', 'copy', 'regenerate', 'openSource'],
  setup(props) {
    return () => h('div', String(props.messages.length))
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
      if (transportUnderTest === 'ws' && wsTransportMounts === 0) {
        emit('update:useSSE', false)
        wsTransportMounts += 1
      }
    })
    return () => h('div')
  },
})

const ChatSidebarStub = defineComponent({
  name: 'ChatSidebar',
  props: {
    conversations: { type: Array as PropType<Array<{ id: string }>>, default: () => [] },
  },
  emits: ['newConversation', 'refresh', 'selectConversation', 'deleteConversation'],
  setup() {
    return () => h('div')
  },
})

const Root = defineComponent({
  setup() {
    const userStore = useUserStore()
    return () => h(NMessageProvider, null, {
      default: () => h(NDialogProvider, null, {
        default: () => h(ChatPage, { key: `Chat::org-${userStore.organizationVersion}` }),
      }),
    })
  },
})

const listResponse = (items: Array<Record<string, unknown>> = []) => ({
  data: {
    success: true,
    data: { data: items, total: items.length, page: 1, page_size: 50 },
  },
})

const historyResponse = (id: string) => ({
  data: {
    success: true,
    data: {
      id,
      title: `history-${id}`,
      created_at: '2026-10-26T00:00:00',
      updated_at: '2026-10-26T00:00:01',
      messages: [
        {
          id: `message-${id}`,
          content: `history-${id}`,
          messageType: 'user',
          createdAt: '2026-10-26T00:00:00',
        },
      ],
    },
  },
})

class FakeWebSocket {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSING = 2
  static readonly CLOSED = 3
  static instances: FakeWebSocket[] = []

  readonly url: string
  readonly protocols?: string | string[]
  readonly sent: string[] = []
  readyState = FakeWebSocket.CONNECTING
  onopen: ((event: Event) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null

  constructor(url: string | URL, protocols?: string | string[]) {
    this.url = String(url)
    this.protocols = protocols
    FakeWebSocket.instances.push(this)
  }

  open() {
    this.readyState = FakeWebSocket.OPEN
    this.onopen?.(new Event('open'))
  }

  close() {
    // Browser close is asynchronous: messages and the close event can still be queued.
    this.readyState = FakeWebSocket.CLOSING
  }

  send(data: string) {
    if (this.readyState !== FakeWebSocket.OPEN) throw new Error('socket is not open')
    this.sent.push(data)
  }

  emitMessage(payload: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent)
  }

  emitClose() {
    this.readyState = FakeWebSocket.CLOSED
    this.onclose?.(new CloseEvent('close'))
  }
}

async function mountChat(initialPath: string, storedId = 'old-session') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/chat', name: 'Chat', component: { template: '<div />' } },
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
    id: storedId,
    title: 'stale title',
    userId: 7,
    createdAt: '2026-10-26T00:00:00',
    updatedAt: '2026-10-26T00:00:01',
  })
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
        'n-button': true,
        'n-icon': true,
        'n-progress': true,
        'n-skeleton': true,
        'n-tag': true,
      },
    },
  })
  await nextTick()
  return { wrapper, router, chatStore, userStore }
}

function visibleMessages(wrapper: ReturnType<typeof mount>): string[] {
  const messages = wrapper.findComponent(ChatMessagesStub).props('messages') as ChatMessage[]
  return messages.map((item) => item.content)
}

async function sendViaInput(wrapper: ReturnType<typeof mount>, content: string) {
  const input = wrapper.findComponent(ChatInputStub)
  input.vm.$emit('update:inputMessage', content)
  await nextTick()
  input.vm.$emit('send')
  await flushPromises()
}

async function finishMount() {
  await flushPromises()
  await flushPromises()
}

async function mountOpenWsChat() {
  transportUnderTest = 'ws'
  const context = await mountChat('/chat?conversation_id=deep-session', 'deep-session')
  await finishMount()
  expect(FakeWebSocket.instances).toHaveLength(1)
  const first = FakeWebSocket.instances[0]
  expect(first.url).toContain('conversation_id=deep-session')
  first.open()
  await nextTick()
  expect(wsService.isConnected()).toBe(true)
  return { ...context, first }
}

async function resetToBlankWs(context: {
  wrapper: ReturnType<typeof mount>
  router: Router
  chatStore: ReturnType<typeof useChatStore>
}) {
  context.wrapper.findComponent(ChatSidebarStub).vm.$emit('newConversation')
  await finishMount()
  expect(context.router.currentRoute.value.query.conversation_id).toBeUndefined()
  expect(context.chatStore.currentConversation).toBeNull()
  expect(FakeWebSocket.instances).toHaveLength(2)
  const second = FakeWebSocket.instances[1]
  expect(second.url).not.toContain('conversation_id=')
  second.open()
  await nextTick()
  expect(wsService.isConnected()).toBe(true)
  return second
}

async function loginAsB(userStore: ReturnType<typeof useUserStore>) {
  loginWithJsonMock.mockResolvedValueOnce({
    data: {
      data: {
        access_token: 'token-B',
        refresh_token: 'refresh-B',
        expires_in: 3600,
        user_info: {
          id: 8,
          username: 'user-B',
          email: 'b@example.invalid',
          organization_id: 1,
          is_active: true,
        },
      },
    },
  })
  const result = await userStore.login({ username: 'user-B', password: 'secret' })
  expect(result.success).toBe(true)
  await finishMount()
}

beforeEach(() => {
  wsService.disconnect()
  for (const event of ['connect', 'disconnect', 'error', 'message', 'chunk']) wsService.off(event)
  vi.clearAllMocks()
  localStorage.clear()
  localStorage.setItem('docmind_token', 'tok')
  transportUnderTest = 'sse'
  wsTransportMounts = 0
  sseHandlers.clear()
  ssePostMock.mockResolvedValue(true)
  getConversationsMock.mockImplementation(async () => listResponse())
  getHistoryMock.mockImplementation(async (id: string) => historyResponse(id))
  switchOrganizationMock.mockImplementation(async (orgId: number) => ({
    data: { success: true, data: { organization_id: orgId, is_org_admin: false } },
  }))
  getUserSettingsMock.mockImplementation(async () => ({ data: { data: {} } }))
  FakeWebSocket.instances = []
  vi.stubGlobal('WebSocket', FakeWebSocket)
})

afterEach(() => {
  wsService.disconnect()
  for (const event of ['connect', 'disconnect', 'error', 'message', 'chunk']) wsService.off(event)
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

enableAutoUnmount(afterEach)

describe('independent adversarial review: initial route snapshot', () => {
  it('deep link -> blank during the initial slow list must not reload the captured old id', async () => {
    let releaseList!: () => void
    const listGate = new Promise<void>((resolve) => { releaseList = resolve })
    getConversationsMock.mockImplementation(async () => {
      await listGate
      return listResponse()
    })
    const { wrapper, router, chatStore } = await mountChat('/chat?conversation_id=old-session')

    await router.push('/chat')
    await flushPromises()
    expect(chatStore.currentConversation).toBeNull()
    expect(getHistoryMock).not.toHaveBeenCalled()

    releaseList()
    await finishMount()
    const observed = {
      currentId: chatStore.currentConversation?.id ?? null,
      messages: visibleMessages(wrapper),
      historyRequests: getHistoryMock.mock.calls.map((call) => call[0]),
    }
    expect(observed).toEqual({ currentId: null, messages: [], historyRequests: [] })
  })

  it('deep link A -> deep link B during the initial slow list must not reload A after B', async () => {
    let releaseList!: () => void
    const listGate = new Promise<void>((resolve) => { releaseList = resolve })
    getConversationsMock.mockImplementation(async () => {
      await listGate
      return listResponse()
    })
    const { wrapper, router, chatStore } = await mountChat('/chat?conversation_id=old-session')

    await router.push('/chat?conversation_id=new-session')
    await flushPromises()
    expect(chatStore.currentConversation?.id).toBe('new-session')

    releaseList()
    await finishMount()
    const observed = {
      currentId: chatStore.currentConversation?.id ?? null,
      messages: visibleMessages(wrapper),
      historyRequests: getHistoryMock.mock.calls.map((call) => call[0]),
    }
    expect(observed).toEqual({
      currentId: 'new-session',
      messages: ['history-new-session'],
      historyRequests: ['new-session'],
    })
  })

  it('an old history response already in flight must not repopulate a blank route', async () => {
    let releaseHistory!: () => void
    const historyGate = new Promise<void>((resolve) => { releaseHistory = resolve })
    getHistoryMock.mockImplementation(async (id: string) => {
      await historyGate
      return historyResponse(id)
    })
    const { wrapper, router, chatStore } = await mountChat('/chat?conversation_id=old-session')
    await flushPromises()
    expect(getHistoryMock).toHaveBeenCalledWith('old-session')

    await router.push('/chat')
    await flushPromises()
    expect(chatStore.currentConversation).toBeNull()
    releaseHistory()
    await finishMount()

    const observed = {
      currentId: chatStore.currentConversation?.id ?? null,
      messages: visibleMessages(wrapper),
    }
    expect(observed).toEqual({ currentId: null, messages: [] })
  })

  it('an older list response must not overwrite the latest generation list', async () => {
    const pending: Array<(value: ReturnType<typeof listResponse>) => void> = []
    getConversationsMock.mockImplementation(() => (
      new Promise<ReturnType<typeof listResponse>>((resolve) => pending.push(resolve))
    ))
    const { wrapper, router, chatStore } = await mountChat('/chat?conversation_id=old-session')
    expect(pending).toHaveLength(1)

    await router.push('/chat')
    await flushPromises()
    expect(pending).toHaveLength(2)
    const item = (id: string) => ({
      id,
      title: id,
      created_at: '2026-10-26T00:00:00',
      updated_at: '2026-10-26T00:00:01',
      message_count: 0,
    })
    // 新代次先完成，旧响应最后到达；最终仍只能看到新代次列表。
    pending[1](listResponse([item('new-list')]))
    await flushPromises()
    pending[0](listResponse([item('old-list')]))
    await finishMount()

    const conversations = wrapper.findComponent(ChatSidebarStub).props('conversations') as Array<{ id: string }>
    expect(conversations.map((item) => item.id)).toEqual(['new-list'])
    expect(chatStore.currentConversation).toBeNull()
  })
})

describe('independent adversarial review: late SSE completion', () => {
  it('an old SSE terminal event after sidebar reset must not restore the old id', async () => {
    const context = await mountChat('/chat?conversation_id=old-session')
    await finishMount()
    await sendViaInput(context.wrapper, 'question in old session')
    expect(ssePostMock.mock.calls.map((call) => call[1].conversationId)).toEqual(['old-session'])

    context.wrapper.findComponent(ChatSidebarStub).vm.$emit('newConversation')
    await finishMount()
    expect(context.chatStore.currentConversation).toBeNull()

    const oldTerminal = sseHandlers.get('message')
    expect(oldTerminal).toBeDefined()
    oldTerminal!({
      type: 'message',
      content: 'late old terminal answer',
      conversationId: 'old-session',
      messageId: 'old-assistant',
    } as never)
    await flushPromises()
    await sendViaInput(context.wrapper, 'intended fresh question')

    const observed = {
      currentId: context.chatStore.currentConversation?.id ?? null,
      outgoingIds: ssePostMock.mock.calls.map((call) => call[1].conversationId ?? null),
    }
    expect(observed).toEqual({ currentId: null, outgoingIds: ['old-session', null] })
  })
})

describe('independent adversarial review: real wsService socket ownership', () => {
  it('a late message from the replaced socket must not restore the old id or poison the next send', async () => {
    const context = await mountOpenWsChat()
    const second = await resetToBlankWs(context)

    context.first.emitMessage({
      type: 'chunk',
      content: 'late old answer',
      conversationId: 'deep-session',
      messageId: 'late-old-message',
    })
    await flushPromises()

    const input = context.wrapper.findComponent(ChatInputStub)
    input.vm.$emit('update:inputMessage', 'intended fresh question')
    await nextTick()
    input.vm.$emit('send')
    await flushPromises()

    const observed = {
      currentId: context.chatStore.currentConversation?.id ?? null,
      messages: visibleMessages(context.wrapper),
      outgoingIds: second.sent.map((raw) => {
        const body = JSON.parse(raw) as Record<string, unknown>
        return body.conversationId ?? null
      }),
    }
    // 合法的新 user 发送会立即上屏；关键是 late old answer 不得混入其中。
    expect(observed).toEqual({
      currentId: null,
      messages: ['intended fresh question'],
      outgoingIds: [null],
    })
  })

  it('a late close from the replaced socket must not disconnect or reconnect over its replacement', async () => {
    const context = await mountOpenWsChat()
    const second = await resetToBlankWs(context)
    vi.useFakeTimers()

    context.first.emitClose()
    const statusAfterOldClose = wsService.getConnectionStatus()
    vi.advanceTimersByTime(2000)

    const observed = {
      statusAfterOldClose,
      socketCount: FakeWebSocket.instances.length,
      replacementStillCurrent: wsService.send('probe', undefined),
      replacementSentCount: second.sent.length,
    }
    expect(observed).toEqual({
      statusAfterOldClose: 'connected',
      socketCount: 2,
      replacementStillCurrent: true,
      replacementSentCount: 1,
    })
  })

  it('an old-org socket event after organization remount must not poison the new-org SSE send', async () => {
    transportUnderTest = 'ws'
    const context = await mountChat('/chat', 'old-session')
    await finishMount()
    expect(FakeWebSocket.instances).toHaveLength(1)
    const oldOrgSocket = FakeWebSocket.instances[0]
    expect(oldOrgSocket.url).not.toContain('conversation_id=')
    oldOrgSocket.open()
    await nextTick()

    await context.userStore.switchOrganization(2)
    await finishMount()
    expect(context.userStore.currentOrgId).toBe(2)
    expect(context.chatStore.currentConversation).toBeNull()
    expect(FakeWebSocket.instances).toHaveLength(1)

    oldOrgSocket.emitMessage({
      type: 'chunk',
      content: 'late old-org answer',
      conversationId: 'old-org-session',
      messageId: 'old-org-message',
    })
    await flushPromises()
    await sendViaInput(context.wrapper, 'intended new-org fresh question')

    const observed = {
      organizationId: context.userStore.currentOrgId,
      currentId: context.chatStore.currentConversation?.id ?? null,
      outgoingIds: ssePostMock.mock.calls.map((call) => call[1].conversationId ?? null),
    }
    expect(observed).toEqual({ organizationId: 2, currentId: null, outgoingIds: [null] })
  })
})

describe('direct A to B login without Chat remount', () => {
  it('old WS events cannot restore A; B uses a fresh socket and only the B new id', async () => {
    const onSpy = vi.spyOn(wsService, 'on')
    const context = await mountOpenWsChat()
    const oldChunkHandler = onSpy.mock.calls.find((call) => call[0] === 'chunk')?.[1]
    expect(oldChunkHandler).toBeDefined()
    const pageUid = (context.wrapper.findComponent(ChatPage).vm as unknown as { $: { uid: number } }).$.uid
    const organizationVersion = context.userStore.organizationVersion
    context.first.emitMessage({
      type: 'chunk',
      content: 'A answer before login',
      conversationId: 'deep-session',
      messageId: 'a-assistant',
    })
    await flushPromises()
    localStorage.setItem('language', 'fr')
    localStorage.setItem('theme', 'dark')

    await loginAsB(context.userStore)
    expect(context.userStore.userInfo?.id).toBe(8)
    expect(context.userStore.organizationVersion).toBe(organizationVersion)
    expect(
      (context.wrapper.findComponent(ChatPage).vm as unknown as { $: { uid: number } }).$.uid,
    ).toBe(pageUid)
    expect(localStorage.getItem('language')).toBe('fr')
    expect(localStorage.getItem('theme')).toBe('dark')

    const second = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    if (second !== context.first) second.open()
    await nextTick()
    context.first.emitMessage({
      type: 'chunk',
      content: 'late A answer after login',
      conversationId: 'deep-session',
      messageId: 'late-a-assistant',
    })
    // 已进入旧回调队列的事件还必须被 composable 的复合 owner 独立拒绝。
    oldChunkHandler!({
      type: 'chunk',
      content: 'late A queued handler answer',
      conversationId: 'deep-session',
      messageId: 'late-a-queued-assistant',
    } as never)
    await flushPromises()
    await sendViaInput(context.wrapper, 'B first question')

    const firstRound = {
      currentId: context.chatStore.currentConversation?.id ?? null,
      messages: visibleMessages(context.wrapper),
      outgoingIds: second.sent.map((raw) => JSON.parse(raw).conversationId ?? null),
      socketCount: FakeWebSocket.instances.length,
    }
    expect(firstRound).toEqual({
      currentId: null,
      messages: ['B first question'],
      outgoingIds: [null],
      socketCount: 2,
    })

    second.emitMessage({
      type: 'chunk',
      content: 'B answer',
      conversationId: 'b-ws-session',
      messageId: 'b-assistant',
    })
    second.emitMessage({
      type: 'message',
      content: 'B answer',
      conversationId: 'b-ws-session',
      messageId: 'b-assistant',
    })
    await flushPromises()
    await sendViaInput(context.wrapper, 'B second question')
    expect(second.sent.map((raw) => JSON.parse(raw).conversationId ?? null)).toEqual([
      null,
      'b-ws-session',
    ])
    expect(context.chatStore.currentConversation?.id).toBe('b-ws-session')
    expect(visibleMessages(context.wrapper)).toEqual([
      'B first question',
      'B answer',
      'B second question',
    ])
  })

  it('old SSE terminal and visible messages cannot cross into B; B two-turn ids stay correct', async () => {
    const context = await mountChat('/chat?conversation_id=old-session')
    await finishMount()
    const pageUid = (context.wrapper.findComponent(ChatPage).vm as unknown as { $: { uid: number } }).$.uid
    const organizationVersion = context.userStore.organizationVersion
    await sendViaInput(context.wrapper, 'A question')
    expect(ssePostMock.mock.calls.map((call) => call[1].conversationId)).toEqual(['old-session'])
    const oldTerminal = sseHandlers.get('message')
    expect(oldTerminal).toBeDefined()

    await loginAsB(context.userStore)
    expect(context.userStore.userInfo?.id).toBe(8)
    expect(context.userStore.organizationVersion).toBe(organizationVersion)
    expect(
      (context.wrapper.findComponent(ChatPage).vm as unknown as { $: { uid: number } }).$.uid,
    ).toBe(pageUid)
    oldTerminal!({
      type: 'message',
      content: 'late A terminal answer',
      conversationId: 'old-session',
      messageId: 'a-assistant',
    } as never)
    await flushPromises()
    await sendViaInput(context.wrapper, 'B first question')

    expect(context.chatStore.currentConversation).toBeNull()
    expect(visibleMessages(context.wrapper)).toEqual(['B first question', ''])
    expect(ssePostMock.mock.calls.map((call) => call[1].conversationId ?? null)).toEqual([
      'old-session',
      null,
    ])

    sseHandlers.get('message')!({
      type: 'message',
      content: 'B answer',
      conversationId: 'b-sse-session',
      messageId: 'b-assistant',
    } as never)
    await flushPromises()
    await sendViaInput(context.wrapper, 'B second question')
    expect(ssePostMock.mock.calls.map((call) => call[1].conversationId ?? null)).toEqual([
      'old-session',
      null,
      'b-sse-session',
    ])
    expect(context.chatStore.currentConversation?.id).toBe('b-sse-session')
    expect(visibleMessages(context.wrapper)).toEqual([
      'B first question',
      'B answer',
      'B second question',
      '',
    ])
  })

  it('old history completing after direct login cannot repopulate A into B', async () => {
    let releaseHistory!: () => void
    const historyGate = new Promise<void>((resolve) => { releaseHistory = resolve })
    getHistoryMock.mockImplementation(async (id: string) => {
      await historyGate
      return historyResponse(id)
    })
    const context = await mountChat('/chat?conversation_id=old-session')
    await flushPromises()
    expect(getHistoryMock).toHaveBeenCalledWith('old-session')

    await loginAsB(context.userStore)
    releaseHistory()
    await finishMount()
    expect(context.userStore.userInfo?.id).toBe(8)
    expect(context.chatStore.currentConversation).toBeNull()
    expect(visibleMessages(context.wrapper)).toEqual([])

    await sendViaInput(context.wrapper, 'B first question')
    expect(ssePostMock.mock.calls.map((call) => call[1].conversationId ?? null)).toEqual([null])
    expect(visibleMessages(context.wrapper)).toEqual(['B first question', ''])
  })

  it('old list completing after direct login cannot overwrite the B list', async () => {
    const pending: Array<(value: ReturnType<typeof listResponse>) => void> = []
    getConversationsMock.mockImplementation(() => (
      new Promise<ReturnType<typeof listResponse>>((resolve) => pending.push(resolve))
    ))
    const context = await mountChat('/chat')
    expect(pending).toHaveLength(1)
    const item = (id: string) => ({
      id,
      title: id,
      created_at: '2026-10-26T00:00:00',
      updated_at: '2026-10-26T00:00:01',
      message_count: 0,
    })

    await loginAsB(context.userStore)
    if (pending.length > 1) {
      pending[1](listResponse([item('b-list')]))
      await flushPromises()
    }
    pending[0](listResponse([item('a-list')]))
    await finishMount()

    const conversations = context.wrapper.findComponent(ChatSidebarStub).props('conversations') as Array<{ id: string }>
    expect({
      requestCount: pending.length,
      listIds: conversations.map((item) => item.id),
      currentId: context.chatStore.currentConversation?.id ?? null,
      userId: context.userStore.userInfo?.id,
    }).toEqual({ requestCount: 2, listIds: ['b-list'], currentId: null, userId: 8 })
  })
})
