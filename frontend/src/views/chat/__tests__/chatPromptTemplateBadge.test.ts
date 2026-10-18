/**
 * E11-② 提示词模板常驻标识：应用后聊天头部显示「当前模板：XX ×清除」。
 *
 * 修前：提示词应用只写 localStorage（activeSystemPrompt/Name）+ 一条瞬态
 * message.info——用户切走再回来完全看不出当前挂着哪个模板，也无法清除
 * （只能发一条带该 systemPrompt 的消息，或手动清 localStorage）。
 * 修复：ChatHeader 常驻显示模板名，×清除同步移除 localStorage（后续消息
 * 不再携带该 systemPrompt）。
 *
 * 变异点：头部不渲染标识 → 「出现」断言红；清除不清 localStorage / 不收起
 * 标识 → 「清除」断言红；无 prompt 时误显示 → 「负例」断言红。
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

// ChatHeader 不 stub —— 本用例断言真实头部渲染
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
        ChatInput: true,
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
  messageSpies.error.mockClear()
  messageSpies.info.mockClear()
  messageSpies.success.mockClear()
})

describe('E11-② 提示词模板常驻标识（出现 / 清除 / 负例）', () => {
  it('应用模板后：聊天头部常驻显示「当前模板：长提示词」（修前无任何标识）', async () => {
    const { wrapper } = await mountChat({ prompt: '请以严谨风格回答', promptName: '长提示词' })

    expect(localStorage.getItem('activeSystemPrompt')).toBe('请以严谨风格回答')
    const text = wrapper.text()
    expect(text).toContain('当前模板')
    expect(text).toContain('长提示词')
  })

  it('点 ×清除：localStorage 双键移除、标识收起（后续消息不再携带 systemPrompt）', async () => {
    const { wrapper } = await mountChat({ prompt: '请以严谨风格回答', promptName: '长提示词' })
    expect(wrapper.text()).toContain('当前模板')

    const clearBtn = wrapper.find('.template-clear')
    expect(clearBtn.exists(), '清除按钮应在屏').toBe(true)
    await clearBtn.trigger('click')
    await flushPromises()

    expect(localStorage.getItem('activeSystemPrompt')).toBeNull()
    expect(localStorage.getItem('activeSystemPromptName')).toBeNull()
    expect(wrapper.text()).not.toContain('当前模板')
    expect(messageSpies.success).toHaveBeenCalled()
  })

  it('负例：无已应用模板时不显示标识', async () => {
    const { wrapper } = await mountChat()
    expect(wrapper.text()).not.toContain('当前模板')
    expect(wrapper.find('.template-clear').exists()).toBe(false)
  })

  it('无名字模板：标识回退显示默认名（未命名），不显示空标识', async () => {
    await mountChat({ prompt: '无名字的提示词' })
    const { wrapper } = await mountChat()
    // 重新挂载读取 localStorage 中的既有模板（模拟切页返回）
    expect(localStorage.getItem('activeSystemPrompt')).toBe('无名字的提示词')
    expect(wrapper.text()).toContain('当前模板')
    expect(wrapper.text()).toContain('未命名')
  })
})
