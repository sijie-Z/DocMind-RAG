/**
 * W2-T7 项 2：提示词「应用」上限对齐（第三轮终检 §2.4）。
 *
 * 现状（修前）：提示词表单 maxlength=5000，而 chat 页 onMounted 只应用
 * `length <= 2000` 的 URL prompt 内容、且无 else 分支——2001~5000 字的提示词
 * 点「应用」后**静默丢弃**（无提示、无落库、无任何反应）。
 * 修复：聊天页上限与表单/后端口径对齐 5000；超限走明确报错（不再静默）。
 *
 * 变异点：
 * - 上限改回 2000 → 「3000 字应用成功」用例精确红；
 * - 删掉超限报错分支 → 「6000 字明确报错」用例红；
 * - 上限 5000 改小（如 4000）→ 「恰 5000 字」边界用例红。
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
  forkConversation: vi.fn().mockResolvedValue({ data: { success: true, data: {} } }),
  parseUpload: vi.fn().mockResolvedValue({ content: 'x', filename: 'f', chunk_count: 1, file_size: 1 }),
}))

vi.mock('@/api/conversation', () => ({
  getConversations: vi.fn().mockResolvedValue({ data: { data: { data: [], total: 0 } } }),
  deleteConversation: vi.fn().mockResolvedValue(undefined),
  createConversation: vi.fn().mockResolvedValue({ data: { data: {} } }),
  updateConversation: vi.fn().mockResolvedValue({ data: { data: {} } }),
  forkConversation: vi.fn().mockResolvedValue({ data: { success: true, data: {} } }),
  getConversation: vi.fn(),
  getConversationMessages: vi.fn(),
  clearConversationMessages: vi.fn(),
  unbindConversationDocs: vi.fn(),
  updateMessageFeedback: vi.fn(),
}))

vi.mock('@/api/knowledge', () => ({
  uploadKnowledgeBase: vi.fn().mockResolvedValue({ data: { success: true, data: { id: 1 } } }),
  getKnowledgeBase: vi.fn().mockResolvedValue({ data: { data: { status: 'indexed' } } }),
  getDocument: vi.fn(),
  getDocumentContent: vi.fn(),
}))

import ChatPage from '../index.vue'
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
  messages: { zh: { chat: { promptApplied: '已应用提示词模板"{name}"', promptTooLong: '提示词超过 {max} 字上限，未应用' }, common: {} } },
})

async function mountChat(query: string) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/chat', name: 'Chat', component: { template: '<div />' } },
      { path: '/login', name: 'Login', component: { template: '<div />' } },
    ],
  })
  await router.push(`/chat?${query}`)
  await router.isReady()
  const pinia = createPinia()
  setActivePinia(pinia)
  const userStore = useUserStore()
  userStore.userInfo = { id: 1, username: 'u' } as never
  const wrapper = mount(Host, {
    global: {
      plugins: [naive, pinia, router, i18n],
      // 与 chatLoadConversation.test.ts 同款：仅 stub 页面子组件，保留主页 onMounted 真链路
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
  return wrapper
}

beforeEach(() => {
  localStorage.clear()
  messageSpies.error.mockClear()
  messageSpies.info.mockClear()
})

describe('W2-T7 · 提示词应用上限对齐 5000（聊天页）', () => {
  it('3000 字提示词（旧 2000 上限区间）：应用成功写入 activeSystemPrompt，不再静默丢弃', async () => {
    const content = '提'.repeat(3000)
    await mountChat(`prompt=${encodeURIComponent(content)}&promptName=长提示词`)
    expect(localStorage.getItem('activeSystemPrompt')).toBe(content)
    expect(localStorage.getItem('activeSystemPromptName')).toBe('长提示词')
    expect(messageSpies.info).toHaveBeenCalled()
    expect(messageSpies.error).not.toHaveBeenCalled()
  })

  it('恰 5000 字（表单 maxlength 上限）：边界内应用成功', async () => {
    const content = '边'.repeat(5000)
    await mountChat(`prompt=${encodeURIComponent(content)}`)
    expect(localStorage.getItem('activeSystemPrompt')).toBe(content)
  })

  it('6000 字超过上限：明确报错、不写入 localStorage（修前是静默无提示）', async () => {
    const content = '超'.repeat(6000)
    await mountChat(`prompt=${encodeURIComponent(content)}`)
    expect(localStorage.getItem('activeSystemPrompt')).toBeNull()
    expect(messageSpies.error).toHaveBeenCalled()
  })
})
