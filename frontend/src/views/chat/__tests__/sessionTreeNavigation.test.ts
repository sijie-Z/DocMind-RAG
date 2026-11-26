/**
 * 批次八 · 会话树点击分支 = 原位切换契约（chat 页）。
 *
 * 变异点：点击分支改调 fork / 走普通 loadConversation 重拉历史 / 未写 route
 * query → 对应断言红。期望：只调 branch-context 一次、不调 fork、不重复拉
 * 历史；store 与 route 都切到目标分支，树面板关闭。
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

const { treeMock, branchContextMock, forkMock } = vi.hoisted(() => ({
  treeMock: vi.fn(),
  branchContextMock: vi.fn(),
  forkMock: vi.fn(),
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
  getDocumentChunk: vi.fn(),
}))

vi.mock('@/api/sessions', () => ({
  sessionsApi: {
    tree: treeMock,
    branchContext: branchContextMock,
    fork: forkMock,
    lineage: vi.fn(),
    summarize: vi.fn(),
    shareTree: vi.fn(),
    setBranchLabel: vi.fn(),
  },
}))

import ChatPage from '../index.vue'
import { getConversationMessages } from '@/api/chat'
import { useChatStore } from '@/stores/chat'
import { useUserStore } from '@/stores/user'
import zh from '@/locales/zh'

const DETAIL_RESPONSE = {
  success: true,
  message: '获取成功',
  data: {
    id: 'conv-1',
    title: '当前会话',
    messages: [
      {
        id: 'm1', content: '原分支消息', messageType: 'user',
        createdAt: '2026-10-10T00:00:00', sources: [], feedback: null, feedbackNote: null,
      },
    ],
    created_at: '2026-10-10T00:00:00',
    updated_at: '',
  },
}

const TREE = {
  root_session_id: 'root',
  current_session_id: 'root',
  root_present: true,
  branch_count: 2,
  tree_truncated: false,
  branches: [
    {
      session_id: 'root', title: '当前会话', parent_session_id: null, root_session_id: 'root',
      fork_point_message_id: null, fork_point_preview: null, fork_reason: null,
      branch_label: null, branch_summary: null, summary_model: null, summarized_at: null,
      created_at: '2026-10-10T00:00:00', depth: 0, is_root: true, is_current: true,
      parent_visible: true, message_count: 1, artifact_count: 0,
      artifact_latest_version: 0, artifact_version_total: 0,
    },
    {
      session_id: 'branch-child', title: '试验分支', parent_session_id: 'root',
      root_session_id: 'root', fork_point_message_id: 'm1', fork_point_preview: '原分支消息',
      fork_reason: 'user_fork', branch_label: '试验', branch_summary: '分支摘要',
      summary_model: 'fake', summarized_at: null, created_at: '2026-11-01T00:00:00',
      depth: 1, is_root: false, is_current: false, parent_visible: true,
      message_count: 1, artifact_count: 2, artifact_latest_version: 2,
      artifact_version_total: 3,
    },
  ],
}

const BRANCH_CONTEXT = {
  session_id: 'branch-child',
  title: '试验分支',
  root_session_id: 'root',
  parent_session_id: 'root',
  fork_point_message_id: 'm1',
  fork_reason: 'user_fork',
  branch_label: '试验',
  branch_summary: '分支摘要',
  summary_model: 'fake',
  summarized_at: null,
  created_at: '2026-11-01T00:00:00',
  updated_at: '2026-11-02T00:00:00',
  settings: { history_limit: 200 },
  message_count: 1,
  artifact_count: 0,
  messages: [
    {
      id: 'bm1', seq: 1, content: '分支里的消息', messageType: 'user',
      createdAt: '2026-11-01T00:00:00', sources: [], feedback: 0, feedbackNote: null,
    },
  ],
  artifacts: [],
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
  messages: { zh },
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
        // 具名桩：测试通过它触发侧栏的「打开会话树」事件（真实入口在侧栏按钮）。
        ChatSidebar: {
          name: 'ChatSidebar',
          template: '<div data-testid="chat-sidebar-stub" />',
          emits: ['openSessionTree'],
        },
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
  vi.clearAllMocks()
  localStorage.setItem('docmind_token', 'tok')
  vi.mocked(getConversationMessages).mockResolvedValue({ data: DETAIL_RESPONSE } as never)
  treeMock.mockResolvedValue({ data: { success: true, data: TREE } })
  branchContextMock.mockResolvedValue({ data: { success: true, data: BRANCH_CONTEXT } })
})

describe('chat 页 · 会话树分支原位切换', () => {
  it('点击分支只调 branch-context，不调 fork、不重复拉历史，store/route 同步切换', async () => {
    const { wrapper, router } = await mountChat()
    const sidebar = wrapper.findComponent({ name: 'ChatSidebar' })
    expect(sidebar.exists()).toBe(true)

    sidebar.vm.$emit('openSessionTree')
    await flushPromises()

    expect(treeMock).toHaveBeenCalledWith('conv-1')
    const node = wrapper.find('[data-session-id="branch-child"] [data-testid="session-tree-node"]')
    expect(node.exists()).toBe(true)

    await node.trigger('click')
    await flushPromises()

    expect(branchContextMock).toHaveBeenCalledWith('branch-child')
    expect(forkMock).not.toHaveBeenCalled()
    // 原位切换不走普通历史接口：初始会话只拉一次，分支不额外拉。
    expect(vi.mocked(getConversationMessages)).toHaveBeenCalledTimes(1)
    expect(vi.mocked(getConversationMessages)).toHaveBeenCalledWith('conv-1')

    const chatStore = useChatStore()
    expect(chatStore.currentConversation?.id).toBe('branch-child')
    expect(chatStore.currentConversation?.title).toBe('试验分支')
    expect(router.currentRoute.value.query.conversation_id).toBe('branch-child')
    expect(wrapper.find('[data-testid="session-tree-panel"]').exists()).toBe(false)
  })
})
