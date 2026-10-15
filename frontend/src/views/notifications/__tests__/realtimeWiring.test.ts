/**
 * 通知页实时订阅接线（F4 判别）：卸载只移除自己的处理器。
 *
 * 背景：通知页 onUnmounted 曾裸调 `notificationSocket.offNotification()`
 * ——该方法无参时**清空全部**处理器，离开通知页后 layout/铃铛的实时推送
 * 静默失效。修复后必须以**自身处理器为参**移除；本用例钉住该调用形状。
 *
 * 变异点：还原裸调 offNotification() → 本用例红（收到的参数非视图处理器）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'

import NotificationsView from '../index.vue'
import { notificationSocket } from '@/utils/notificationSocket'

const apiMocks = vi.hoisted(() => ({
  getNotifications: vi.fn().mockResolvedValue({ data: { items: [], total: 0 } }),
  getNotificationSummary: vi.fn().mockResolvedValue({
    data: { total: 0, unread_count: 0, by_type: {} },
  }),
  markAllAsRead: vi.fn().mockResolvedValue({}),
  markAsRead: vi.fn().mockResolvedValue({}),
  deleteNotification: vi.fn().mockResolvedValue({}),
  batchDeleteNotifications: vi.fn().mockResolvedValue({}),
}))

vi.mock('@/api/notification', () => apiMocks)

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'Home', component: { template: '<div />' } },
      { path: '/notifications', name: 'Notifications', component: { template: '<div />' } },
    ],
  })
}

describe('通知页实时订阅接线', () => {
  beforeEach(() => vi.clearAllMocks())
  afterEach(() => vi.restoreAllMocks())

  it('卸载只移除自己的处理器，不清空全部（F4 判别）', async () => {
    const onSpy = vi.spyOn(notificationSocket, 'onNotification')
    const offSpy = vi.spyOn(notificationSocket, 'offNotification')
    const router = makeRouter()
    await router.push('/notifications')

    const wrapper = mount(NotificationsView, { global: { plugins: [router] } })
    await flushPromises()

    expect(onSpy).toHaveBeenCalledTimes(1)
    const viewHandler = onSpy.mock.calls[0][0]

    wrapper.unmount()

    expect(offSpy).toHaveBeenCalledTimes(1)
    expect(
      offSpy.mock.calls[0][0],
      '必须以自身处理器为参移除——裸调 offNotification() 会清空全部处理器，' +
        '留下页面后铃铛/layout 的实时推送会静默失效',
    ).toBe(viewHandler)
  })
})
