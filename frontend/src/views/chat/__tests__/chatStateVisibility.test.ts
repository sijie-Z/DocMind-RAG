/**
 * E7（体验批）：聊天三态可见性判别测试（先红后绿）。
 *
 * 背景（修前）：
 * 1) loadConversation 直接复用生成态 isLoading —— 切换会话加载历史时，界面误现
 *    「停止生成」按钮、生成中的打字 loading 与流式光标，历史加载/生成中互不区分；
 * 2) SSE thinking 事件（reasoning/planning/correction）已落账到 message.thinking，
 *    但 ChatMessages 零渲染——推理过程对用户不可见；
 * 3) 停止生成后无任何标记，用户无法分辨「回答到此为止」是完成还是被中断。
 *
 * 修复后：
 * - 新增 isLoadingHistory（与 isLoading 分离），历史加载仅显示独立的历史加载指示，
 *   不出现停止按钮/生成 loading/流式光标；
 * - thinking 渲染为默认收起的折叠区，点击展开；
 * - 停止生成给最后一条 assistant 消息打 stopped 标记，渲染「已停止」+ 继续提示。
 *
 * 变异点（回退判别）：
 * - loadConversation 改回复用 isLoading → 「加载历史不出现停止生成」红；
 * - 去掉 thinking 折叠块 → thinking 渲染/展开用例红；
 * - 去掉 stopped 渲染，或停止时不打标 → 已停止用例红；
 * - 停止改回 useChatConnection 版本（不 abort SSE / 不落标记）→ 停止用例红。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { defineComponent, h, ref, nextTick, type Ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createI18n } from 'vue-i18n'
import naive, { NDialogProvider } from 'naive-ui'

const { messageSpies, sseHandlers } = vi.hoisted(() => ({
  messageSpies: { error: vi.fn(), info: vi.fn(), success: vi.fn(), warning: vi.fn(), loading: vi.fn() },
  sseHandlers: {} as Record<string, (data: unknown) => void>,
}))

vi.mock('@/utils/message', () => ({ useDedupedMessage: () => messageSpies }))

vi.mock('@/utils/sseService', () => ({
  sseService: {
    on: vi.fn((ev: string, fn: (data: unknown) => void) => { sseHandlers[ev] = fn }),
    off: vi.fn(),
    post: vi.fn().mockResolvedValue(true),
    abort: vi.fn(),
  },
}))

// E5 起虚拟滚动为 ChatMessages 局部引入（不再经 main.ts 全局注册），测试侧
// 与既有 chat 用例统一：把模块替换为共享直通 stub（按 items 逐项渲染 default slot）。
vi.mock('vue-virtual-scroller', async () => {
  const stubs = await import('@/test-utils/virtualScroller')
  return { DynamicScroller: stubs.DynamicScrollerStub, DynamicScrollerItem: stubs.DynamicScrollerItemStub }
})

vi.mock('@/api/chat', () => ({
  getConversationMessages: vi.fn(),
  clearConversationMessages: vi.fn().mockResolvedValue({ data: { success: true } }),
  updateMessageFeedback: vi.fn().mockResolvedValue({ data: {} }),
  unbindConversationDocs: vi.fn().mockResolvedValue({ data: { success: true } }),
  parseUpload: vi.fn().mockResolvedValue({ content: 'x', filename: 'f', chunk_count: 1, file_size: 1 }),
}))

vi.mock('@/api/conversation', () => ({
  getConversations: vi.fn().mockResolvedValue({ data: { data: { data: [], total: 0 } } }),
  deleteConversation: vi.fn().mockResolvedValue(undefined),
  createConversation: vi.fn().mockResolvedValue({ data: { data: {} } }),
  updateConversation: vi.fn().mockResolvedValue({ data: { data: {} } }),
  getConversation: vi.fn(),
  getConversationMessages: vi.fn(),
  clearConversationMessages: vi.fn(),
  unbindConversationDocs: vi.fn(),
  updateMessageFeedback: vi.fn(),
}))

vi.mock('@/api/knowledge', () => ({
  uploadKnowledgeBase: vi.fn().mockResolvedValue({ data: { success: true, data: { id: 1 } } }),
  getKnowledgeBase: vi.fn().mockResolvedValue({ data: { data: { status: 'indexed' } } }),
  getDocumentDetail: vi.fn().mockResolvedValue({ data: { data: {} } }),
  getDocumentContent: vi.fn().mockResolvedValue({ data: { data: {} } }),
}))

import ChatMessages from '../components/ChatMessages.vue'
import ChatInput from '../components/ChatInput.vue'
import ChatPage from '../index.vue'
import { useChatSend } from '../composables/useChatSend'
import { sseService } from '@/utils/sseService'
import { getConversationMessages } from '@/api/chat'
import { useUserStore } from '@/stores/user'
import type { ChatMessage } from '@/types/chat'

// DynamicScroller 为全局注册（main.ts app.use(VueVirtualScroller)），测试侧用
// 直通 stub 复现「按 items 逐项渲染 default slot」的行为。
const DynamicScroller = defineComponent({
  props: { items: { type: Array, default: () => [] } },
  setup(props, { slots }) {
    return () =>
      h('div', (props.items as unknown[]).map((item, index) => slots.default?.({ item, index, active: true })))
  },
})
const DynamicScrollerItem = defineComponent({
  setup(_, { slots }) {
    return () => h('div', slots.default?.())
  },
})

// Markdown stub：把 content 原样渲染为文本（本文件不校验 Markdown 解析）
const MarkdownStub = defineComponent({
  name: 'Markdown',
  props: { content: { type: String, default: '' }, streaming: { type: Boolean, default: false } },
  setup(props) {
    return () => h('div', { class: 'markdown-stub' }, props.content)
  },
})

const i18n = createI18n({
  legacy: false,
  locale: 'zh',
  missingWarn: false,
  fallbackWarn: false,
  messages: {
    zh: {
      chat: {
        stopGenerating: '停止生成',
        loadingHistory: '正在加载历史消息...',
        thinkingLabel: '思考过程',
        generationStopped: '已停止',
        stoppedHint: '可继续提问或重新生成',
        sourcesLabel: '来源',
        sourceDoc: '文档',
        unknownDoc: '未知文档',
        noPreview: '无预览',
        relevanceScore: '相关度 {score}',
        useful: '有用',
        notUseful: '没用',
        regenerate: '重新生成',
        backToBottom: '回到底部',
        welcome: '欢迎',
        welcomeDesc: '描述',
        analyzeFiles: '分析文件',
        sendFailed: '发送失败',
        networkError: '网络错误',
        waitForUpload: '等待上传',
        waitForParsing: '等待解析',
        removeFailedAttachments: '移除失败附件',
        attachmentsNotReady: '附件未就绪',
        connecting: '连接中',
        retrying: '重试中',
        inputPlaceholder: '输入',
        send: '发送',
        disclaimer: '免责声明',
        uploadFile: '上传文件',
        streamResponse: '流式响应',
        privacyMode: '隐私模式',
        sseMode: 'SSE模式',
        fileUploading: '上传中',
        fileParsing: '解析中',
        fileIndexing: '索引中',
        fileCompleted: '已完成',
        fileFailed: '解析失败',
        attachmentModeOnly: '仅附件模式',
        strictModeTitle: '强约束模式',
        strictModeDesc: '描述',
      },
    },
  },
})

const assistantMsg = (patch: Partial<ChatMessage> = {}): ChatMessage => ({
  id: 1, content: '答案', messageType: 'assistant', conversationId: 1, ...patch,
})

function mountMessages(messages: ChatMessage[], extra: Record<string, unknown> = {}) {
  return mount(ChatMessages, {
    props: { messages, isLoading: false, showBackToBottom: false, userAvatar: '', suggestions: [], ...extra },
    global: {
      plugins: [i18n],
      components: { DynamicScroller, DynamicScrollerItem },
      stubs: {
        Markdown: MarkdownStub,
        PlanTree: true, ReflectionBanner: true, RetryHintLine: true, RunReportBlock: true,
        'n-icon': true, 'n-avatar': true, 'n-button': true, 'n-tooltip': true,
        'n-tag': true, 'n-popover': true,
      },
    },
  })
}

describe('E7 · ChatMessages 三态可见性（历史加载 / 生成中 / 已停止）', () => {
  it('历史加载态：独立指示可见，不出现生成 loading 与流式光标', () => {
    const wrapper = mountMessages([], { isLoadingHistory: true, isLoading: false })
    expect(wrapper.find('[data-testid="history-loading"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('正在加载历史消息')
    // 历史加载 ≠ 生成中：生成 loading 与光标不得出现
    expect(wrapper.find('[data-testid="generation-loading"]').exists()).toBe(false)
    expect(wrapper.find('.animate-blink').exists()).toBe(false)
    wrapper.unmount()
  })

  it('生成态：只有生成 loading，不出现历史加载指示', () => {
    const wrapper = mountMessages([assistantMsg()], { isLoading: true, isLoadingHistory: false })
    expect(wrapper.find('[data-testid="generation-loading"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="history-loading"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('thinking 折叠区：默认收起，点击展开可见推理内容', async () => {
    const wrapper = mountMessages([
      assistantMsg({ thinking: '先分析问题THINK-ALPHA，再组织答案' }),
    ])
    const block = wrapper.find('[data-testid="thinking-block"]')
    expect(block.exists()).toBe(true)
    // 默认收起：内容不渲染（v-if）
    expect(wrapper.find('[data-testid="thinking-content"]').exists()).toBe(false)

    await wrapper.find('[data-testid="thinking-toggle"]').trigger('click')
    expect(wrapper.find('[data-testid="thinking-content"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="thinking-content"]').text()).toContain('THINK-ALPHA')

    // 再次点击收起
    await wrapper.find('[data-testid="thinking-toggle"]').trigger('click')
    expect(wrapper.find('[data-testid="thinking-content"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('无 thinking 字段的消息不渲染折叠区', () => {
    const wrapper = mountMessages([assistantMsg()])
    expect(wrapper.find('[data-testid="thinking-block"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('已停止态：显示「已停止」标记与继续提示；未停止消息不显示', () => {
    const stopped = mountMessages([assistantMsg({ stopped: true })])
    expect(stopped.find('[data-testid="stopped-notice"]').exists()).toBe(true)
    expect(stopped.text()).toContain('已停止')
    expect(stopped.text()).toContain('继续')
    stopped.unmount()

    const normal = mountMessages([assistantMsg()])
    expect(normal.find('[data-testid="stopped-notice"]').exists()).toBe(false)
    normal.unmount()
  })
})

describe('E7 · 聊天页历史加载：不出现「停止生成」', () => {
  const DETAIL_RESPONSE = {
    success: true,
    message: '获取成功',
    data: {
      id: 'conv-1',
      title: '测试会话',
      messages: [
        { id: 'm1', content: '历史提问', messageType: 'user', createdAt: '2026-10-10T00:00:00', sources: [] },
        { id: 'm2', content: '历史回答', messageType: 'assistant', createdAt: '2026-10-10T00:01:00', sources: [] },
      ],
      created_at: '2026-10-10T00:00:00',
      updated_at: '',
    },
  }

  const Host = defineComponent({
    setup() {
      return () => h(NDialogProvider, null, { default: () => h(ChatPage) })
    },
  })

  async function mountChat(deferred: Promise<unknown>): Promise<VueWrapper> {
    vi.mocked(getConversationMessages).mockReturnValue(deferred as never)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/chat', name: 'Chat', component: { template: '<div />' } },
        { path: '/login', name: 'Login', component: { template: '<div />' } },
      ],
    })
    await router.push('/chat?conversation_id=conv-1')
    await router.isReady()
    const pinia = createPinia()
    setActivePinia(pinia)
    const userStore = useUserStore()
    userStore.userInfo = { id: 1, username: 'u' } as never
    const wrapper = mount(Host, {
      global: {
        plugins: [naive, pinia, router, i18n],
        components: { DynamicScroller, DynamicScrollerItem },
        stubs: {
          ChatSidebar: true,
          ChatHeader: true,
          DocumentPreviewModal: true,
          DebugPanel: true,
          Markdown: MarkdownStub,
          PlanTree: true, ReflectionBanner: true, RetryHintLine: true, RunReportBlock: true,
        },
      },
    })
    await flushPromises()
    return wrapper
  }

  beforeEach(() => {
    vi.clearAllMocks()
    for (const k of Object.keys(sseHandlers)) delete sseHandlers[k]
    localStorage.setItem('docmind_token', 'tok')
  })

  it('历史请求挂起期间：不显示停止生成按钮，只显示历史加载指示；完成后正常落消息', async () => {
    let resolveHistory!: (value: unknown) => void
    const deferred = new Promise((resolve) => { resolveHistory = resolve })
    const wrapper = await mountChat(deferred)

    // 修前：loadConversation 置 isLoading=true → ChatInput 渲染「停止生成」→ 红
    expect(wrapper.findComponent(ChatInput).exists()).toBe(true)
    expect(wrapper.text()).not.toContain('停止生成')
    expect(wrapper.find('[data-testid="history-loading"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="generation-loading"]').exists()).toBe(false)

    resolveHistory({ data: DETAIL_RESPONSE })
    await flushPromises()
    await flushPromises()

    expect(wrapper.find('[data-testid="history-loading"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('历史回答')
    expect(wrapper.text()).not.toContain('停止生成')
    wrapper.unmount()
  })

  it('停止生成：中止传输并给最后一条 assistant 消息打「已停止」标记（走 useChatSend 停止链路）', async () => {
    const wrapper = await mountChat(Promise.resolve({ data: DETAIL_RESPONSE }))
    await flushPromises()
    const vm = wrapper.findComponent(ChatPage).vm as unknown as {
      inputMessage: string
      handleSend: () => Promise<void>
      messages: ChatMessage[]
    }

    vm.inputMessage = '再问一句'
    await vm.handleSend()
    await flushPromises()
    expect(vm.messages[vm.messages.length - 1].messageType).toBe('assistant')

    vi.mocked(sseService.abort).mockClear()
    wrapper.findComponent(ChatInput).vm.$emit('stopGeneration')
    await nextTick()

    expect(sseService.abort).toHaveBeenCalled()
    expect(vm.messages[vm.messages.length - 1].stopped).toBe(true)
    wrapper.unmount()
  })
})

describe('E7 · useChatSend 停止生成落标记', () => {
  let messages!: Ref<ChatMessage[]>
  let isLoading!: Ref<boolean>
  let stop!: () => void
  let send!: (text: string) => Promise<void>
  let wrapper!: VueWrapper

  const Host = defineComponent({
    setup() {
      messages = ref<ChatMessage[]>([])
      isLoading = ref(false)
      const attachedFiles = ref([])
      const attachedFileIds = ref<string[]>([])
      const isRetrieving = ref(false)
      const sseStatus = ref('disconnected')
      const useSSE = ref(true)
      const useStream = ref(true)
      const { handleSSESend, stopGeneration } = useChatSend(
        messages, attachedFiles, attachedFileIds,
        () => {}, async () => {},
        isLoading, isRetrieving, sseStatus, useSSE, useStream,
      )
      send = (text: string) => handleSSESend(text)
      stop = stopGeneration
      return () => h('div')
    },
  })

  beforeEach(() => {
    setActivePinia(createPinia())
    for (const k of Object.keys(sseHandlers)) delete sseHandlers[k]
    wrapper = mount(Host, { global: { plugins: [i18n] } })
  })

  afterEach(() => {
    wrapper.unmount()
    vi.mocked(sseService.abort).mockClear()
  })

  it('生成中停止：最后一条 assistant 消息 stopped=true，SSE 被中止', async () => {
    await send('测试问题')
    await nextTick()
    expect(isLoading.value).toBe(true)

    stop()

    expect(isLoading.value).toBe(false)
    expect(sseService.abort).toHaveBeenCalled()
    const last = messages.value[messages.value.length - 1]
    expect(last.messageType).toBe('assistant')
    expect(last.stopped).toBe(true)
  })

  it('未处于生成态时停止：不给历史消息打标（避免误标）', async () => {
    messages.value = [
      { id: 'h1', content: '历史', messageType: 'assistant', conversationId: 1 },
    ]
    stop()
    expect(messages.value[0].stopped).toBeUndefined()
  })
})
