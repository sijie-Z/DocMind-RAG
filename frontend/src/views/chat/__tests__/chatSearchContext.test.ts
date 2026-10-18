/**
 * E11-① 跨页上下文（聊天侧）：读取 ?q=&context= 并预填输入框。
 *
 * 修前：聊天页 onMounted 只读 conversation_id / prompt——搜索页 push 的
 * q/context 被完全忽略，用户落在空白聊天页（跨页意图丢失）。
 * 修复：q 预填输入框；context（所点结果正文片段）以 Markdown 引用块附在问题
 * 下方，用户可编辑后再发送（不自动发送，避免未经确认的模型调用）；参数用后
 * 即从 URL 清除，刷新不重复预填。
 *
 * 变异点：删 q 读取 → 「预填」断言红；删 context 拼接 → 「片段保留」断言红；
 * 删 URL 清除 → 「参数用后即清」断言红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createI18n } from 'vue-i18n'
import naive, { NDialogProvider } from 'naive-ui'

const { messageSpies } = vi.hoisted(() => ({
  messageSpies: { error: vi.fn(), info: vi.fn(), success: vi.fn(), warning: vi.fn(), loading: vi.fn() },
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

import ChatPage from '../index.vue'
import { useUserStore } from '@/stores/user'
import zh from '@/locales/zh'

const Host = defineComponent({
  setup() {
    return () => h(NDialogProvider, null, { default: () => h(ChatPage) })
  },
})

const i18n = createI18n({ legacy: false, locale: 'zh', missingWarn: false, fallbackWarn: false, messages: { zh } })

async function mountChat(query: Record<string, string>) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/chat', name: 'Chat', component: { template: '<div />' } },
      { path: '/login', name: 'Login', component: { template: '<div />' } },
    ],
  })
  await router.push({ path: '/chat', query })
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
  const vm = wrapper.findComponent(ChatPage).vm as unknown as { inputMessage: string }
  return { wrapper, router, vm }
}

beforeEach(() => {
  localStorage.clear()
  localStorage.setItem('docmind_token', 'tok')
  vi.clearAllMocks()
})

describe('E11-① 聊天页消费 ?q=&context=（问问 AI 跨页预填）', () => {
  const Q = '关于文件「architecture.md」：架构设计'
  const CONTEXT = '# DocMind 系统架构设计本文档描述了整体架构设计'

  it('带 q：输入框预填该问题（修前被完全忽略、输入框为空）', async () => {
    const { vm } = await mountChat({ q: Q })
    expect(vm.inputMessage).toBe(Q)
  })

  it('带 q+context：输入框同时保留所点结果的正文片段（引用块形式）', async () => {
    const { vm } = await mountChat({ q: Q, context: CONTEXT })
    expect(vm.inputMessage).toContain(Q)
    expect(vm.inputMessage).toContain(CONTEXT)
    expect(vm.inputMessage).toContain('> ')
  })

  it('参数用后即从 URL 清除（刷新不重复预填）', async () => {
    const { router } = await mountChat({ q: Q, context: CONTEXT })
    expect(router.currentRoute.value.query.q).toBeUndefined()
    expect(router.currentRoute.value.query.context).toBeUndefined()
  })

  it('负例：无 q 时不预填（冷启动空输入）', async () => {
    const { vm } = await mountChat({})
    expect(vm.inputMessage).toBe('')
  })
})
