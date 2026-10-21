/**
 * B34/B36 + C7 契约回归：chat 页 loadConversation 历史映射与无权提示。
 *
 * 真值（backend/app/api/v1/endpoints/chat.py:347-390 会话详情响应）：
 *   - 消息键为 camelCase：messageType / createdAt / feedback / feedbackNote；
 *   - 无权/不存在会话：HTTP 200 + { success:false, message:"无权访问" }（:340）。
 * 旧实现：读 m.created_at（时间丢，B36）、映射丢 feedback（重载后点赞高亮必丢，
 * B34）、success:false 静默落入空聊天态（C7）。
 *
 * 变异点：映射回退旧读点 / 去掉 success:false 分支 → 对应用例红。
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
}))

import ChatPage from '../index.vue'
import { getConversationMessages } from '@/api/chat'
import { useChatStore } from '@/stores/chat'
import { useUserStore } from '@/stores/user'
import type { ChatMessage } from '@/types/chat'

// 真响应形状（chat.py:353-390）：assistant 带 sources/feedback；updated_at 可为 ''
const DETAIL_RESPONSE = {
  success: true,
  message: '获取成功',
  data: {
    id: 'conv-1',
    title: '测试会话',
    messages: [
      {
        id: 'm1', content: '你好', messageType: 'user',
        createdAt: '2026-10-10T00:00:00', sources: [], feedback: null, feedbackNote: null,
      },
      {
        id: 'm2', content: '回答', messageType: 'assistant',
        createdAt: '2026-10-10T00:01:00', sources: [],
        feedback: 1, feedbackNote: null,
      },
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

const i18n = createI18n({
  legacy: false,
  locale: 'zh',
  missingWarn: false,
  fallbackWarn: false,
  messages: { zh: { chat: { historyFailed: '加载历史消息失败' }, common: {} } },
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
  // 登录态就绪（跳过 getUserInfo 网络路径；token 来自 localStorage）
  const userStore = useUserStore()
  userStore.userInfo = { id: 1, username: 'u' } as never
  const wrapper = mount(Host, {
    global: {
      plugins: [naive, pinia, router, i18n],
      // 仅 stub 页面子组件（保留 ChatPage 本体真实 setup/onMounted 链路）；
      // 不能用 shallow:true —— 那会把 Host 的 NDialogProvider 一并 stub、
      // 槽内容不再渲染
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

describe('chat 页 · loadConversation 映射与无权提示（B34/B36/C7）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.setItem('docmind_token', 'tok')
    vi.mocked(getConversationMessages).mockResolvedValue({ data: DETAIL_RESPONSE } as never)
  })

  it('历史消息映射读真实键：createdAt 保留、feedback 保留（旧映射二者皆丢）', async () => {
    const wrapper = await mountChat()

    expect(getConversationMessages).toHaveBeenCalledWith('conv-1')
    const chatStore = useChatStore()
    expect(chatStore.currentConversation?.id).toBe('conv-1')

    const vm = wrapper.findComponent(ChatPage).vm as unknown as { messages: ChatMessage[] }
    const msgs = vm.messages
    expect(msgs).toHaveLength(2)
    expect(msgs[0].messageType).toBe('user')
    // B36：真键 createdAt（旧代码读 m.created_at → undefined）
    expect(msgs[1].createdAt).toBe('2026-10-10T00:01:00')
    // B34：feedback 进入消息对象（旧映射丢失 → 重载后点赞高亮必丢）
    expect(msgs[1].feedback).toBe(1)
  })

  it('无权会话（200 + success:false）如实提示并中止，不静默进空态（C7）', async () => {
    vi.mocked(getConversationMessages).mockResolvedValue({
      data: { success: false, message: '无权访问' },
    } as never)

    const wrapper = await mountChat()

    expect(messageSpies.error).toHaveBeenCalledWith('无权访问')
    expect(useChatStore().currentConversation).toBeNull()
    const vm = wrapper.findComponent(ChatPage).vm as unknown as { messages: ChatMessage[] }
    expect(vm.messages).toHaveLength(0)
  })
})
