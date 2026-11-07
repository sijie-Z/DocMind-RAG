/**
 * 侧栏折叠按钮（品牌头部单按钮）行为：折叠/展开切换时按钮不卸载（焦点保持）、
 * aria-label/aria-expanded 随态变化、侧栏宽度切换、移动端隐藏。
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

import Layouts from '../index.vue'
import { useAppStore } from '@/stores/app'
import { useUserStore } from '@/stores/user'
import { getUserSettings } from '@/api/user'

const i18n = createI18n({
  legacy: false,
  locale: 'en',
  missingWarn: false,
  fallbackWarn: false,
  messages: { en },
})

const DummyRoute = defineComponent({ template: '<div />' })
let currentWrapper: { unmount: () => void } | null = null

async function mountLayout() {
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
  appStore.sidebarCollapsed = false
  const wrapper = mount(Layouts, {
    attachTo: document.body,
    global: { plugins: [naive, pinia, router, i18n] },
  })
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

describe('侧栏折叠按钮', () => {
  it('折叠/展开切换：同一按钮不卸载、焦点保持、aria 与宽度随态变化', async () => {
    const { wrapper, appStore } = await mountLayout()
    const toggle = wrapper.find('[data-testid="sidebar-toggle"]')
    expect(toggle.exists()).toBe(true)
    expect(toggle.attributes('aria-expanded')).toBe('true')
    expect(toggle.attributes('aria-label')?.toLowerCase()).toContain('collapse')
    expect(toggle.classes()).toEqual(expect.arrayContaining(['h-11', 'w-11', 'hidden', 'md:flex']))

    const element = toggle.element as HTMLElement
    element.focus()
    await toggle.trigger('click')
    await flushPromises()

    expect(appStore.sidebarCollapsed).toBe(true)
    const collapsedToggle = wrapper.find('[data-testid="sidebar-toggle"]')
    expect(collapsedToggle.element).toBe(element)
    expect(collapsedToggle.attributes('aria-expanded')).toBe('false')
    expect(collapsedToggle.attributes('aria-label')?.toLowerCase()).toContain('expand')
    expect(document.activeElement).toBe(element)
    expect(wrapper.find('#app-sidebar').classes()).toContain('w-20')

    await collapsedToggle.trigger('click')
    await flushPromises()
    expect(appStore.sidebarCollapsed).toBe(false)
    expect(wrapper.find('#app-sidebar').classes()).toContain('w-64')
  })
})
