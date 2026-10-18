/**
 * 会话侧栏列表契约（T15 · 响应解包收敛）。
 *
 * 真响应形状（backend/app/api/v1/endpoints/chat.py get_conversations）：
 *   {success: true, message: "获取成功",
 *    data: {data: [<会话>], total, page, page_size}}
 * 迁移 apiUnwrap 后必须与旧手写双层解包同形：数组入列、hasMore 按页长判定。
 *
 * 变异点：把 fetchConversations 退回 res.data.data.data 读取 →
 * 「真实双层信封」用例精确红（列表恒空、hasMore 恒 false 走 else 分支）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
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

import { getConversations } from '@/api/conversation'
import { useChatSessions } from '../composables/useChatSessions'

// 真响应字段逐字对齐 chat.py 列表端点（updated_at 可为空串）
const CONVERSATIONS = [
  {
    id: 'c1',
    title: '会话一',
    created_at: '2026-10-09T13:49:00',
    updated_at: '',
    message_count: 2,
    last_message: 'hi',
  },
  {
    id: 'c2',
    title: '会话二',
    created_at: '2026-10-10T04:44:00',
    updated_at: '2026-10-10T05:00:00',
    message_count: 5,
    last_message: 'yo',
  },
]

const REAL_RESPONSE = {
  data: {
    success: true,
    message: '获取成功',
    data: { data: CONVERSATIONS, total: 42, page: 1, page_size: 50 },
  },
}

const EMPTY_RESPONSE = {
  data: {
    success: true,
    message: '获取成功',
    data: { data: [], total: 0, page: 1, page_size: 50 },
  },
}

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

describe('useChatSessions · 列表形状契约（真实双层信封）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('真实双层信封 → 数组入列、hasMore=false、错误态清零', async () => {
    vi.mocked(getConversations).mockResolvedValue(REAL_RESPONSE as never)
    await mountComposable()
    await sessions.fetchConversations()

    expect(sessions.conversations.value.map((c) => c.id)).toEqual(['c1', 'c2'])
    // 每页 50，本次仅 2 条 → 无更多
    expect(sessions.hasMore.value).toBe(false)
    expect(sessions.convLoadError.value).toBe(false)
    // 请求参数保持 page/page_size（chat.py 声明的查询参数）
    expect(getConversations).toHaveBeenLastCalledWith({ page: 1, page_size: 50 })
  })

  it('空列表信封 → 空态且 hasMore=false，不抛错', async () => {
    vi.mocked(getConversations).mockResolvedValue(EMPTY_RESPONSE as never)
    await mountComposable()
    await sessions.fetchConversations()

    expect(sessions.conversations.value).toEqual([])
    expect(sessions.hasMore.value).toBe(false)
    expect(sessions.convLoadError.value).toBe(false)
  })
})
