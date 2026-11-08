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

  const recordError = (kind: 'badge' | 'list', error: unknown) => {
    const message = error instanceof Error ? error.message : String(error)
    if (kind === 'badge') badgeError.value = message
    else listError.value = message
    console.warn(`[notification] ${kind} load failed:`, error)
  }

  /** 首屏只拉未读数；并发调用共享在途请求。 */
  const bootstrapBadge = (): Promise<void> => {
    if (badgePromise) return badgePromise
    badgePromise = (async () => {
      try {
        const countRes = await getUnreadCount()
        unreadCount.value = countRes.data?.count || 0
        badgeError.value = null
        loadedOnce.value = true
      } catch (error) {
        recordError('badge', error)
      } finally {
        badgePromise = null
      }
    })()
    return badgePromise
  }

  /** 打开弹层时加载最近 8 条；并发/重复打开共享在途请求，不重复拉取。 */
  const loadHeaderItems = (): Promise<void> => {
    if (headerPromise) return headerPromise
    headerLoading.value = true
    headerPromise = (async () => {
      try {
        const listRes = await getNotifications({ limit: 8 })
        headerItems.value = listRes.data?.items || []
        headerLoaded.value = true
        listError.value = null
      } catch (error) {
        recordError('list', error)
      } finally {
        headerLoading.value = false
        headerPromise = null
      }
    })()
    return headerPromise
  }

  /** 重试/标记全部已读/删除后同时刷新未读数与列表。 */
  const refresh = async () => {
    await Promise.all([bootstrapBadge(), loadHeaderItems()])
  }

  const applyRealtime = (payload: RealtimeNotificationPayload) => {
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
