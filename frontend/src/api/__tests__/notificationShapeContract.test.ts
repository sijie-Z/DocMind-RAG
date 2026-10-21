/**
 * notifications 响应形状契约（T11 · 方案 B）。
 *
 * 钉住事实：GET /notifications/、/notifications/unread-count、/notifications/summary
 * 及写操作回执均为「裸结构」（响应体即业务体，无 success/data 信封）。
 * 取样方式：backend/app/api/v1/endpoints/notifications.py 源码逐字——
 * L171-177 列表 / L241 unread-count / L265-269 summary / L299,316,333,343,352 回执
 * （2026-10-15 复核）；并与前端既有 mock 对齐（stores/__tests__/notification.test.ts、
 * views/notifications/__tests__/realtimeWiring.test.ts 均 mock { data: { items: [...] } }）。
 *
 * 兼容面：apiUnwrap 对这些形状必须原样透传（不误剥）；此测同时是 apiUnwrap 的
 * 形状护栏——若后端改吐信封或 apiUnwrap 规则变化导致误剥，此处即红。
 */
import { describe, it, expect } from 'vitest'
import { apiUnwrap } from '@/utils/apiUnwrap'
import { getNotifications, getUnreadCount, getNotificationSummary } from '@/api/notification'

/** 真机响应对象的最小化身（axios 响应挂 status/statusText/headers）。 */
const respOf = (body: unknown) => ({ data: body, status: 200, statusText: 'OK', headers: {}, config: {} })

// 形状逐字对齐 notifications.py:171-177（target_route/target_id 可空可缺，
// 前端声明为可选字段，样例从略）
const REAL_LIST = {
  items: [
    { id: 1, title: '欢迎', content: '示例内容', type: 'system', is_read: false, created_at: '2026-10-15T00:00:00' },
  ],
  total: 1,
  unread_count: 1,
  skip: 0,
  limit: 20,
}

describe('notifications 响应形状契约 · 裸结构（形态①）', () => {
  it('模块导出面 + 类型锚点：getNotifications 声明即列表体本身（编译期），样例核心字段核对（运行期）', () => {
    expect([getNotifications, getUnreadCount, getNotificationSummary].every((fn) => typeof fn === 'function')).toBe(true)
    const pin: Awaited<ReturnType<typeof getNotifications>>['data'] = REAL_LIST
    expect(pin.items[0].id).toBe(1)
    expect(pin.total).toBe(1)
    expect(pin.unread_count).toBe(1)
  })

  it('列表：apiUnwrap 原样透传（同一引用，不误剥）', () => {
    expect(apiUnwrap(respOf(REAL_LIST))).toBe(REAL_LIST)
  })

  it('unread-count：{count: n} 裸结构原样', () => {
    const pin: Awaited<ReturnType<typeof getUnreadCount>>['data'] = { count: 3 }
    expect(apiUnwrap(respOf(pin))).toBe(pin)
  })

  it('summary：{total, unread_count, by_type} 裸结构原样', () => {
    const pin: Awaited<ReturnType<typeof getNotificationSummary>>['data'] = {
      total: 5,
      unread_count: 2,
      by_type: { system: 3, task: 2 },
    }
    expect(apiUnwrap(respOf(pin))).toBe(pin)
  })

  it('写操作回执：message-only（含 updated/deleted 附加键）原样，不误剥', () => {
    const acks = [
      { message: '标记成功' },
      { message: '全部已读', updated: 2 },
      { message: '删除成功' },
      { message: '批量删除成功', deleted: 2 },
      { message: '没有可删除的通知', deleted: 0 },
    ]
    for (const ack of acks) {
      expect(apiUnwrap(respOf(ack))).toBe(ack)
    }
  })

  it('宽容面：若后端未来加一层 {data} 信封，apiUnwrap 仍解回同一业务体', () => {
    expect(apiUnwrap(respOf({ data: REAL_LIST }))).toBe(REAL_LIST)
  })
})
