/**
 * E2 · 聊天页「清空对话」二次确认判别用例。
 *
 * 验收口径（E2 五处破坏性操作之四）：
 *  - 未确认（取消）→ clearConversationMessages 不得被调用，消息不变；
 *  - 确认后 success:true → 清空消息并提示成功；
 *  - 确认后 success:false → 如实反馈服务端 message（此前静默无提示）。
 *
 * 变异点：去掉确认 → 负相用例红；删掉 success:false 的 else 反馈 → 对应用例红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createI18n } from 'vue-i18n'
import naive, { NDialogProvider } from 'naive-ui'

import ChatPage from '../index.vue'
import { getConversationMessages } from '@/api/chat'
import { useUserStore } from '@/stores/user'
import type { ChatMessage } from '@/types/chat'
import zh from '@/locales/zh'

interface DialogOptions {
  title?: string
  content?: string
  onPositiveClick?: () => unknown
  onNegativeClick?: () => unknown
  onClose?: () => unknown
}

const { dialogState, dialogWarning } = vi.hoisted(() => {
  const state = {
    decision: 'cancel' as 'cancel' | 'confirm',
    calls: [] as DialogOptions[],
  }
  const warning = vi.fn((opts: DialogOptions) => {
    state.calls.push(opts)
    if (state.decision === 'confirm') opts.onPositiveClick?.()
    else opts.onNegativeClick?.()
  })
  return { dialogState: state, dialogWarning: warning }
})

const { messageSpies, apiMocks } = vi.hoisted(() => ({
  messageSpies: { error: vi.fn(), info: vi.fn(), success: vi.fn(), warning: vi.fn(), loading: vi.fn() },
  apiMocks: { clear: vi.fn() },
}))

vi.mock('naive-ui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('naive-ui')>()
  return {
    ...actual,
    useDialog: () => ({ warning: dialogWarning, info: vi.fn() }),
  }
})

vi.mock('@/utils/message', () => ({ useDedupedMessage: () => messageSpies }))

vi.mock('@/api/chat', () => ({
  getConversationMessages: vi.fn(),
  clearConversationMessages: apiMocks.clear,
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

// 真响应形状（chat.py:353-390）：两条消息，用于验证取消后消息不变
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
        createdAt: '2026-10-10T00:01:00', sources: [], feedback: null, feedbackNote: null,
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

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

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
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.setItem('docmind_token', 'tok')
  dialogState.decision = 'cancel'
  dialogState.calls.length = 0
  dialogWarning.mockClear()
  apiMocks.clear.mockReset().mockResolvedValue({ data: { success: true } })
  vi.mocked(getConversationMessages).mockResolvedValue({ data: DETAIL_RESPONSE } as never)
})

describe('聊天页 · 清空对话二次确认（E2）', () => {
  it('负相（取消）：不调清空接口，消息不变', async () => {
    const wrapper = await mountChat()
    const vm = wrapper.findComponent(ChatPage).vm as unknown as {
      currentConversationId: string
      clearChat: () => Promise<void>
      messages: ChatMessage[]
    }
    expect(vm.currentConversationId).toBe('conv-1')

    dialogState.decision = 'cancel'
    await vm.clearChat()
    await flushPromises()

    expect(dialogWarning).toHaveBeenCalledTimes(1)
    expect(apiMocks.clear).not.toHaveBeenCalled()
    expect(vm.messages).toHaveLength(2)
  })

  it('正相（确认 + success:true）：调接口、清空消息并提示成功', async () => {
    const wrapper = await mountChat()
    const vm = wrapper.findComponent(ChatPage).vm as unknown as {
      clearChat: () => Promise<void>
      messages: ChatMessage[]
    }

    dialogState.decision = 'confirm'
    await vm.clearChat()
    await flushPromises()

    expect(dialogWarning).toHaveBeenCalledTimes(1)
    expect(apiMocks.clear).toHaveBeenCalledWith('conv-1')
    expect(vm.messages).toHaveLength(0)
    expect(messageSpies.success).toHaveBeenCalled()
  })

  it('确认后 success:false：如实反馈服务端 message，消息不清空', async () => {
    const wrapper = await mountChat()
    const vm = wrapper.findComponent(ChatPage).vm as unknown as {
      clearChat: () => Promise<void>
      messages: ChatMessage[]
    }

    dialogState.decision = 'confirm'
    apiMocks.clear.mockResolvedValue({ data: { success: false, message: '会话无权限' } })
    await vm.clearChat()
    await flushPromises()

    expect(apiMocks.clear).toHaveBeenCalledWith('conv-1')
    expect(messageSpies.error).toHaveBeenCalledWith('会话无权限')
    expect(vm.messages).toHaveLength(2)
  })
})
