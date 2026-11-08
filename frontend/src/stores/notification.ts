import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { getNotifications, getUnreadCount, type Notification } from '@/api/notification'
import type { RealtimeNotificationPayload } from '@/utils/notificationSocket'

export const useNotificationStore = defineStore('notification', () => {
  const headerItems = ref<Notification[]>([])
  const unreadCount = ref(0)
  const latestRealtimeId = ref<number | null>(null)
  // 两路错误分开维护：count 成功不得清掉列表错误，反之亦然。
  const badgeError = ref<string | null>(null)
  const listError = ref<string | null>(null)
  const lastError = computed(() => badgeError.value || listError.value)
  // 是否至少成功加载过一次未读数（实时推送不从 0 起跳的基线）
  const loadedOnce = ref(false)
  // 铃铛弹层列表按需加载：首屏只拉未读数（badge），打开弹层再拉列表。
  const headerLoaded = ref(false)
  const headerLoading = ref(false)
  let badgePromise: Promise<void> | null = null
  let headerPromise: Promise<void> | null = null
  // 状态世代：标记全部已读/删除/实时推送/强制刷新会递增。在途请求按发起时的
  // 世代决定能否写回——迟到的旧响应不得覆盖更新的本地状态或伪造未读回弹。
  let stateEpoch = 0

  /** 丢弃所有在途响应并解除在途引用（下一次调用会重新发请求）。 */
  const invalidate = () => {
    stateEpoch += 1
    badgePromise = null
    headerPromise = null
  }

  const recordError = (kind: 'badge' | 'list', error: unknown) => {
    const message = error instanceof Error ? error.message : String(error)
    if (kind === 'badge') badgeError.value = message
    else listError.value = message
    console.warn(`[notification] ${kind} load failed:`, error)
  }

  /** 首屏只拉未读数；并发调用共享在途请求。force=true 丢弃在途后重取。 */
  const bootstrapBadge = (options: { force?: boolean } = {}): Promise<void> => {
    if (options.force) invalidate()
    if (badgePromise) return badgePromise
    const epoch = stateEpoch
    const request = (async () => {
      try {
        const countRes = await getUnreadCount()
        // 期间发生过已读/删除/推送/身份变化：旧快照不得写回。
        if (epoch !== stateEpoch) return
        unreadCount.value = countRes.data?.count || 0
        badgeError.value = null
        loadedOnce.value = true
      } catch (error) {
        if (epoch === stateEpoch) recordError('badge', error)
      } finally {
        // 只清自己：旧请求的 finally 不得把新请求的引用置空。
        if (badgePromise === request) badgePromise = null
      }
    })()
    badgePromise = request
    return request
  }

  /** 打开弹层时加载最近 8 条；并发/重复打开共享在途请求，不重复拉取。 */
  const loadHeaderItems = (options: { force?: boolean } = {}): Promise<void> => {
    if (options.force) invalidate()
    if (headerPromise) return headerPromise
    const epoch = stateEpoch
    headerLoading.value = true
    const request = (async () => {
      try {
        const listRes = await getNotifications({ limit: 8 })
        if (epoch !== stateEpoch) return
        headerItems.value = listRes.data?.items || []
        headerLoaded.value = true
        listError.value = null
      } catch (error) {
        if (epoch === stateEpoch) recordError('list', error)
      } finally {
        if (headerPromise === request) {
          headerLoading.value = false
          headerPromise = null
        }
      }
    })()
    headerPromise = request
    return request
  }

  /**
   * 重试/标记全部已读/删除后同时刷新未读数与列表。
   * 变更后的调用必须传 force=true：复用变更前的在途请求会让 PUT 结果被旧快照
   * 覆盖（徽标回弹、已删行复活）。
   */
  const refresh = async (options: { force?: boolean } = {}): Promise<void> => {
    if (options.force) invalidate()
    await Promise.all([bootstrapBadge(), loadHeaderItems()])
  }

  const applyRealtime = (payload: RealtimeNotificationPayload) => {
    // 推送代表比在途 GET 更新的状态：丢弃旧快照，避免新通知被列表快照覆盖、
    // ++ 计数被旧未读数回写。
    invalidate()
    const existed = headerItems.value.some((n) => n.id === payload.id)
    const normalized: Notification = {
      id: payload.id,
      title: payload.title,
      content: payload.content,
      type: payload.type,
      is_read: payload.is_read,
      created_at: payload.created_at,
      target_route: payload.target_route,
      target_id: payload.target_id,
    }

    headerItems.value = [normalized, ...headerItems.value.filter((n) => n.id !== normalized.id)].slice(0, 8)
    latestRealtimeId.value = payload.id
    if (existed) return
    // 未成功加载过（无有效基线）时实时推送不加计：否则未读数会"从 0 起跳"伪造
    // （失败置空后的持续失真放大 bug）；等重试成功后以服务端真值覆盖
    if (!loadedOnce.value) return
    if (!normalized.is_read) {
      unreadCount.value += 1
    }
  }

  const consumeRead = (id: number) => {
    const notification = headerItems.value.find((n) => n.id === id)
    if (!notification || notification.is_read) return
    // 已读是比在途未读快照更新的状态：丢弃旧响应，防止计数回弹。
    invalidate()
    headerItems.value = headerItems.value.map((n) => (n.id === id ? { ...n, is_read: true } : n))
    unreadCount.value = Math.max(0, unreadCount.value - 1)
  }

  return {
    headerItems,
    unreadCount,
    latestRealtimeId,
    lastError,
    loadedOnce,
    headerLoaded,
    headerLoading,
    bootstrapBadge,
    loadHeaderItems,
    refresh,
    applyRealtime,
    consumeRead,
  }
})
