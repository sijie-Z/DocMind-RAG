import request from '@/utils/request'
import type { AxiosResponse } from 'axios'

export interface Notification {
  id: number
  title: string
  content: string
  type: string
  is_read: boolean
  target_route?: string
  target_id?: string
  created_at: string
}

export interface NotificationListResponse {
  items: Notification[]
  total: number
  unread_count: number
  skip: number
  limit: number
}

export interface NotificationSummaryResponse {
  total: number
  unread_count: number
  by_type: Record<string, number>
}

/**
 * E14/D1：通知页自理错误的静默请求选项（页面唯一 toast / 页内错误态）。
 * 未要求静默的调用方（布局铃铛、通知 store）保持既有全局拦截器提示。
 */
export interface SilentRequestOptions {
  silent?: boolean
}

const SILENT_HEADERS = { headers: { 'X-Silent-Error': '1' } } as const

export const getNotifications = async (params?: {
  skip?: number
  limit?: number
  unread_only?: boolean
  type?: string
  q?: string
}, options?: SilentRequestOptions): Promise<AxiosResponse<NotificationListResponse>> => {
  return request.get('/notifications/', { params, ...(options?.silent ? SILENT_HEADERS : {}) })
}

export const getUnreadCount = async (): Promise<AxiosResponse<{ count: number }>> => {
  return request.get('/notifications/unread-count')
}

export const getNotificationSummary = async (
  options?: SilentRequestOptions
): Promise<AxiosResponse<NotificationSummaryResponse>> => {
  return request.get('/notifications/summary', options?.silent ? SILENT_HEADERS : undefined)
}

export const markAsRead = async (id: number): Promise<AxiosResponse<void>> => {
  return request.put(`/notifications/${id}/read`)
}

export const markAllAsRead = async (): Promise<AxiosResponse<void>> => {
  return request.put('/notifications/read-all')
}

export const deleteNotification = async (
  id: number,
  options?: SilentRequestOptions
): Promise<AxiosResponse<void>> => {
  return request.delete(`/notifications/${id}`, options?.silent ? SILENT_HEADERS : undefined)
}

export const batchDeleteNotifications = async (
  ids: number[],
  options?: SilentRequestOptions
): Promise<AxiosResponse<void>> => {
  return request.delete('/notifications/', {
    data: { ids },
    ...(options?.silent ? SILENT_HEADERS : {}),
  })
}
