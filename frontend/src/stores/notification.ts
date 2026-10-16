import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getNotifications, getUnreadCount, getNotificationSummary, type Notification, type NotificationSummaryResponse } from '@/api/notification'
import type { RealtimeNotificationPayload } from '@/utils/notificationSocket'

export const useNotificationStore = defineStore('notification', () => {
  const headerItems = ref<Notification[]>([])
  const unreadCount = ref(0)
  const summary = ref<NotificationSummaryResponse>({ total: 0, unread_count: 0, by_type: {} })
  const latestRealtimeId = ref<number | null>(null)
  // 错误态（第二轮 §7.2③）：失败可判别 + 是否至少成功加载过一次
  const lastError = ref<string | null>(null)
  const loadedOnce = ref(false)

  const bootstrap = async () => {
    try {
      const [listRes, countRes, summaryRes] = await Promise.all([
        getNotifications({ limit: 8 }),
        getUnreadCount(),
        getNotificationSummary()
      ])

      headerItems.value = listRes.data?.items || []
      unreadCount.value = countRes.data?.count || 0
      if (summaryRes.data) {
        summary.value = summaryRes.data
      }
      // 只在成功时清错误标记（对照 notificationSocket.ts：状态只在成功路径复位）
      lastError.value = null
      loadedOnce.value = true
    } catch (error) {
      // 失败保留上次成功值，不置空——否则失败与"真没通知"不可区分（铃铛假装"没有通知"）
      lastError.value = error instanceof Error ? error.message : String(error)
      console.warn('[notification] bootstrap failed:', error)
    }
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
    summary.value.total += 1
    if (!normalized.is_read) {
      unreadCount.value += 1
      summary.value.unread_count += 1
    }
  }

  const consumeRead = (id: number) => {
    const notification = headerItems.value.find((n) => n.id === id)
    if (!notification || notification.is_read) return
    headerItems.value = headerItems.value.map((n) => (n.id === id ? { ...n, is_read: true } : n))
    unreadCount.value = Math.max(0, unreadCount.value - 1)
    summary.value.unread_count = Math.max(0, summary.value.unread_count - 1)
  }

  return {
    headerItems,
    unreadCount,
    summary,
    latestRealtimeId,
    lastError,
    loadedOnce,
    bootstrap,
    applyRealtime,
    consumeRead,
  }
})
