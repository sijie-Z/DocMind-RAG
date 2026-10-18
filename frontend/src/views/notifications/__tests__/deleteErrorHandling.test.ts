/**
 * E14/D3 · 通知页删除错误处理判别用例。
 *
 * 期望行为：
 * - 单条删除失败：失败可见（一次 toast，后端 detail 原文；axios 英文不）上屏），
 *   列表保持原样、不刷新（状态如实）；
 * - 批量删除失败：选中状态保留（不得假装已删），失败可见；
 * - 删除成功：行消失、选择清空、列表刷新；
 * - 汇总接口失败不阻塞列表加载（统计用占位符如实表示未知）。
 *
 * 变异点：catch 吞错 / 批删失败清选择 / 失败后仍刷新列表 / 汇总失败阻塞列表 → 红。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { nextTick } from 'vue'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createI18n } from 'vue-i18n'
import naive from 'naive-ui'

import NotificationsView from '../index.vue'
import zh from '@/locales/zh'

const apiMocks = vi.hoisted(() => ({
  getNotifications: vi.fn(),
  getNotificationSummary: vi.fn(),
  markAllAsRead: vi.fn(),
  markAsRead: vi.fn(),
  deleteNotification: vi.fn(),
  batchDeleteNotifications: vi.fn(),
}))
vi.mock('@/api/notification', () => apiMocks)

const messageSpies = vi.hoisted(() => ({
  success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn(), loading: vi.fn(),
}))
vi.mock('@/utils/message', () => ({ useDedupedMessage: () => messageSpies }))

const NOTIFICATION = {
  id: 1,
  title: '文档解析完成',
  content: 'architecture.md 已完成索引',
  type: 'document',
  is_read: false,
  created_at: '2026-10-08T05:29:00',
}

const listPage = (items: unknown[]) => ({ data: { items, total: items.length } })
const summaryBody = { total: 1, unread_count: 1, by_type: { document: 1 } }

function axiosError(status: number, data: unknown, message: string) {
  return Object.assign(new Error(message), {
    isAxiosError: true,
    config: { url: '/x' },
    response: status === 0 ? undefined : { status, data },
  })
}

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

async function mountView() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'Home', component: { template: '<div />' } },
      { path: '/notifications', name: 'Notifications', component: { template: '<div />' } },
      { path: '/dashboard', name: 'Dashboard', component: { template: '<div />' } },
    ],
  })
  await router.push('/notifications')
  await router.isReady()
  const wrapper = mount(NotificationsView, { global: { plugins: [naive, router, i18n] } })
  await flushPromises()
  await flushPromises()
  return wrapper
}

const vmOf = (wrapper: Awaited<ReturnType<typeof mountView>>) =>
  wrapper.vm as unknown as {
    handleDelete: (row: typeof NOTIFICATION) => Promise<void>
    handleBatchDelete: () => Promise<void>
    handleSearch: () => void
    items: unknown[]
    selectedIds: number[]
  }

beforeEach(() => {
  vi.clearAllMocks()
  apiMocks.getNotifications.mockResolvedValue(listPage([NOTIFICATION]))
  apiMocks.getNotificationSummary.mockResolvedValue({ data: summaryBody })
  apiMocks.deleteNotification.mockResolvedValue({})
  apiMocks.batchDeleteNotifications.mockResolvedValue({})
})

afterEach(() => {
  document.body.innerHTML = ''
})

describe('通知页 · D3 单条删除', () => {
  it('删除失败：一次 toast 展示后端 detail、行保留、不刷新列表', async () => {
    const wrapper = await mountView()
    expect(wrapper.text()).toContain(NOTIFICATION.title)

    apiMocks.deleteNotification.mockRejectedValueOnce(
      axiosError(400, { detail: '通知不存在或已被删除' }, 'Request failed with status code 400'),
    )
    await vmOf(wrapper).handleDelete(NOTIFICATION)
    await flushPromises()
    await nextTick()

    expect(messageSpies.error).toHaveBeenCalledTimes(1)
    const text = String(messageSpies.error.mock.calls[0][0])
    expect(text).toContain('通知不存在或已被删除')
    expect(text).not.toContain('Request failed with status code')
    // 状态如实：行仍在，且失败后不触发刷新（仅挂载时一次加载）
    expect(wrapper.text()).toContain(NOTIFICATION.title)
    expect(apiMocks.getNotifications).toHaveBeenCalledTimes(1)
  })

  it('删除成功：行消失、列表刷新、成功提示一次', async () => {
    const wrapper = await mountView()
    apiMocks.getNotifications.mockResolvedValue(listPage([]))
    apiMocks.getNotificationSummary.mockResolvedValue({ data: { total: 0, unread_count: 0, by_type: {} } })

    await vmOf(wrapper).handleDelete(NOTIFICATION)
    await flushPromises()
    await nextTick()

    expect(messageSpies.success).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).not.toContain(NOTIFICATION.title)
    expect(apiMocks.getNotifications).toHaveBeenCalledTimes(2)
  })
})

describe('通知页 · D3 批量删除', () => {
  it('批删失败：选中状态保留（不得假装已删）、失败可见、不刷新', async () => {
    const wrapper = await mountView()
    vmOf(wrapper).selectedIds = [1]
    apiMocks.batchDeleteNotifications.mockRejectedValueOnce(
      axiosError(400, { detail: '部分通知已处理，请刷新后重试' }, 'Request failed with status code 400'),
    )

    await vmOf(wrapper).handleBatchDelete()
    await flushPromises()
    await nextTick()

    expect(messageSpies.error).toHaveBeenCalledTimes(1)
    const text = String(messageSpies.error.mock.calls[0][0])
    expect(text).toContain('部分通知已处理，请刷新后重试')
    expect(vmOf(wrapper).selectedIds, '失败时必须保留选中项').toEqual([1])
    expect(apiMocks.getNotifications).toHaveBeenCalledTimes(1)
  })

  it('批删成功：选择清空、列表刷新、成功提示一次', async () => {
    const wrapper = await mountView()
    vmOf(wrapper).selectedIds = [1]
    apiMocks.getNotifications.mockResolvedValue(listPage([]))

    await vmOf(wrapper).handleBatchDelete()
    await flushPromises()
    await nextTick()

    expect(vmOf(wrapper).selectedIds).toEqual([])
    expect(messageSpies.success).toHaveBeenCalledTimes(1)
    expect(apiMocks.getNotifications).toHaveBeenCalledTimes(2)
  })
})

describe('通知页 · D2 搜索失败清旧结果 + 重试', () => {
  it('筛选请求失败 → 清空旧结果、错误态展示后端 detail；重试成功恢复', async () => {
    const wrapper = await mountView()
    expect(wrapper.text()).toContain(NOTIFICATION.title)

    apiMocks.getNotifications.mockRejectedValueOnce(
      axiosError(400, { detail: '筛选参数非法' }, 'Request failed with status code 400'),
    )
    vmOf(wrapper).handleSearch()
    await flushPromises()
    await nextTick()

    expect(wrapper.text()).toContain(zh.notifications.loadFailed)
    expect(wrapper.text()).toContain('筛选参数非法')
    expect(wrapper.text()).not.toContain(NOTIFICATION.title)
    expect(vmOf(wrapper).items, '失败后通知列表状态必须清空').toEqual([])

    apiMocks.getNotifications.mockResolvedValue(listPage([NOTIFICATION]))
    const retry = wrapper.findAll('button').find(
      (b) => b.text().trim() === zh.common.retry,
    )
    expect(retry, '错误态应提供重试按钮').toBeTruthy()
    await retry!.trigger('click')
    await flushPromises()
    await nextTick()
    expect(wrapper.text()).toContain(NOTIFICATION.title)
  })
})

describe('通知页 · 汇总失败不阻塞列表', () => {
  it('summary 接口失败：列表仍加载，统计用占位符如实表示未知', async () => {
    apiMocks.getNotificationSummary.mockRejectedValueOnce(
      axiosError(500, {}, 'Request failed with status code 500'),
    )

    const wrapper = await mountView()

    expect(wrapper.text()).toContain(NOTIFICATION.title)
    expect(wrapper.text()).toContain('—')
    expect(messageSpies.error).not.toHaveBeenCalled()
  })
})
