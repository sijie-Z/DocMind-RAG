/**
 * 布局顶栏通知下拉的 i18n：删除按钮不得硬编码中文。
 *
 * 背景：`layouts/index.vue` 的通知下拉每行末的「删除」按钮是唯一硬编码
 * 中文的可见操作（同页其余文案都已走词条）。切到 en/ja/fr 后中英混杂。
 *
 * 变异点：删除按钮退回 `>删除<` → 用例 1 红（渲染层断言，非源码字符串）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createI18n } from 'vue-i18n'
import { defineComponent } from 'vue'
import naive from 'naive-ui'
import en from '@/locales/en'
import fr from '@/locales/fr'

const { messageSpies } = vi.hoisted(() => ({
  messageSpies: { error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn(), loading: vi.fn() },
}))
vi.mock('@/utils/message', () => ({ useDedupedMessage: () => messageSpies }))

vi.mock('@/utils/notificationSocket', () => ({
  notificationSocket: {
    connect: vi.fn(),
    disconnect: vi.fn(),
    onNotification: vi.fn(),
    offNotification: vi.fn(),
  },
}))

vi.mock('@/api/notification', () => ({
  getNotifications: vi.fn().mockResolvedValue({
    data: { items: [{ id: 1, title: 'T', content: 'C', type: 'system', is_read: false, created_at: '2026-10-09T00:00:00Z' }] },
  }),
  getUnreadCount: vi.fn().mockResolvedValue({ data: { count: 1 } }),
  getNotificationSummary: vi.fn().mockResolvedValue({ data: { total: 1, unread_count: 1, by_type: {} } }),
  markAsRead: vi.fn().mockResolvedValue(undefined),
  markAllAsRead: vi.fn().mockResolvedValue(undefined),
  deleteNotification: vi.fn().mockResolvedValue(undefined),
}))

vi.mock('@/api/user', () => ({
  getUserSettings: vi.fn().mockResolvedValue({ data: { data: { theme: 'light', language: 'en' } } }),
  updateUserSettings: vi.fn(),
  updateUserProfile: vi.fn().mockResolvedValue({ data: {} }),
}))

import Layouts from '../index.vue'
import { useUserStore } from '@/stores/user'
import { useNotificationStore } from '@/stores/notification'

const i18n = createI18n({ legacy: false, locale: 'en', missingWarn: false, fallbackWarn: false, messages: { en, fr } })

let currentWrapper: { unmount: () => void } | null = null

async function mountLayout() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', name: 'Chat', component: defineComponent({ template: '<div />' }) }],
  })
  await router.push('/')
  await router.isReady()
  const pinia = createPinia()
  setActivePinia(pinia)
  useUserStore().userInfo = { id: 1, username: 'u' } as never
  const wrapper = mount(Layouts, { attachTo: document.body, global: { plugins: [naive, pinia, router, i18n] } })
  currentWrapper = wrapper
  await flushPromises()
  await flushPromises()
  return { wrapper, notificationStore: useNotificationStore() }
}

beforeEach(() => {
  localStorage.clear()
  vi.clearAllMocks()
  localStorage.setItem('docmind_token', 'tok')
})

afterEach(async () => {
  currentWrapper?.unmount()
  currentWrapper = null
  document.body.innerHTML = ''
  await flushPromises()
})

describe('布局顶栏 · 通知下拉 i18n', () => {
  it('en 下通知行末操作按钮渲染 Delete，不含「删除」', async () => {
    const { wrapper, notificationStore } = await mountLayout()
    // 直接播种 headerItems（与真实加载同数据结构），避免依赖 popover 打开时序
    notificationStore.headerItems = [
      { id: 1, title: 'T', content: 'C', type: 'system', isRead: false, createdAt: '2026-10-09T00:00:00Z' },
    ] as never
    await flushPromises()

    // 打开铃铛下拉（naive-ui popover：点 trigger 才挂载内容）
    const bell = wrapper.find('button[aria-label="Notifications"]')
    expect(bell.exists(), '顶栏通知按钮应存在').toBe(true)
    await bell.trigger('click')
    await flushPromises()

    // popover 内容 teleport 到 body：在 document 层面断言
    const bodyText = document.body.innerText || document.body.textContent || ''
    expect(bodyText, '不应出现硬编码「删除」').not.toContain('删除')
    expect(bodyText, '删除按钮应走 common.delete → Delete').toContain('Delete')

    // 第二语言：fr 下必须是 Supprimer —— 硬编码英文（>Delete<）在这里会露馅（评审 B 变异 2）
    ;(i18n.global.locale as unknown as { value: string }).value = 'fr'
    await flushPromises()
    const frText = document.body.innerText || document.body.textContent || ''
    expect(frText, 'fr 下删除按钮应走词条 → Supprimer').toContain('Supprimer')
    expect(frText, '硬编码英文 Delete 不应出现在 fr 界面').not.toContain('Delete')
  })

  it('errors.pageLoadFailed 四语齐备（错误提示所用词条存在）', async () => {
    const zh = (await import('@/locales/zh')).default
    const en = (await import('@/locales/en')).default
    const ja = (await import('@/locales/ja')).default
    const fr = (await import('@/locales/fr')).default
    const zhVal = (zh.errors as Record<string, string>).pageLoadFailed
    expect(zhVal, 'zh.errors.pageLoadFailed 应存在').toBeTruthy()
    for (const [loc, msgs] of [['en', en], ['ja', ja], ['fr', fr]] as const) {
      const v = (msgs.errors as Record<string, string>).pageLoadFailed
      expect(v, `${loc}.errors.pageLoadFailed 应存在`).toBeTruthy()
      expect(v, `${loc}.errors.pageLoadFailed 不得照抄中文`).not.toBe(zhVal)
    }
  })
})
