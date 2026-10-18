/**
 * E11-③ 聊天页内部：conversation_id query 变化驱动会话加载（不依赖整页重挂载）。
 *
 * 修前：会话切换靠布局 key=fullPath 整页重挂 + onMounted 读一次 query；布局
 * key 收敛为 route.name 后（E11 修复本体），同页 query 变化不会再重挂，
 * 必须由聊天页自身 watch(conversation_id) 接管加载，否则切会话无任何反应。
 *
 * 变异点：删 watch → 「query 变化加载新会话」用例红；watch 不比较当前会话
 * → 无关 query 变化（prompt 清除等）触发重复加载（重复请求断言红）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createI18n } from 'vue-i18n'
import naive, { NDialogProvider } from 'naive-ui'

vi.mock('@/utils/message', () => ({
  useDedupedMessage: () => ({ error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn(), loading: vi.fn() }),
}))

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

import ChatPage from '../index.vue'
import { getConversationMessages } from '@/api/chat'
import { useChatStore } from '@/stores/chat'
import { useUserStore } from '@/stores/user'

const Host = defineComponent({
  setup() {
    return () => h(NDialogProvider, null, { default: () => h(ChatPage) })
  },
})

const i18n = createI18n({
  legacy: false,
  locale: 'zh',
  missingWarn: false,
  fallbackWarn: false,
  messages: { zh: { chat: { historyFailed: '加载失败', msgLoadFailed: '加载失败' }, common: {} } },
})

const messagesFor = (id: string) => ({
  success: true,
  data: { id, title: `会话-${id}`, messages: [], created_at: '2026-10-10T00:00:00', updated_at: '' },
})

async function mountChat() {
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
  return { wrapper, router }
}

beforeEach(() => {
  localStorage.clear()
  localStorage.setItem('docmind_token', 'tok')
  vi.clearAllMocks()
  vi.mocked(getConversationMessages).mockImplementation(async (id: string) => ({ data: messagesFor(id) }) as never)
})

describe('E11-③ 聊天页 watch(conversation_id)：同页切会话即时加载', () => {
  it('query 切到 conv-2：加载新会话、组件实例保持不变（无整页重挂载）', async () => {
    const { wrapper, router } = await mountChat()
    expect(getConversationMessages).toHaveBeenCalledWith('conv-1')
    // 组件实例 uid：整页重挂载会生成新实例（uid 变化）
    const uidBefore = (wrapper.findComponent(ChatPage).vm as unknown as { $: { uid: number } }).$.uid
    expect(useChatStore().currentConversation?.id).toBe('conv-1')

    await router.push('/chat?conversation_id=conv-2')
    await flushPromises()
    await flushPromises()

    expect(getConversationMessages).toHaveBeenCalledWith('conv-2')
    expect(useChatStore().currentConversation?.id).toBe('conv-2')
    // 同页实例复用：没有整页重挂载
    const uidAfter = (wrapper.findComponent(ChatPage).vm as unknown as { $: { uid: number } }).$.uid
    expect(uidAfter).toBe(uidBefore)
  })

  it('同一 conversation_id 的无关 query 变化（如 prompt 清除）：不重复拉取', async () => {
    const { router } = await mountChat()
    expect(getConversationMessages).toHaveBeenCalledTimes(1)

    await router.replace('/chat?conversation_id=conv-1')
    await flushPromises()
    expect(getConversationMessages).toHaveBeenCalledTimes(1)
  })
})
