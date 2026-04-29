import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getNotifications, getUnreadCount, getNotificationSummary, type Notification, type NotificationSummaryResponse } from '@/api/notification'
import type { RealtimeNotificationPayload } from '@/utils/notificationSocket'

export const useNotificationStore = defineStore('notification', () => {
  const headerItems = ref<Notification[]>([])
  const unreadCount = ref(0)
  const summary = ref<NotificationSummaryResponse>({ total: 0, unread_count: 0, by_type: {} })
  const latestRealtimeId = ref<number | null>(null)

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
    } catch (error) {
      headerItems.value = []
      unreadCount.value = 0
      summary.value = { total: 0, unread_count: 0, by_type: {} }
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
    if (!existed) {
      summary.value.total += 1
      if (!normalized.is_read) {
        unreadCount.value += 1
        summary.value.unread_count += 1
      }
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
    bootstrap,
    applyRealtime,
    consumeRead,
  }
})
