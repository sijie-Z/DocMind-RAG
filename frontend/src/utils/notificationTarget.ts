/**
 * 通知点击的落点解析（布局铃铛与通知列表共用）。
 *
 * 后端 target_route 是路由名、target_id 是业务 id：知识库重建等通知必须
 * 直接落到对应文档页（/knowledge/:documentId），而不是只打开知识库列表。
 */
import type { RouteLocationRaw } from 'vue-router'

export type NotificationTargetLike = {
  target_route?: string | null
  target_id?: string | number | null
  type?: string | null
}

export function notificationTargetLocation(
  notification: NotificationTargetLike,
): RouteLocationRaw {
  const routeName = notification.target_route || undefined
  const targetId = notification.target_id

  if (routeName === 'Knowledge' && targetId) {
    return { name: 'KnowledgeDocument', params: { documentId: String(targetId) } }
  }
  if (routeName) {
    return {
      name: routeName,
      query: targetId ? { id: String(targetId) } : undefined,
    }
  }

  const type = notification.type || 'system'
  if (type === 'knowledge' || type === 'document') return { name: 'Knowledge' }
  if (type === 'chat') return { name: 'Chat' }
  if (type === 'account' || type === 'security') return { name: 'Profile' }
  if (type === 'agent') return { name: 'Agent' }
  return { name: 'Notifications' }
}
