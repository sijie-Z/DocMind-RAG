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
 *  - 重试成功后 lastError 清除、以服务端真值覆盖；
 *  - 首屏只拉未读数（bootstrapBadge）；列表在打开弹层时按需加载（loadHeaderItems），
 *    不再请求无人消费的 summary。
 *
 * 变异（预计红）：
 *  - catch 恢复置空 → 用例 1/2/3 红；
 *  - applyRealtime 去掉 loadedOnce 闸门（从 0 起跳加计）→ 用例 2 红；
 *  - 成功路径不更新 loadedOnce/不清 lastError → 用例 2/4 红；
 *  - bootstrapBadge 顺手拉列表 → 用例 6 红。
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

/** 让未读数与列表进入"上次成功"状态：unread=5，列表两条 */
const seedSuccess = () => {
  mockedList.mockResolvedValue({
    data: { items: [notif(2), notif(1)], total: 5, unread_count: 5, skip: 0, limit: 8 },
  } as any)
  mockedCount.mockResolvedValue({ data: { count: 5 } } as any)
}

/** 两接口全部拒答（断网/接口挂） */
const rejectAll = () => {
  mockedList.mockRejectedValue(new Error('Network Error'))
  mockedCount.mockRejectedValue(new Error('Network Error'))
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
    await store.bootstrapBadge()
    await store.loadHeaderItems()
    expect(store.loadedOnce).toBe(true)
    expect(store.unreadCount).toBe(5)

    rejectAll()
    await store.bootstrapBadge()
    await store.loadHeaderItems()

    // 旧值保留（旧实现此处全被置空 → 红）
    expect(store.headerItems.map((n) => n.id)).toEqual([2, 1])
    expect(store.unreadCount).toBe(5)
    expect(store.loadedOnce).toBe(true)
    // 错误态可判别 + 观测量留痕
    expect(store.lastError).toBeTruthy()
    expect(warnSpy).toHaveBeenCalled()
  })

  it('2. 首次加载即失败：无有效基线，实时推送不从 0 起跳伪造计数（条目照常入列表）', async () => {
    const store = useNotificationStore()
    rejectAll()
    await store.bootstrapBadge()
    await store.loadHeaderItems()

    expect(store.loadedOnce).toBe(false)
    expect(store.lastError).toBeTruthy()
    expect(store.unreadCount).toBe(0)

    store.applyRealtime(realtimePayload(100))

    // 旧实现此处 unreadCount 变 1（从 0 起跳 → 红）
    expect(store.unreadCount).toBe(0)
    // 真实推送不丢：条目入列表、latestRealtimeId 记录
    expect(store.headerItems.some((n) => n.id === 100)).toBe(true)
    expect(store.latestRealtimeId).toBe(100)
  })

  it('3. 成功→刷新失败→实时推送：基于保留值加计（5→6），不从 0 起跳（旧实现为 1）', async () => {
    const store = useNotificationStore()
    seedSuccess()
    await store.bootstrapBadge()
    await store.loadHeaderItems()

    rejectAll()
    await store.bootstrapBadge()
    await store.loadHeaderItems()
    expect(store.unreadCount).toBe(5)

    store.applyRealtime(realtimePayload(9))

    expect(store.unreadCount).toBe(6)
    expect(store.headerItems[0].id).toBe(9)
    expect(store.headerItems).toHaveLength(3)
  })

  it('4. 重试成功后 lastError 清除，以服务端真值覆盖', async () => {
    const store = useNotificationStore()
    seedSuccess()
    await store.bootstrapBadge()
    await store.loadHeaderItems()

    rejectAll()
    await store.bootstrapBadge()
    await store.loadHeaderItems()
    expect(store.lastError).toBeTruthy()

    mockedList.mockResolvedValue({
      data: { items: [notif(7), notif(6)], total: 7, unread_count: 7, skip: 0, limit: 8 },
    } as any)
    mockedCount.mockResolvedValue({ data: { count: 7 } } as any)
    await store.refresh()

    expect(store.lastError).toBe(null)
    expect(store.unreadCount).toBe(7)
    expect(store.headerItems.map((n) => n.id)).toEqual([7, 6])
  })

  it('5. 同 id 重复实时推送不重复加计（既有去重语义不被破坏）', async () => {
    const store = useNotificationStore()
    seedSuccess()
    await store.bootstrapBadge()
    await store.loadHeaderItems()

    store.applyRealtime(realtimePayload(2)) // 已在列表中的 id
    expect(store.unreadCount).toBe(5)
    expect(store.headerItems).toHaveLength(2)
  })

  it('6. 首屏只拉未读数；列表仅在 loadHeaderItems 时请求（不再拉 summary）', async () => {
    const store = useNotificationStore()
    seedSuccess()
    await store.bootstrapBadge()

    expect(mockedCount).toHaveBeenCalledTimes(1)
    expect(mockedList).not.toHaveBeenCalled()
    expect(store.headerLoaded).toBe(false)

    await store.loadHeaderItems()
    expect(mockedList).toHaveBeenCalledTimes(1)
    expect(store.headerLoaded).toBe(true)
    expect(store.headerLoading).toBe(false)
    expect(mockedSummary).not.toHaveBeenCalled()
  })

  it('7. 未读数失败但列表成功：错误不被列表成功清掉', async () => {
    const store = useNotificationStore()
    mockedCount.mockRejectedValue(new Error('count down'))
    mockedList.mockResolvedValue({
      data: { items: [notif(1)], total: 1, unread_count: 1, skip: 0, limit: 8 },
    } as any)

    await store.refresh()

    expect(store.unreadCount).toBe(0)
    expect(store.headerLoaded).toBe(true)
    expect(store.lastError).toBeTruthy()
  })

  it('8. 列表失败但未读数成功：错误不被计数成功清掉', async () => {
    const store = useNotificationStore()
    mockedCount.mockResolvedValue({ data: { count: 3 } } as any)
    mockedList.mockRejectedValue(new Error('list down'))

    await store.refresh()

    expect(store.unreadCount).toBe(3)
    expect(store.headerLoaded).toBe(false)
    expect(store.lastError).toBeTruthy()
  })

  it('9. 并发 bootstrapBadge 共享在途请求，不重复打 unread-count', async () => {
    const store = useNotificationStore()
    let resolveCount: (value: unknown) => void = () => {}
    mockedCount.mockReturnValue(
      new Promise((resolve) => {
        resolveCount = resolve
      }) as any
    )

    const first = store.bootstrapBadge()
    const second = store.bootstrapBadge()
    expect(mockedCount).toHaveBeenCalledTimes(1)

    resolveCount({ data: { count: 3 } })
    await Promise.all([first, second])
    expect(store.unreadCount).toBe(3)
  })
})
