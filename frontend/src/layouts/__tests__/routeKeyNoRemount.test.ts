/**
 * E11-③ 切换会话不整页重挂载：布局路由包装 key 由 fullPath 改为 route.name。
 *
 * 修前：layouts/index.vue 的页面包装 div `:key="route.fullPath"`——聊天页内
 * 切换会话（只改 query: conversation_id）即整棵 ChatPage 卸载重挂（Transition
 * out-in），消息区白屏闪烁、进行中的输入丢失。
 * 修复：key 改为 route.name——同路由仅 query 变化不重挂；跨路由仍正常重挂。
 * 聊天页内部另以 watch(conversation_id) 驱动会话加载（见
 * chat/__tests__/chatConversationSwitch.test.ts）。
 *
 * 变异点：
 * - key 回退 fullPath → 「同路由 query 变化不重挂」用例红（setup 计数 +1）；
 * - key 误改成常量（连跨路由也不重挂）→ 「跨路由仍重挂」正对照用例红。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createI18n } from 'vue-i18n'
import { defineComponent, h } from 'vue'
import naive from 'naive-ui'

vi.mock('@/utils/message', () => ({
  useDedupedMessage: () => ({ error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn(), loading: vi.fn() }),
}))

vi.mock('@/utils/notificationSocket', () => ({
  notificationSocket: {
    connect: vi.fn(),
    disconnect: vi.fn(),
    onNotification: vi.fn(),
    offNotification: vi.fn(),
  },
}))

vi.mock('@/api/notification', () => ({
  getNotifications: vi.fn().mockResolvedValue({ data: { items: [] } }),
  getUnreadCount: vi.fn().mockResolvedValue({ data: { count: 0 } }),
  getNotificationSummary: vi.fn().mockResolvedValue({ data: { total: 0, unread_count: 0, by_type: {} } }),
  markAsRead: vi.fn().mockResolvedValue(undefined),
  markAllAsRead: vi.fn().mockResolvedValue(undefined),
  deleteNotification: vi.fn().mockResolvedValue(undefined),
}))

vi.mock('@/api/user', () => ({
  getUserSettings: vi.fn().mockResolvedValue({ data: { data: {} } }),
  updateUserSettings: vi.fn().mockResolvedValue({ data: {} }),
  updateUserProfile: vi.fn().mockResolvedValue({ data: {} }),
}))

import Layouts from '../index.vue'

const i18n = createI18n({
  legacy: false,
  locale: 'zh',
  missingWarn: false,
  fallbackWarn: false,
  messages: {
    zh: {
      menu: {
        systemTitle: 'DocMind', home: '首页', chat: '智能对话', conversations: '历史记录',
        knowledge: '知识库', prompts: '提示词管理', search: '语义搜索', users: '用户管理',
        organizations: '组织管理', auditLogs: '审计日志', monitoring: '监控', mcpServers: 'MCP',
        personalSettings: '个人设置', workflow: 'Agent 工作流', agent: '智能 Agent',
        scheduledTasks: '定时任务', notifications: '通知', systemHelp: '系统帮助',
        systemAbout: '关于', logout: '退出登录',
      },
      profile: { theme: '主题', language: '语言' },
      notifications: { empty: '暂无通知' },
      common: {},
    },
  },
})

// setup 计数探针：每次组件被重新挂载计数 +1
let chatSetupCount = 0
const ChatProbe = defineComponent({
  name: 'ChatProbe',
  setup() {
    chatSetupCount++
    return () => h('div', { class: 'chat-probe' }, `setup:${chatSetupCount}`)
  },
})
const SearchProbe = defineComponent({
  name: 'SearchProbe',
  setup() {
    return () => h('div', { class: 'search-probe' }, 'search')
  },
})

let currentWrapper: { unmount: () => void } | null = null

async function mountLayouts(initial: string) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/chat', name: 'Chat', component: ChatProbe },
      { path: '/search', name: 'Search', component: SearchProbe },
    ],
  })
  await router.push(initial)
  await router.isReady()
  const pinia = createPinia()
  setActivePinia(pinia)
  // 不设 token：布局 onMounted 的通知/设置轮询分支整体跳过，专注路由 key 行为
  const wrapper = mount(Layouts, { global: { plugins: [naive, pinia, router, i18n] } })
  currentWrapper = wrapper
  await flushPromises()
  return { wrapper, router }
}

beforeEach(() => {
  localStorage.clear()
  chatSetupCount = 0
})

afterEach(async () => {
  currentWrapper?.unmount()
  currentWrapper = null
  await flushPromises()
})

describe('E11-③ 布局路由 key：同路由 query 变化不整页重挂载', () => {
  it('在聊天页切换会话（?conversation_id 变化）：ChatPage 不重挂载（setup 计数保持 1）', async () => {
    const { wrapper, router } = await mountLayouts('/chat')
    expect(chatSetupCount).toBe(1)
    expect(wrapper.find('.chat-probe').exists()).toBe(true)

    await router.push('/chat?conversation_id=conv-2')
    await flushPromises()
    await vi.waitFor(() => expect(router.currentRoute.value.query.conversation_id).toBe('conv-2'))

    // 修前 fullPath key 变化 → 整棵组件树重建，计数变 2
    expect(chatSetupCount).toBe(1)
    expect(wrapper.find('.chat-probe').text()).toBe('setup:1')
  })

  it('正对照：跨路由（Chat → Search → Chat）仍正常重挂载（key 不会过度收敛）', async () => {
    const { wrapper, router } = await mountLayouts('/chat')
    expect(chatSetupCount).toBe(1)

    await router.push('/search')
    await vi.waitFor(() => expect(wrapper.find('.search-probe').exists()).toBe(true))

    await router.push('/chat')
    await vi.waitFor(() => expect(wrapper.find('.chat-probe').exists()).toBe(true))
    expect(chatSetupCount).toBe(2)
  })
})
