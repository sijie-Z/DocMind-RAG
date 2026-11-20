/**
 * 会话侧栏「按标题搜索」判别性契约。
 *
 * 覆盖四个点：
 *  1) 防抖后的查询作为 `search` 查询参数发给列表 API（真实后端契约 chat.py get_conversations）；
 *  2) 空结果渲染专用空态，不误报「无历史会话」；
 *  3) 清除后立即恢复全量（请求不含 search 参数）；
 *  4) 快速输入只有最终查询落地（防抖 + listRequestSeq 竞态守卫）。
 *
 * 变异点：把 setConversationSearch 改成即时请求 → 「快速输入只发最终查询」红；
 * 把清除分支去掉 → 「清除后无 search」红；把侧栏空态写死 chat.noHistory → 「空态」红。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createI18n } from 'vue-i18n'
import { NDialogProvider } from 'naive-ui'

vi.mock('@/utils/message', () => ({
  useDedupedMessage: () => ({
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
    loading: vi.fn(),
  }),
}))

vi.mock('@/api/conversation', () => ({
  getConversations: vi.fn(),
  deleteConversation: vi.fn().mockResolvedValue(undefined),
}))

vi.mock('@/api/health', () => ({
  getHealth: vi.fn().mockResolvedValue({ version: '9.9.9' }),
}))

import { getConversations } from '@/api/conversation'
import { useChatSessions } from '../composables/useChatSessions'
import ChatSidebar from '../components/ChatSidebar.vue'
import zh from '@/locales/zh'

const pageResponse = (titles: string[]) => ({
  data: {
    success: true,
    message: '获取成功',
    data: {
      data: titles.map((title, index) => ({
        id: `c${index}`,
        title,
        created_at: '2026-10-09T13:49:00',
        updated_at: '',
        message_count: 1,
        last_message: 'hi',
      })),
      total: titles.length,
      page: 1,
      page_size: 50,
    },
  },
})

let sessions: ReturnType<typeof useChatSessions>
const Host = defineComponent({
  setup() {
    sessions = useChatSessions()
    return () => h('div')
  },
})

async function mountComposable() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: { template: '<div />' } }],
  })
  await router.push('/')
  await router.isReady()
  const i18n = createI18n({
    legacy: false,
    locale: 'zh',
    missingWarn: false,
    fallbackWarn: false,
    messages: { zh: {} },
  })
  const Root = defineComponent({
    render: () => h(NDialogProvider, null, { default: () => h(Host) }),
  })
  mount(Root, { global: { plugins: [pinia, router, i18n] } })
  await flushPromises()
}

describe('useChatSessions · 会话标题搜索请求契约', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('防抖后的关键词作为 search 参数发给列表 API', async () => {
    vi.mocked(getConversations).mockResolvedValue(pageResponse(['合同审查']) as never)
    await mountComposable()

    sessions.setConversationSearch('合同')
    await vi.advanceTimersByTimeAsync(279)
    expect(getConversations).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(1)
    await flushPromises()
    expect(getConversations).toHaveBeenCalledTimes(1)
    expect(getConversations).toHaveBeenLastCalledWith({ page: 1, page_size: 50, search: '合同' })
    expect(sessions.conversations.value.map((c) => c.title)).toEqual(['合同审查'])
  })

  it('清除搜索后立即恢复全量，请求不带 search 参数', async () => {
    vi.mocked(getConversations).mockResolvedValue(pageResponse(['合同审查']) as never)
    await mountComposable()

    sessions.setConversationSearch('合同')
    await vi.advanceTimersByTimeAsync(280)
    await flushPromises()
    expect(getConversations).toHaveBeenLastCalledWith({ page: 1, page_size: 50, search: '合同' })

    sessions.setConversationSearch('', { immediate: true })
    await flushPromises()
    expect(getConversations).toHaveBeenCalledTimes(2)
    expect(getConversations).toHaveBeenLastCalledWith({ page: 1, page_size: 50 })
    expect(sessions.conversationSearch.value).toBe('')
  })

  it('连续快速输入只发最终查询', async () => {
    vi.mocked(getConversations).mockResolvedValue(pageResponse(['最终结果']) as never)
    await mountComposable()

    sessions.setConversationSearch('合')
    await vi.advanceTimersByTimeAsync(100)
    sessions.setConversationSearch('合同')
    await vi.advanceTimersByTimeAsync(100)
    sessions.setConversationSearch('合同审查')
    await vi.advanceTimersByTimeAsync(280)
    await flushPromises()

    expect(getConversations).toHaveBeenCalledTimes(1)
    expect(getConversations).toHaveBeenLastCalledWith({ page: 1, page_size: 50, search: '合同审查' })
  })

  it('搜索查询变更时丢弃在途的过期响应', async () => {
    let resolveOld!: (value: unknown) => void
    let resolveNew!: (value: unknown) => void
    const oldPending = new Promise((resolve) => { resolveOld = resolve })
    const newPending = new Promise((resolve) => { resolveNew = resolve })
    vi.mocked(getConversations)
      .mockImplementationOnce(() => oldPending as never)
      .mockImplementationOnce(() => newPending as never)
    await mountComposable()

    const initialRequest = sessions.fetchConversations()
    sessions.setConversationSearch('合同审查')
    await vi.advanceTimersByTimeAsync(280)
    expect(getConversations).toHaveBeenCalledTimes(2)

    resolveNew(pageResponse(['最新结果']))
    await flushPromises()
    resolveOld(pageResponse(['过期结果']))
    await flushPromises()
    await initialRequest

    expect(sessions.conversations.value.map((c) => c.title)).toEqual(['最新结果'])
  })

  it('从后续页发起新查询时把 currentPage 重置为 1（M3 判别）', async () => {
    vi.mocked(getConversations).mockResolvedValue(pageResponse(['合同审查']) as never)
    await mountComposable()

    // 制造「当前在第 2 页」的真实状态：hasMore 由首屏 50 条数据推出。
    const fullPage = Array.from({ length: 50 }, (_, i) => `会话${i}`)
    vi.mocked(getConversations).mockResolvedValueOnce(pageResponse(fullPage) as never)
    await sessions.fetchConversations()
    await sessions.loadMore()
    expect(sessions.currentPage.value).toBe(2)

    // 查询变更在防抖到期时把 currentPage 重置回 1（变异：去掉重置 → 这里红）。
    sessions.setConversationSearch('合同')
    await vi.advanceTimersByTimeAsync(280)
    await flushPromises()
    expect(sessions.currentPage.value).toBe(1)
    expect(getConversations).toHaveBeenLastCalledWith({ page: 1, page_size: 50, search: '合同' })
  })

  it('请求用 trim 后的词，显示值保留原始输入（不吞键入空格）', async () => {
    vi.mocked(getConversations).mockResolvedValue(pageResponse(['合同审查']) as never)
    await mountComposable()

    // 输入框是受控组件：conversationSearch 必须保留原始输入，否则用户
    // 键入「合同 审查」时中间空格会被立即 trim 掉，词打不出来。
    sessions.setConversationSearch('  合同  ')
    expect(sessions.conversationSearch.value).toBe('  合同  ')
    await vi.advanceTimersByTimeAsync(280)
    await flushPromises()
    // 但发往 API 的 search 必须是 trim 后的干净词，UI 与请求语义一致。
    expect(getConversations).toHaveBeenLastCalledWith({ page: 1, page_size: 50, search: '合同' })
  })
})

// n-input 替身：把 Naive UI 的 v-model / clear 事件落成原生 DOM 事件，便于断言侧栏接线。
const NInputStub = defineComponent({
  name: 'NInputStub',
  props: {
    value: { type: String, default: '' },
    placeholder: { type: String, default: '' },
  },
  emits: ['update:value', 'clear'],
  setup(props, { emit, slots }) {
    return () => h('div', [
      slots.prefix?.(),
      h('input', {
        'data-testid': 'chat-conversation-search',
        placeholder: props.placeholder,
        value: props.value,
        onInput: (e: Event) => emit('update:value', (e.target as HTMLInputElement).value),
      }),
      h('button', {
        type: 'button',
        'data-testid': 'native-clear',
        onClick: () => emit('clear'),
      }, 'clear'),
    ])
  },
})

function mountSidebar(search = '', titles: string[] = []) {
  const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })
  return mount(ChatSidebar, {
    props: {
      sidebarOpen: true,
      conversations: titles.map((title, index) => ({
        id: `c${index}`,
        title,
        created_at: '2026-10-09T13:49:00',
        updated_at: '',
        message_count: 1,
        last_message: 'hi',
      })),
      isListLoading: false,
      convLoadError: false,
      currentConversationId: undefined,
      conversationSearch: search,
    },
    global: {
      plugins: [i18n],
      stubs: {
        'n-input': NInputStub,
        'n-button': true,
        'n-popconfirm': true,
        'n-tooltip': true,
        'n-icon': true,
        'n-skeleton': true,
        'n-result': true,
      },
    },
  })
}

describe('ChatSidebar · 搜索 UI 与空态', () => {
  beforeEach(() => vi.clearAllMocks())

  it('搜索关键词无结果时渲染搜索专用空态（不是「无历史会话」）', () => {
    const wrapper = mountSidebar('合同', [])
    expect(wrapper.text()).toContain(zh.chat.conversationSearch.empty)
    expect(wrapper.text()).not.toContain(zh.chat.noHistory)
    expect(wrapper.find('[data-testid="chat-conversation-search-clear"]').exists()).toBe(true)
  })

  it('输入框与清除按钮分别抛出 searchChange / searchClear', async () => {
    const wrapper = mountSidebar()
    const input = wrapper.find('[data-testid="chat-conversation-search"]')
    expect(input.attributes('placeholder') ?? '').toBe(zh.chat.conversationSearch.placeholder)

    await input.setValue('合同')
    expect(wrapper.emitted('searchChange')).toEqual([['合同']])

    await wrapper.find('[data-testid="native-clear"]').trigger('click')
    expect(wrapper.emitted('searchClear')).toHaveLength(1)
  })

  it('无搜索词且无会话时仍显示原有「无历史会话」空态', () => {
    const wrapper = mountSidebar('', [])
    expect(wrapper.text()).toContain(zh.chat.noHistory)
    expect(wrapper.text()).not.toContain(zh.chat.conversationSearch.empty)
  })
})
