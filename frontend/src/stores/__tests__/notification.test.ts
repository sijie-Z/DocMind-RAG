/**
 * T1 判别测试 · 通知 store 错误态 + 实时计数基线（第二轮清单 §7.2③）
 *
 * 现场：
 *  ① `stores/notification.ts` bootstrap 三接口任一失败 → catch 把
 *     headerItems/unreadCount/summary 全量置空，失败与"真没通知"在 UI 上不可区分
 *     （铃铛假装"没有通知"）；
 *  ② 置空后 `applyRealtime` 的加计从 0 起跳：实时推送每来一条 +1，
 *     未读数变成"失败后收到的推送条数"，持续失真。
 *     对照样板 `notificationSocket.ts`：内部计数只在成功时重置，失败保留状态并重试。
 *
 * 修法约定（本测试钉死）：
 *  - catch 不置空：保留上次成功值；lastError 记录错误；loadedOnce 标记"是否至少成功过一次"；
 *    catch 内补 console.warn；
 *  - 从未成功加载过（无有效基线）时，实时推送不伪造计数（不出现"从 0 起跳"的 +1），
 *    但推送条目照常入列表（真实数据不丢）；成功加载后按保留值加计；
 *  - 重试成功后 lastError 清除、以服务端真值覆盖。
 *
 * 变异（预计红）：
 *  - catch 恢复置空 → 用例 1/2/3 红；
 *  - applyRealtime 去掉 loadedOnce 闸门（从 0 起跳加计）→ 用例 2 红；
 *  - 成功路径不更新 loadedOnce/不清 lastError → 用例 2/4 红。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('@/api/notification', () => ({
  getNotifications: vi.fn(),
  getUnreadCount: vi.fn(),
  getNotificationSummary: vi.fn(),
}))

import { useNotificationStore } from '@/stores/notification'
import { getNotifications, getUnreadCount, getNotificationSummary } from '@/api/notification'

const mockedList = vi.mocked(getNotifications)
const mockedCount = vi.mocked(getUnreadCount)
const mockedSummary = vi.mocked(getNotificationSummary)

const notif = (id: number, isRead = false) => ({
  id,
  title: `标题${id}`,
  content: `内容${id}`,
  type: 'system',
  is_read: isRead,
  created_at: '2026-10-14T00:00:00Z',
})

const realtimePayload = (id: number) => ({
  id,
  title: `实时${id}`,
  content: `实时内容${id}`,
  type: 'system',
  is_read: false,
  created_at: '2026-10-14T01:00:00Z',
})

/** 让三接口进入"上次成功"状态：unread=5，列表两条，summary 一致 */
const seedSuccess = () => {
  mockedList.mockResolvedValue({
    data: { items: [notif(2), notif(1)], total: 5, unread_count: 5, skip: 0, limit: 8 },
  } as any)
  mockedCount.mockResolvedValue({ data: { count: 5 } } as any)
  mockedSummary.mockResolvedValue({
    data: { total: 5, unread_count: 5, by_type: { system: 5 } },
  } as any)
}

/** 三接口全部拒答（断网/接口挂） */
const rejectAll = () => {
  mockedList.mockRejectedValue(new Error('Network Error'))
  mockedCount.mockRejectedValue(new Error('Network Error'))
  mockedSummary.mockRejectedValue(new Error('Network Error'))
}

let warnSpy: ReturnType<typeof vi.spyOn>

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
})

afterEach(() => {
  warnSpy.mockRestore()
})

describe('T1 · 通知 store 错误态', () => {
  it('1. 加载失败保留上次成功值，不置空；lastError/loadedOnce 就位；catch 有 console.warn', async () => {
    const store = useNotificationStore()
    seedSuccess()
    await store.bootstrap()
    expect(store.loadedOnce).toBe(true)
    expect(store.unreadCount).toBe(5)

    rejectAll()
    await store.bootstrap()

    // 旧值保留（旧实现此处全被置空 → 红）
    expect(store.headerItems.map((n) => n.id)).toEqual([2, 1])
    expect(store.unreadCount).toBe(5)
    expect(store.summary).toEqual({ total: 5, unread_count: 5, by_type: { system: 5 } })
    expect(store.loadedOnce).toBe(true)
    // 错误态可判别 + 观测量留痕
    expect(store.lastError).toBeTruthy()
    expect(warnSpy).toHaveBeenCalled()
  })

  it('2. 首次加载即失败：无有效基线，实时推送不从 0 起跳伪造计数（条目照常入列表）', async () => {
    const store = useNotificationStore()
    rejectAll()
    await store.bootstrap()

    expect(store.loadedOnce).toBe(false)
    expect(store.lastError).toBeTruthy()
    expect(store.unreadCount).toBe(0)

    store.applyRealtime(realtimePayload(100))

    // 旧实现此处 unreadCount 变 1、summary.total 变 1（从 0 起跳 → 红）
    expect(store.unreadCount).toBe(0)
    expect(store.summary.total).toBe(0)
    expect(store.summary.unread_count).toBe(0)
    // 真实推送不丢：条目入列表、latestRealtimeId 记录
    expect(store.headerItems.some((n) => n.id === 100)).toBe(true)
    expect(store.latestRealtimeId).toBe(100)
  })

  it('3. 成功→刷新失败→实时推送：基于保留值加计（5→6），不从 0 起跳（旧实现为 1）', async () => {
    const store = useNotificationStore()
    seedSuccess()
    await store.bootstrap()

    rejectAll()
    await store.bootstrap()
    expect(store.unreadCount).toBe(5)

    store.applyRealtime(realtimePayload(9))

    expect(store.unreadCount).toBe(6)
    expect(store.summary.total).toBe(6)
    expect(store.summary.unread_count).toBe(6)
    expect(store.headerItems[0].id).toBe(9)
    expect(store.headerItems).toHaveLength(3)
  })

  it('4. 重试成功后 lastError 清除，以服务端真值覆盖', async () => {
    const store = useNotificationStore()
    seedSuccess()
    await store.bootstrap()

    rejectAll()
    await store.bootstrap()
    expect(store.lastError).toBeTruthy()

    mockedList.mockResolvedValue({
      data: { items: [notif(7), notif(6)], total: 7, unread_count: 7, skip: 0, limit: 8 },
    } as any)
    mockedCount.mockResolvedValue({ data: { count: 7 } } as any)
    mockedSummary.mockResolvedValue({
      data: { total: 7, unread_count: 7, by_type: { system: 7 } },
    } as any)
    await store.bootstrap()

    expect(store.lastError).toBe(null)
    expect(store.unreadCount).toBe(7)
    expect(store.summary.unread_count).toBe(7)
    expect(store.headerItems.map((n) => n.id)).toEqual([7, 6])
  })

  it('5. 同 id 重复实时推送不重复加计（既有去重语义不被破坏）', async () => {
    const store = useNotificationStore()
    seedSuccess()
    await store.bootstrap()

    store.applyRealtime(realtimePayload(2)) // 已在列表中的 id
    expect(store.unreadCount).toBe(5)
    expect(store.summary.total).toBe(5)
    expect(store.headerItems).toHaveLength(2)
  })
})
