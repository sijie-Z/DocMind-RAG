/**
 * 会话历史页 i18n：模板与消息提示不得硬编码中文。
 *
 * 背景：`views/conversations/index.vue` 此前整页写死中文（副标题/新对话/
 * 搜索占位/加载中/重试/空态/继续/未命名/条消息/加载失败/删除成功与失败），
 * 切到 en/ja/fr 后整页仍是中文。`conversations.*` 命名空间早已存在于四语
 * locale 中，只是页面没用。本用例以真实 locale 文件挂载，切语言后断言
 * 英文文案出现、中文文案消失。
 *
 * 变异点：把任一 t('conversations.*') 退回硬编码中文 → en 下断言红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
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

import ConversationsPage from '../index.vue'
import { getConversations } from '@/api/conversation'
import zh from '@/locales/zh'
import en from '@/locales/en'

const LIST_RESPONSE = {
  data: {
    data: {
      data: [
        { id: 'c1', title: '', message_count: 2, last_message: 'hi', created_at: '2026-10-09T13:49:00', updated_at: '2026-10-09T13:49:00' },
      ],
      total: 1,
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

async function mountWith(locale: 'zh' | 'en') {
  const i18n = createI18n({
    legacy: false, locale, missingWarn: false, fallbackWarn: false,
    messages: { zh, en },
  })
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
  return { wrapper, i18n }
}

describe('会话历史页 · i18n', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getConversations).mockResolvedValue(LIST_RESPONSE as never)
  })

  it('zh 下渲染中文文案（副标题/搜索占位/继续/条消息/未命名）', async () => {
    const { wrapper } = await mountWith('zh')
    const text = wrapper.text()
    expect(text).toContain('管理您过去的所有对话记录')
    expect(text).toContain('继续')
    expect(text).toContain('条消息')
    expect(text).toContain('未命名会话')
    expect(wrapper.find('input').attributes('placeholder')).toBe('搜索会话标题...')
  })

  it('切到 en 后页面文案全英文、不留中文（整页无 CJK）', async () => {
    const { wrapper, i18n } = await mountWith('zh')
    ;(i18n.global.locale as unknown as { value: string }).value = 'en'
    await nextTick()
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('Manage all your past conversation records')
    expect(text).toContain('Continue')
    expect(text).toContain('messages')
    expect(text).toContain('Untitled Chat')
    expect(wrapper.find('input').attributes('placeholder')).toBe('Search conversation titles...')

    // 硬约束：整页不得有 CJK（会话数据本身标题为空，不会引入中文）
    expect(text).not.toMatch(/[㐀-䶿一-鿿]/)
  })

  it('加载失败提示同样走 i18n（en 下 message.error 与错误态都用英文）', async () => {
    vi.mocked(getConversations).mockRejectedValue(new Error('boom'))
    const { wrapper } = await mountWith('en')
    await flushPromises()
    expect(messageSpies.error).toHaveBeenCalledWith('Failed to load')
    expect(wrapper.text()).toContain('Failed to load')
    expect(wrapper.text()).not.toMatch(/[㐀-䶿一-鿿]/)
  })
})
