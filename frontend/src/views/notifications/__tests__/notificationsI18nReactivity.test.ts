/**
 * 通知页 i18n 真·响应式判别用例。
 *
 * 修前：类型/已读下拉选项、表格列头、汇总卡片、类型分布全部是 setup 期
 * 常量或硬编码中文，切换语言后页面残留中文。
 * 修复：选项与列定义改 computed（依赖 t），汇总类型码经 typeLabel 本地化。
 *
 * 变异点：把 typeOptions/readOptions/columns 退回常量，或汇总改回 `{{ key }}`
 * → 对应断言红。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createI18n } from 'vue-i18n'
import { defineComponent, h, nextTick } from 'vue'
import naive, { NMessageProvider, NSelect } from 'naive-ui'

import NotificationsView from '../index.vue'
import zh from '@/locales/zh'
import en from '@/locales/en'

const apiMocks = vi.hoisted(() => ({
  getNotifications: vi.fn(),
  getNotificationSummary: vi.fn(),
  markAllAsRead: vi.fn().mockResolvedValue({}),
  markAsRead: vi.fn().mockResolvedValue({}),
  deleteNotification: vi.fn().mockResolvedValue({}),
  batchDeleteNotifications: vi.fn().mockResolvedValue({}),
}))

vi.mock('@/api/notification', () => apiMocks)
vi.mock('@/utils/notificationSocket', () => ({
  notificationSocket: { onNotification: vi.fn(), offNotification: vi.fn() },
}))

const i18n = createI18n({
  legacy: false,
  locale: 'zh',
  fallbackLocale: 'en',
  messages: { zh, en },
})

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'Home', component: { template: '<div />' } },
      { path: '/notifications', name: 'Notifications', component: { template: '<div />' } },
      { path: '/dashboard', name: 'Dashboard', component: { template: '<div />' } },
    ],
  })
}

function trigger() {
  return apiMocks.getNotifications.mockResolvedValue({
    data: {
      items: [
        {
          id: 1,
          title: '系统维护通知',
          content: '今晚 22:00 维护',
          type: 'system',
          is_read: false,
          target_route: null,
          target_id: null,
          created_at: '2026-11-01T10:00:00',
        },
      ],
      total: 1,
    },
  })
}

async function mountPage() {
  const router = makeRouter()
  await router.push('/notifications')
  const wrapper = mount(
    defineComponent({
      render: () => h(NMessageProvider, null, { default: () => h(NotificationsView) }),
    }),
    { global: { plugins: [naive, router, i18n] } },
  )
  await flushPromises()
  await flushPromises()
  return wrapper
}

function headers(wrapper: ReturnType<typeof mount>) {
  return wrapper.findAll('th').map((th) => th.text())
}

beforeEach(() => {
  vi.clearAllMocks()
  i18n.global.locale.value = 'zh'
  trigger()
  apiMocks.getNotificationSummary.mockResolvedValue({
    data: { total: 1, unread_count: 1, by_type: { system: 1 } },
  })
})

afterEach(() => {
  i18n.global.locale.value = 'zh'
})

describe('通知页 · 语言切换后文案实时更新', () => {
  it('汇总卡片与类型分布随 locale 切换', async () => {
    const wrapper = await mountPage()
    expect(wrapper.text()).toContain('总通知')
    expect(wrapper.text()).toContain('类型分布')
    expect(wrapper.text()).toContain('系统: 1')

    i18n.global.locale.value = 'en'
    await nextTick()
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('Total')
    expect(text).toContain('By type')
    expect(text, '类型分布仍显示中文类型名').toContain('System: 1')
    expect(text).not.toContain('总通知')
    wrapper.unmount()
  })

  it('表格列头与已读状态随 locale 切换（computed 列）', async () => {
    const wrapper = await mountPage()
    expect(headers(wrapper)).toEqual(
      expect.arrayContaining(['标题', '内容', '类型', '时间', '状态', '操作']),
    )
    // 行内状态单元格（selection,标题,内容,类型,时间,状态,操作 → index 5）；
    // 不用 wrapper.text() 断言，否则会被「只看未读」按钮满足而空转（评审 B）
    const statusCell = () => wrapper.findAll('tbody tr')[0].findAll('td')[5]
    expect(statusCell().text()).toBe('未读')

    // 下拉选项本身（typeOptions/readOptions 必须是 computed，否则不随语言变）
    const optionLabels = () =>
      wrapper.findAllComponents(NSelect)
        .map((c) => (c.props('options') as Array<{ label: string }>).map((o) => o.label))
        .flat()
    expect(optionLabels()).toEqual(expect.arrayContaining(['系统', '知识库', '未读', '全部']))

    i18n.global.locale.value = 'en'
    await nextTick()
    await flushPromises()

    expect(headers(wrapper)).toEqual(
      expect.arrayContaining(['Title', 'Content', 'Type', 'Time', 'Status', 'Actions']),
    )
    expect(headers(wrapper), '切换后列头残留中文').not.toContain('标题')
    expect(optionLabels()).toEqual(
      expect.arrayContaining(['System', 'Knowledge base', 'Unread', 'All']),
    )
    // 行内状态单元格必须是英文（不与筛选按钮文案共用断言）
    expect(statusCell().text()).toBe('Unread')
    wrapper.unmount()
  })
})
