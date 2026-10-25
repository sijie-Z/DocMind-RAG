/**
 * 引用编号契约：正文 [N]、来源卡与 Markdown 导出共用后端原始 rank。
 * 过滤后仅保留 rank=2/3 时不得按数组下标顺移；旧历史缺 rank 才回退位置。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createI18n } from 'vue-i18n'
import naive, { NDialogProvider } from 'naive-ui'
import ChatMessages from '../components/ChatMessages.vue'
import ChatPage from '../index.vue'
import { getConversationMessages } from '@/api/chat'
import { useUserStore } from '@/stores/user'
import type { ChatMessage, KnowledgeSource } from '@/types/chat'
import zh from '@/locales/zh'

vi.mock('vue-virtual-scroller', async () => {
  const stubs = await import('@/test-utils/virtualScroller')
  return {
    DynamicScroller: stubs.DynamicScrollerStub,
    DynamicScrollerItem: stubs.DynamicScrollerItemStub,
  }
})

const { messageSpies } = vi.hoisted(() => ({
  messageSpies: {
    error: vi.fn(), info: vi.fn(), success: vi.fn(), warning: vi.fn(), loading: vi.fn(),
  },
}))

vi.mock('@/utils/message', () => ({ useDedupedMessage: () => messageSpies }))

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
  getDocumentDetail: vi.fn(),
  getDocumentContent: vi.fn(),
}))

const MarkdownStub = defineComponent({
  name: 'Markdown',
  props: { content: { type: String, default: '' } },
  setup(props) {
    return () => h('div', props.content)
  },
})

const NPopoverPassthrough = defineComponent({
  name: 'Popover',
  setup(_, { slots }) {
    return () => h('div', [slots.trigger?.(), slots.default?.()])
  },
})

const NTagPassthrough = defineComponent({
  name: 'NTag',
  setup(_, { slots }) {
    return () => h('span', { class: 'source-tag' }, [slots.icon?.(), slots.default?.()])
  },
})

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

function mountChatMessages(sources: KnowledgeSource[], content: string) {
  const message = {
    id: 1,
    content,
    messageType: 'assistant',
    conversationId: 1,
    sources,
  } as unknown as ChatMessage
  return mount(ChatMessages, {
    props: {
      messages: [message], isLoading: false, showBackToBottom: false, userAvatar: '', suggestions: [],
    },
    global: {
      plugins: [i18n],
      stubs: {
        Markdown: MarkdownStub,
        Popover: NPopoverPassthrough,
        'n-tag': NTagPassthrough,
        'n-icon': true,
        'n-avatar': true,
        'n-button': true,
        'n-tooltip': true,
      },
    },
  })
}

const Host = defineComponent({
  setup() {
    return () => h(NDialogProvider, null, { default: () => h(ChatPage) })
  },
})

async function mountChatPage(sources: KnowledgeSource[], content: string) {
  vi.mocked(getConversationMessages).mockResolvedValue({
    data: {
      success: true,
      data: {
        id: 'conv-citations',
        title: '引用测试',
        messages: [{
          id: 'm1', content, messageType: 'assistant', createdAt: '2026-10-26T00:00:00',
          sources, feedback: null, feedbackNote: null,
        }],
        created_at: '2026-10-26T00:00:00',
        updated_at: '',
      },
    },
  } as never)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/chat', name: 'Chat', component: { template: '<div />' } },
      { path: '/login', name: 'Login', component: { template: '<div />' } },
    ],
  })
  await router.push('/chat?conversation_id=conv-citations')
  await router.isReady()
  const pinia = createPinia()
  setActivePinia(pinia)
  const userStore = useUserStore()
  userStore.userInfo = { id: 1, username: 'u' } as never
  const wrapper = mount(Host, {
    global: {
      plugins: [naive, pinia, router, i18n],
      stubs: {
        ChatSidebar: true,
        ChatMessages: true,
        ChatInput: true,
        ChatHeader: true,
        DocumentPreviewModal: true,
        DebugPanel: true,
      },
    },
  })
  await flushPromises()
  await flushPromises()
  return wrapper.findComponent(ChatPage)
}

let exportedBlob: Blob | undefined
const createObjectURLMock = vi.fn((blob: Blob) => {
  exportedBlob = blob
  return 'blob:citation-rank-test'
})
const revokeObjectURLMock = vi.fn()
Object.defineProperty(URL, 'createObjectURL', {
  configurable: true, writable: true, value: createObjectURLMock,
})
Object.defineProperty(URL, 'revokeObjectURL', {
  configurable: true, writable: true, value: revokeObjectURLMock,
})
const anchorClickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

beforeEach(() => {
  vi.clearAllMocks()
  exportedBlob = undefined
  localStorage.setItem('docmind_token', 'tok')
})

afterEach(() => {
  anchorClickSpy.mockClear()
})

async function exportMarkdown(sources: KnowledgeSource[], content: string): Promise<string> {
  const page = await mountChatPage(sources, content)
  const vm = page.vm as unknown as { handleExportChat: () => void; messages: ChatMessage[] }
  expect(vm.messages).toHaveLength(1)
  vm.handleExportChat()
  expect(createObjectURLMock).toHaveBeenCalledTimes(1)
  if (!exportedBlob) throw new Error('导出未生成 Blob')
  return exportedBlob.text()
}

describe('ChatMessages · 原始引用 rank', () => {
  it('正文 [2] 且仅有 rank=2 的 B 时，B 卡片显示 [2] 而非 [1]', () => {
    const wrapper = mountChatMessages([{ filename: 'B', rank: 2, relevanceScore: null }], '正文 [2]')

    expect(wrapper.text()).toContain('[2] B')
    expect(wrapper.text()).not.toContain('[1] B')
  })

  it('过滤后 rank=2、3 两条来源保持 [2]、[3]，不顺移', () => {
    const wrapper = mountChatMessages([
      { filename: 'B', rank: 2, relevanceScore: null },
      { filename: 'C', rank: 3, relevanceScore: null },
    ], '正文 [2] [3]')

    expect(wrapper.text()).toContain('[2] B')
    expect(wrapper.text()).toContain('[3] C')
    expect(wrapper.text()).not.toContain('[1] B')
    expect(wrapper.text()).not.toContain('[2] C')
  })

  it('旧历史来源缺 rank 时仍按数组位置回退为 [1]、[2]', () => {
    const wrapper = mountChatMessages([{ filename: 'A' }, { filename: 'B' }], '旧回答 [1] [2]')

    expect(wrapper.text()).toContain('[1] A')
    expect(wrapper.text()).toContain('[2] B')
  })
})

describe('聊天页 Markdown 导出 · 原始引用 rank', () => {
  it('正文与来源段共同保留 rank=2、3，不重编号', async () => {
    const content = '正文 [2] 与 [3]'
    const markdown = await exportMarkdown([
      { filename: 'B', rank: 2, relevanceScore: null },
      { filename: 'C', rank: 3, relevanceScore: null },
    ], content)

    expect(markdown).toContain(content)
    expect(markdown).toContain('- [2] B (')
    expect(markdown).toContain('- [3] C (')
    expect(markdown).not.toContain('- [1] B (')
    expect(markdown).not.toContain('- [2] C (')
  })

  it('旧历史导出缺 rank 时仍按数组位置回退为 [1]、[2]', async () => {
    const markdown = await exportMarkdown([{ filename: 'A' }, { filename: 'B' }], '旧回答 [1] [2]')

    expect(markdown).toContain('- [1] A (')
    expect(markdown).toContain('- [2] B (')
  })
})
