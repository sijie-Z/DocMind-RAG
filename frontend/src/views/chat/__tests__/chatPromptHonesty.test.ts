/**
 * E14/P2 判别用例：提示词「应用到对话」不得声称模板已生效。
 *
 * 背景：B3 已删除 activeSystemPrompt 发送通道（useChatSend 不再读取），旧
 * 实现仍写 localStorage 双键 + 头部常驻「当前模板：X」徽标 + toast「已应用
 * 提示词模板」——三项宣称均与实际不符（模板不会随消息发送）。
 *
 * 现口径（最诚实原则，产品裁定暂缺）：保留入口，把提示词**填入输入框**
 * （可见、可编辑、由用户确认后自行发送）；文案只说明"已填入输入框"，不承诺
 * 已应用/已发送；移除「当前模板」徽标与死 localStorage 通道。
 *
 * 变异点：
 * - 恢复徽标/「已应用」文案 → 「不宣称已应用」红；
 * - 恢复写 activeSystemPrompt → 「死键不再写入」红；
 * - 不填输入框 → 「填入输入框」红；
 * - 超限不报错 / 仍填框 → 「上限」用例红。
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

// ChatInput 轻量桩：渲染 v-model:inputMessage，便于断言预填内容
const ChatInputStub = defineComponent({
  name: 'ChatInput',
  props: { inputMessage: { type: String, default: '' } },
  template: '<div class="chat-input-stub">{{ inputMessage }}</div>',
})

const Host = defineComponent({
  setup() {
    return () => h(NDialogProvider, null, { default: () => h(ChatPage) })
  },
})

const i18n = createI18n({ legacy: false, locale: 'zh', missingWarn: false, fallbackWarn: false, messages: { zh } })

// ChatHeader 不 stub —— 本用例断言真实头部不再出现「当前模板」宣称
async function mountChat(query: Record<string, string> = {}) {
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
        ChatInput: ChatInputStub,
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
  Object.values(messageSpies).forEach((spy) => spy.mockClear())
})

describe('E14/P2 · 提示词应用语义如实（填入输入框，不宣称已应用）', () => {
  it('应用入口：提示词填入输入框；不写 activeSystemPrompt 死键；不出现「当前模板/已应用」宣称', async () => {
    const { wrapper } = await mountChat({ prompt: '请以严谨风格回答', promptName: '长提示词' })

    // B3 后的死通道：不得再写入
    expect(localStorage.getItem('activeSystemPrompt')).toBeNull()
    expect(localStorage.getItem('activeSystemPromptName')).toBeNull()
    // 提示词可见地落在输入框（由用户确认后发送）
    expect(wrapper.find('.chat-input-stub').text()).toBe('请以严谨风格回答')
    // 头部不得再出现「当前模板」徽标
    const text = wrapper.text()
    expect(text).not.toContain('当前模板')
    expect(text).not.toContain('已应用')
    // toast 只说明填入输入框，不承诺应用/发送
    expect(messageSpies.info).toHaveBeenCalledTimes(1)
    const shown = String(messageSpies.info.mock.calls[0][0])
    expect(shown).toContain('填入输入框')
    expect(shown).not.toContain('已应用')
  })

  it('负例：未带 prompt 参数时不填框、不提示、无模板宣称', async () => {
    const { wrapper } = await mountChat()

    expect(wrapper.find('.chat-input-stub').text()).toBe('')
    expect(messageSpies.info).not.toHaveBeenCalled()
    expect(wrapper.text()).not.toContain('当前模板')
    expect(localStorage.getItem('activeSystemPrompt')).toBeNull()
  })

  it('超过 5000 字上限：明确报错且不填框（不静默、不落死键）', async () => {
    const { wrapper } = await mountChat({ prompt: '超'.repeat(6000) })

    expect(messageSpies.error).toHaveBeenCalled()
    expect(wrapper.find('.chat-input-stub').text()).toBe('')
    expect(localStorage.getItem('activeSystemPrompt')).toBeNull()
  })
})
