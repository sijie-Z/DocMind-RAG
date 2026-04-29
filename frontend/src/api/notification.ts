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

export const getNotifications = async (params?: {
  skip?: number
  limit?: number
  unread_only?: boolean
  type?: string
  q?: string
}): Promise<AxiosResponse<NotificationListResponse>> => {
  return request.get('/notifications/', { params })
}

export const getUnreadCount = async (): Promise<AxiosResponse<{ count: number }>> => {
  return request.get('/notifications/unread-count')
}

export const getNotificationSummary = async (): Promise<AxiosResponse<NotificationSummaryResponse>> => {
  return request.get('/notifications/summary')
}

export const markAsRead = async (id: number): Promise<AxiosResponse<any>> => {
  return request.put(`/notifications/${id}/read`)
}

export const markAllAsRead = async (): Promise<AxiosResponse<any>> => {
  return request.put('/notifications/read-all')
}

export const deleteNotification = async (id: number): Promise<AxiosResponse<any>> => {
  return request.delete(`/notifications/${id}`)
}

export const batchDeleteNotifications = async (ids: number[]): Promise<AxiosResponse<any>> => {
  return request.delete('/notifications/', { data: { ids } })
}
