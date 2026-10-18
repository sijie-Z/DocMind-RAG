/**
 * G2 · 英文态侧栏分组标题的折叠隐藏/截断策略。
 *
 * 现场（存量截图 23-lang-switched-prompts）：折叠态侧栏只有 80px 宽，分组标题
 * "Workspace / Knowledge / Intelligence" 没有截断策略，被硬裁成
 * "Works/Knowl/Intellig" 并挤压图标列。
 * 修法：折叠态不渲染组标题文本（整行收起并标记 aria-hidden）；展开态以
 * truncate 单行省略 + title 悬浮全称。
 *
 * 说明：当前菜单配置未启用分组标题键，本用例通过注入分组键验证承载机制本身
 * （layouts 的 groupI18nKey 渲染路径）。
 *
 * 变异点：折叠态重新渲染标题文本 → 用例 1 红；展开态去掉 truncate/title →
 * 用例 2 红。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createI18n } from 'vue-i18n'
import { defineComponent } from 'vue'
import naive from 'naive-ui'
import en from '@/locales/en'

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
  getUserSettings: vi.fn(),
  updateUserSettings: vi.fn(),
  updateUserProfile: vi.fn().mockResolvedValue({ data: {} }),
}))

// 注入一个英文长词分组键（开发分支的菜单未启用组标题，此处验证策略路径）
vi.mock('@/config/menu', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/config/menu')>()
  const [first, ...rest] = actual.SIDEBAR_MENU_GROUPS
  return {
    ...actual,
    SIDEBAR_MENU_GROUPS: [{ ...first, groupI18nKey: 'menu.groupWorkspace' }, ...rest],
  }
})

import Layouts from '../index.vue'
import { useAppStore } from '@/stores/app'
import { useUserStore } from '@/stores/user'
import { getUserSettings } from '@/api/user'

const i18n = createI18n({
  legacy: false,
  locale: 'en',
  missingWarn: false,
  fallbackWarn: false,
  messages: {
    en: { ...en, menu: { ...en.menu, groupWorkspace: 'Intelligence Workspace' } },
  },
})

const DummyRoute = defineComponent({ template: '<div />' })

let currentWrapper: { unmount: () => void } | null = null

async function mountLayout(collapsed: boolean) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', name: 'Chat', component: DummyRoute }],
  })
  await router.push('/')
  await router.isReady()
  const pinia = createPinia()
  setActivePinia(pinia)
  const userStore = useUserStore()
  userStore.userInfo = { id: 1, username: 'u' } as never
  const appStore = useAppStore()
  appStore.sidebarCollapsed = collapsed
  const wrapper = mount(Layouts, { global: { plugins: [naive, pinia, router, i18n] } })
  currentWrapper = wrapper
  await flushPromises()
  await flushPromises()
  return { wrapper, appStore }
}

beforeEach(() => {
  localStorage.clear()
  vi.clearAllMocks()
  localStorage.setItem('docmind_token', 'tok')
  vi.mocked(getUserSettings).mockResolvedValue({
    data: { data: { theme: 'light', language: 'en' } },
  } as never)
})

afterEach(async () => {
  currentWrapper?.unmount()
  currentWrapper = null
  await flushPromises()
})

describe('G2 · 侧栏分组标题折叠/截断策略', () => {
  it('折叠态：标题文本不渲染（不硬裁溢出），标题行以 aria-hidden 收起', async () => {
    const { wrapper } = await mountLayout(true)

    expect(wrapper.text()).not.toContain('Intelligence Workspace')
    const title = wrapper.find('.n-menu-item-group-title')
    expect(title.exists(), '分组标题容器应存在（由折叠策略收起）').toBe(true)
    expect(title.attributes('aria-hidden')).toBe('true')
    expect(title.classes()).toContain('sidebar-group-title--collapsed')
  })

  it('展开态：完整词条单行 truncate，title 悬浮全称', async () => {
    const { wrapper } = await mountLayout(false)

    expect(wrapper.text()).toContain('Intelligence Workspace')
    const title = wrapper.find('.n-menu-item-group-title')
    expect(title.exists()).toBe(true)
    const span = title.find('span')
    expect(span.exists(), '标题应以单行 span 渲染').toBe(true)
    expect(span.classes()).toContain('truncate')
    expect(span.attributes('title')).toBe('Intelligence Workspace')
  })
})
