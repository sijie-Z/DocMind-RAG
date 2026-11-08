import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getNotifications, getUnreadCount, type Notification } from '@/api/notification'
import type { RealtimeNotificationPayload } from '@/utils/notificationSocket'

export const useNotificationStore = defineStore('notification', () => {
  const headerItems = ref<Notification[]>([])
  const unreadCount = ref(0)
  const latestRealtimeId = ref<number | null>(null)
  // 错误态（第二轮 §7.2③）：失败可判别 + 是否至少成功加载过一次
  const lastError = ref<string | null>(null)
  const loadedOnce = ref(false)
  // 铃铛弹层列表按需加载：首屏只拉未读数（badge），打开弹层再拉列表。
  const headerLoaded = ref(false)
  const headerLoading = ref(false)

  const recordError = (error: unknown) => {
    // 失败保留上次成功值，不置空——否则失败与"真没通知"不可区分（铃铛假装"没有通知"）
    lastError.value = error instanceof Error ? error.message : String(error)
    console.warn('[notification] load failed:', error)
  }

  /** 首屏只拉未读数；列表与 summary 不再阻塞仪表盘。 */
  const bootstrapBadge = async () => {
    try {
      const countRes = await getUnreadCount()
      unreadCount.value = countRes.data?.count || 0
      lastError.value = null
      loadedOnce.value = true
    } catch (error) {
      recordError(error)
    }
  }

  /** 打开弹层时加载最近 8 条；已加载过则不重复拉取（实时推送会维护列表）。 */
  const loadHeaderItems = async () => {
    if (headerLoading.value) return
    headerLoading.value = true
    try {
      const listRes = await getNotifications({ limit: 8 })
      headerItems.value = listRes.data?.items || []
      headerLoaded.value = true
      lastError.value = null
    } catch (error) {
      recordError(error)
    } finally {
      headerLoading.value = false
    }
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
