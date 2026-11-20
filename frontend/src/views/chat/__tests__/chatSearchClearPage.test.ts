/**
 * 会话搜索「清除按钮」页面级接线判别（M5）。
 *
 * 既有契约分别测了 ChatSidebar 的 searchClear emit 与 useChatSessions 的
 * setConversationSearch，但没有测聊天页把两者接起来的那一行：
 *   index.vue: onConversationSearchClear -> setConversationSearch('', { immediate: true })
 * 本用例真实挂载 ChatPage + ChatSidebar，点击侧栏清除按钮，断言页面接线调用
 * setter、立即刷新列表且请求不带 search 参数。
 *
 * 变异点：把 index.vue 的 onConversationSearchClear 改成 noop → 本用例红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createI18n } from 'vue-i18n'
import naive, { NDialogProvider } from 'naive-ui'

const setConversationSearch = vi.fn((value: string, options: { immediate?: boolean } = {}) => {
  // 模拟 composable 的 immediate 语义：清空查询、回到第 1 页并立即刷新列表。
  const next = value.trim()
  conversationSearch.value = next
  if (options.immediate) {
    currentPage.value = 1
    void fetchConversations(next)
  }
})
const currentPage = ref(1)
const conversations = ref<Array<{ id: string; title: string; created_at: string; updated_at: string; message_count: number; last_message: string }>>([])
const isListLoading = ref(false)
const convLoadError = ref(false)
const sidebarOpen = ref(true)
const conversationSearch = ref('合同')
const currentConversationId = ref<string | undefined>(undefined)
const fetchConversations = vi.fn(async (_search?: string) => {})

vi.mock('@/views/chat/composables/useChatSessions', () => ({
  useChatSessions: () => ({
    conversations,
    isListLoading,
    sidebarOpen,
    currentConversationId,
    conversationSearch,
    setConversationSearch,
    fetchConversations,
    handleDeleteConversation: vi.fn(),
    convLoadError,
    currentPage,
    hasMore: ref(false),
    loadMore: vi.fn(),
  }),
}))

vi.mock('@/utils/message', () => ({
  useDedupedMessage: () => ({
    success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn(), loading: vi.fn(),
  }),
}))

vi.mock('@/api/chat', () => ({
  getConversationMessages: vi.fn(),
  clearConversationMessages: vi.fn().mockResolvedValue({ data: { success: true } }),
  updateMessageFeedback: vi.fn().mockResolvedValue({ data: {} }),
  unbindConversationDocs: vi.fn().mockResolvedValue({ data: { success: true } }),
  parseUpload: vi.fn().mockResolvedValue({ content: 'x', filename: 'f', chunk_count: 1, file_size: 1 }),
}))

vi.mock('@/api/conversation', () => ({
  getConversations: vi.fn(),
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
  getDocumentChunk: vi.fn(),
}))

vi.mock('@/api/organization', () => ({
  getOrgPrivacyPolicy: vi.fn().mockResolvedValue({ data: null }),
}))

vi.mock('@/api/health', () => ({
  getHealth: vi.fn().mockResolvedValue({ version: '9.9.9' }),
}))

import ChatPage from '../index.vue'
import zh from '@/locales/zh'
import { useUserStore } from '@/stores/user'

const i18n = createI18n({
  legacy: false,
  locale: 'zh',
  missingWarn: false,
  fallbackWarn: false,
  messages: { zh },
})

// 原生替身：让搜索输入与「清除搜索」按钮在 DOM 中真实可点。
const NInputStub = defineComponent({
  name: 'NInputStub',
  props: { value: { type: String, default: '' }, placeholder: { type: String, default: '' } },
  emits: ['update:value', 'clear'],
  setup(props, { emit }) {
    return () => h('div', [
      h('input', {
        'data-testid': 'chat-conversation-search',
        placeholder: props.placeholder,
        value: props.value,
        onInput: (e: Event) => emit('update:value', (e.target as HTMLInputElement).value),
      }),
      h('button', { type: 'button', 'data-testid': 'native-clear', onClick: () => emit('clear') }, 'clear'),
    ])
  },
})
const NButtonStub = defineComponent({
  name: 'NButtonStub',
  emits: ['click'],
  setup(_, { slots, emit, attrs }) {
    return () => h('button', { type: 'button', ...attrs, onClick: (e: MouseEvent) => emit('click', e) }, [
      slots.icon?.(), slots.default?.(),
    ])
  },
})
const Passthrough = defineComponent({
  name: 'Passthrough',
  setup(_, { slots }) {
    return () => h('span', [slots.trigger?.(), slots.default?.()])
  },
})

const Host = defineComponent({
  setup() {
    return () => h(NDialogProvider, null, { default: () => h(ChatPage) })
  },
})

async function mountChat() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/chat', name: 'Chat', component: { template: '<div />' } },
      { path: '/login', name: 'Login', component: { template: '<div />' } },
    ],
  })
  await router.push('/chat')
  await router.isReady()
  const pinia = createPinia()
  setActivePinia(pinia)
  useUserStore().userInfo = { id: 1, username: 'u' } as never
  const wrapper = mount(Host, {
    global: {
      plugins: [naive, pinia, router, i18n],
      stubs: {
        // 只 stub 重组件，ChatSidebar 保持真实以覆盖页面 ↔ 侧栏接线。
        ChatMessages: true,
        ChatInput: true,
        ChatHeader: true,
        DocumentPreviewModal: true,
        DebugPanel: true,
        'n-input': NInputStub,
        'n-button': NButtonStub,
        'n-popconfirm': Passthrough,
        'n-tooltip': Passthrough,
        'n-icon': true,
        'n-skeleton': true,
        'n-result': true,
      },
    },
  })
  await flushPromises()
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.setItem('docmind_token', 'tok')
  conversations.value = []
  conversationSearch.value = '合同'
  currentPage.value = 1
})

describe('聊天页 · 会话搜索清除接线（M5 判别）', () => {
  it('点击侧栏清除按钮 → setter 收到空串并立即刷新，列表恢复无 search 参数', async () => {
    const wrapper = await mountChat()
    const clear = wrapper.find('[data-testid="chat-conversation-search-clear"]')
    expect(clear.exists()).toBe(true)
    expect(clear.text()).toBe(zh.chat.conversationSearch.clear)

    await clear.trigger('click')
    await flushPromises()

    // 页面接线必须把清除翻译为 setter('', { immediate: true })，而非 noop。
    expect(setConversationSearch).toHaveBeenCalledWith('', { immediate: true })
    // setter 的 immediate 语义由上面的 spy mock 执行：清空查询、回到第 1 页、立即刷新。
    expect(fetchConversations).toHaveBeenCalledWith('')
    expect(conversationSearch.value).toBe('')
    expect(currentPage.value).toBe(1)
    // 清除后侧栏不再显示「清除搜索」按钮，UI 与 API 查询状态一致。
    expect(wrapper.find('[data-testid="chat-conversation-search-clear"]').exists()).toBe(false)
  })

  it('侧栏输入变更 → setter 收到原始输入（页面接线未丢 searchChange）', async () => {
    const wrapper = await mountChat()
    const input = wrapper.find('[data-testid="chat-conversation-search"]')
    expect(input.exists()).toBe(true)
    await input.setValue('发票')
    expect(setConversationSearch).toHaveBeenCalledWith('发票')
  })
})
