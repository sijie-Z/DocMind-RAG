/**
 * W2-T7 项 5：答案来源卡 → 原文预览接通（第三轮终检 §2.4）。
 *
 * 现状（修前）：来源 tag 带 cursor-pointer/hover 样式但**零点击处理**；chat 页
 * DocumentPreviewModal 的四个 ref（showPreviewModal/previewLoading/previewDoc/
 * previewContent）只有声明与模板绑定、全树无写入点 → 弹窗永不打开（死接线）。
 * 修复：tag click 冒泡 openSource → 页内拉取文档详情 + 全文（/documents/{id}、
 * /documents/{id}/content）→ 打开弹窗；失败明确报错。
 *
 * 变异点：
 * - 删 ChatMessages 中 tag 的 @click 冒泡 → 「点击来源卡冒泡」用例红；
 * - 删 chat 页 handleOpenSource（或删 refs 写入）→ 「打开弹窗并拉全文」用例红。
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
  getDocumentDetail: vi.fn(),
  getDocumentContent: vi.fn(),
}))

import ChatPage from '../index.vue'
import ChatMessages from '../components/ChatMessages.vue'
import { getDocumentDetail, getDocumentContent } from '@/api/knowledge'
import { useUserStore } from '@/stores/user'
import type { ChatMessage, KnowledgeSource } from '@/types/chat'

const mockedGetDocument = vi.mocked(getDocumentDetail)
const mockedGetDocumentContent = vi.mocked(getDocumentContent)

// 真实来源形状（chat_service.build_sources_list 逐键对应）：documentId 是文档 id，
// 无 fileId 键——实现按 fileId 取值会在此精确红（实机曾踩：vitest 自造 fileId 掩盖）。
const SOURCE: KnowledgeSource = {
  filename: '架构文档.pdf',
  relevanceScore: 0.82,
  content: '命中的分块内容',
  documentId: 'doc-7',
  hasKeyword: true,
  hasVector: true,
  rewriteHits: 1,
  freshFactor: 1,
}

// ── Part A：ChatMessages 点击来源卡应冒泡 openSource ─────────────────────
const NTagStub = defineComponent({
  name: 'NTag',
  emits: ['click'],
  setup(_, { slots, emit }) {
    return () => h('span', { class: 'n-tag-stub', onClick: () => emit('click') }, slots.default?.())
  },
})
const NPopoverStub = defineComponent({
  name: 'NPopover',
  setup(_, { slots }) {
    return () => h('div', [slots.trigger?.(), slots.default?.()])
  },
})
const DynamicScroller = defineComponent({
  props: { items: { type: Array, default: () => [] } },
  setup(props, { slots }) {
    return () => h('div', props.items.map((item, index) => slots.default?.({ item, index, active: true })))
  },
})
const DynamicScrollerItem = defineComponent({
  setup(_, { slots }) {
    return () => h('div', slots.default?.())
  },
})

const messagesI18n = createI18n({
  legacy: false,
  locale: 'zh',
  messages: {
    zh: {
      chat: {
        sourcesLabel: '来源', sourceDoc: '文档', unknownDoc: '未知文档', noPreview: '无预览',
        relevanceScore: '相关度 {score}', useful: '有用', notUseful: '没用', regenerate: '重新生成',
        backToBottom: '回到底部', welcome: '欢迎', welcomeDesc: '描述', copy: '复制', fork: '分叉',
      },
      onboarding: { welcome: { stepUpload: '传', stepAsk: '问', stepAgent: 'Agent' } },
    },
  },
})

describe('W2-T7 · ChatMessages 来源卡冒泡 openSource', () => {
  it('点击来源 tag：$emit(openSource, source) 携带原始引用对象（修前零点击处理）', async () => {
    const msg: ChatMessage = {
      id: 1, content: '回答', messageType: 'assistant', conversationId: 1,
      sources: [SOURCE],
    }
    const wrapper = mount(ChatMessages, {
      props: { messages: [msg], isLoading: false, showBackToBottom: false, userAvatar: '', suggestions: [] },
      global: {
        plugins: [messagesI18n],
        components: { DynamicScroller, DynamicScrollerItem },
        stubs: {
          Markdown: true, 'n-icon': true, 'n-avatar': true, 'n-button': true,
          'n-tooltip': true, 'n-tag': NTagStub, 'n-popover': NPopoverStub,
        },
      },
    })

    const tag = wrapper.find('.n-tag-stub')
    expect(tag.exists()).toBe(true)
    await tag.trigger('click')

    const emitted = wrapper.emitted('openSource')
    expect(emitted, '点击来源卡必须冒泡 openSource（修前无任何处理）').toBeTruthy()
    expect(emitted![0][0]).toMatchObject({ documentId: 'doc-7', filename: '架构文档.pdf' })
  })
})

// ── Part B：chat 页收到 openSource → 打开预览弹窗并拉取全文 ──────────────
const MsgStub = defineComponent({
  name: 'ChatMessages',
  emits: ['openSource', 'scroll', 'scrollToBottom', 'useSuggestion', 'onboardingStep', 'feedback', 'copy', 'regenerate', 'fork'],
  setup() {
    return () => h('div', { class: 'msg-stub' })
  },
})
const PreviewStub = defineComponent({
  name: 'DocumentPreviewModal',
  props: { show: Boolean, loading: Boolean, doc: Object, content: String },
  emits: ['update:show', 'download'],
  setup() {
    return () => h('div', { class: 'preview-stub' })
  },
})

const pageI18n = createI18n({
  legacy: false,
  locale: 'zh',
  missingWarn: false,
  fallbackWarn: false,
  messages: { zh: { chat: { sourcePreviewFailed: '原文加载失败' }, common: {} } },
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
  const userStore = useUserStore()
  userStore.userInfo = { id: 1, username: 'u' } as never
  const Host = defineComponent({
    setup() {
      return () => h(NDialogProvider, null, { default: () => h(ChatPage) })
    },
  })
  const wrapper = mount(Host, {
    global: {
      plugins: [naive, pinia, router, pageI18n],
      stubs: {
        ChatSidebar: true,
        ChatMessages: MsgStub,
        ChatInput: true,
        ChatHeader: true,
        DocumentPreviewModal: PreviewStub,
        DebugPanel: true,
      },
    },
  })
  await flushPromises()
  await flushPromises()
  return wrapper
}

describe('W2-T7 · chat 页来源卡 → 原文预览接通', () => {
  beforeEach(() => {
    localStorage.setItem('docmind_token', 'tok') // onMounted token 门（先例 chatLoadConversation）
    messageSpies.error.mockClear()
    mockedGetDocument.mockReset()
    mockedGetDocumentContent.mockReset()
  })

  it('openSource → 拉取文档详情+全文，打开弹窗并填充内容（修前四 ref 无写入点）', async () => {
    mockedGetDocument.mockResolvedValue({
      data: {
        success: true,
        data: {
          id: 'doc-7', title: '架构文档', filename: '架构文档.pdf', file_size: 2048,
          file_type: 'pdf', status: 'indexed', created_at: '2026-10-01T00:00:00',
          description: '描述', keywords: ['架构'], upload_source: '知识库上传',
        },
      },
    } as never)
    mockedGetDocumentContent.mockResolvedValue({
      data: {
        success: true,
        data: { id: 'doc-7', filename: '架构文档.pdf', content: '全文内容甲', summary: '摘要', suggested_tags: ['tag'] },
      },
    } as never)

    const wrapper = await mountChat()
    wrapper.findComponent(MsgStub).vm.$emit('openSource', SOURCE)
    await flushPromises()
    await flushPromises()

    expect(mockedGetDocument).toHaveBeenCalledWith('doc-7')
    expect(mockedGetDocumentContent).toHaveBeenCalledWith('doc-7')

    const modal = wrapper.findComponent(PreviewStub)
    expect(modal.props('show')).toBe(true)
    expect(modal.props('loading')).toBe(false)
    expect(modal.props('content')).toBe('全文内容甲')
    expect(modal.props('doc')).toMatchObject({ id: 'doc-7', filename: '架构文档.pdf', source: '知识库上传', summary: '摘要' })
    expect(messageSpies.error).not.toHaveBeenCalled()
  })

  it('全文接口失败：明确报错、弹窗不 silent（loading 收敛）', async () => {
    mockedGetDocument.mockResolvedValue({
      data: { success: true, data: { id: 'doc-7', filename: '架构文档.pdf' } },
    } as never)
    mockedGetDocumentContent.mockRejectedValue(new Error('boom'))

    const wrapper = await mountChat()
    wrapper.findComponent(MsgStub).vm.$emit('openSource', SOURCE)
    await flushPromises()
    await flushPromises()

    expect(messageSpies.error).toHaveBeenCalled()
    const modal = wrapper.findComponent(PreviewStub)
    expect(modal.props('show')).toBe(true)
    expect(modal.props('loading')).toBe(false)
  })

  it('无 documentId 的引用：不请求、不开弹窗（防御空引用）', async () => {
    const wrapper = await mountChat()
    wrapper.findComponent(MsgStub).vm.$emit('openSource', { filename: 'x', relevanceScore: 0 })
    await flushPromises()

    expect(mockedGetDocument).not.toHaveBeenCalled()
    expect(wrapper.findComponent(PreviewStub).props('show')).toBe(false)
  })
})
