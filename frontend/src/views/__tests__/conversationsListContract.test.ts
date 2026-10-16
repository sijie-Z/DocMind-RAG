/**
 * B31/B30 契约回归：会话历史页时间渲染与搜索参数。
 *
 * 真值（backend/app/api/v1/endpoints/chat.py:187-192 列表响应）：
 *   - updated_at 可为空串（`isoformat() if s.updated_at else ""`）——空串上屏
 *     "Invalid Date"（A_sweep CONV-1 实锤，3/3 行）；
 *   - 搜索参数名为 `search`（:158 前端已发），后端过滤见 test_api_chat.py。
 *
 * 变异点：formatDate 去守卫 → 「无 Invalid Date」断言红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createI18n } from 'vue-i18n'
import naive, { NDialogProvider } from 'naive-ui'

const { messageSpies } = vi.hoisted(() => ({
  messageSpies: { error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn() },
}))
vi.mock('@/utils/message', () => ({ useDedupedMessage: () => messageSpies }))

vi.mock('@/api/conversation', () => ({
  getConversations: vi.fn(),
  deleteConversation: vi.fn().mockResolvedValue(undefined),
}))

import ConversationsPage from '../conversations/index.vue'
import { getConversations } from '@/api/conversation'

// 真响应形状（chat.py:187-196）：data.data 数组；updated_at 空串与正常值并存
const LIST_RESPONSE = {
  data: {
    data: {
      data: [
        { id: 'c1', title: '会话一', message_count: 2, last_message: 'hi', created_at: '2026-10-09T13:49:00', updated_at: '' },
        { id: 'c2', title: '会话二', message_count: 5, last_message: 'yo', created_at: '2026-10-10T04:44:00', updated_at: '2026-10-10T05:00:00' },
      ],
      total: 2,
      page: 1,
      page_size: 10,
    },
  },
}

const Host = defineComponent({
  setup() {
    return () => h(NDialogProvider, null, { default: () => h(ConversationsPage) })
  },
})

const i18n = createI18n({
  legacy: false, locale: 'zh', missingWarn: false, fallbackWarn: false,
  messages: { zh: { conversations: { title: '会话历史' }, menu: { conversations: '历史记录' }, common: {} } },
})

async function mountPage() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/conversations', component: { template: '<div />' } }],
  })
  await router.push('/conversations')
  await router.isReady()
  const pinia = createPinia()
  setActivePinia(pinia)
  const wrapper = mount(Host, { global: { plugins: [naive, pinia, router, i18n] } })
  await flushPromises()
  return wrapper
}

describe('conversations 页 · 时间与搜索（B31/B30 前端侧）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getConversations).mockResolvedValue(LIST_RESPONSE as never)
  })

  it('updated_at 空串不渲染时间、不上屏 Invalid Date；正常值照常渲染（CONV-1 回归）', async () => {
    const wrapper = await mountPage()
    const text = wrapper.text()

    expect(text).toContain('会话一')
    expect(text).toContain('会话二')
    // A_sweep 实锤：修复前 3/3 行渲染 "Invalid Date"
    expect(text).not.toContain('Invalid Date')
    // 正常值行仍渲染 MM-DD HH:mm
    expect(text).toContain('10-10 05:00')
    // 空串行不渲染时间（不含任何 "10-09" 形式时间）
    expect(text).not.toContain('10-09 13:49')
  })

  it('搜索参数名为 search，随输入透传（前端侧回归守卫）', async () => {
    const wrapper = await mountPage()
    const vm = wrapper.findComponent(ConversationsPage).vm as unknown as {
      searchText: string
      loadConversations: () => Promise<void>
    }
    vm.searchText = '四个字'
    await vm.loadConversations()
    await flushPromises()

    expect(getConversations).toHaveBeenLastCalledWith({ page: 1, page_size: 10, search: '四个字' })
  })
})
