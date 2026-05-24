import request from '@/utils/request'
import type { AxiosResponse } from 'axios'
import type { ApiResponse } from '@/types/common'

export interface User {
  id: number
  username: string
  email: string
  nickname: string
  full_name?: string
  phone?: string
  avatar?: string
  avatar_url?: string
  role: 'admin' | 'user'
  status: 'active' | 'inactive'
  organization_ids: number[]
  organizations?: Array<{ id: number; name: string; color: string }>
  last_login_at?: string
  created_at: string
  updated_at: string
  remark?: string
}

export interface UserListResponse {
  data: User[]
  total: number
  page: number
  page_size: number
}

export interface UserProfileResponse extends User {
  bio?: string
  preferences?: Record<string, unknown>
  api_key?: string
}

export interface AuditLogEntry {
  id: number
  user_id: number
  username?: string
  action: string
  target_type?: string
  target_id?: string
  details?: string
  ip_address?: string
  created_at: string
}

export const getUsers = async (params?: {
  page?: number
  page_size?: number
  search?: string
  role?: string
  status?: string
}): Promise<AxiosResponse<ApiResponse<UserListResponse>>> => {
  return request.get('/users/', { params })
}

export const getUser = async (id: number): Promise<AxiosResponse<User>> => {
  return request.get(`/users/${id}`)
}

export const getUserProfile = async (): Promise<AxiosResponse<UserProfileResponse>> => {
  return request.get('/users/me')
}

export const getAuditLogs = async (params?: {
  skip?: number
  limit?: number
  user_id?: number
  action?: string
  search?: string
  start_date?: string
  end_date?: string
}): Promise<AxiosResponse<ApiResponse<{ items: AuditLogEntry[]; total: number }>>> => {
  return request.get('/users/audit-logs', { params })
}

export const createUser = async (data: {
  username: string
  email: string
  nickname: string
  phone?: string
  role: 'admin' | 'user'
  status: 'active' | 'inactive'
  organization_ids?: number[]
  remark?: string
}): Promise<AxiosResponse<{ data: User }>> => {
  return request.post('/users/create', data)
}

export const updateUser = async (id: number, data: {
  email?: string
  nickname?: string
  phone?: string
  role?: 'admin' | 'user'
  status?: 'active' | 'inactive'
  organization_ids?: number[]
  remark?: string
}): Promise<AxiosResponse<{ data: User }>> => {
  return request.put(`/users/${id}`, data)
}

export interface UserStats {
  conversation_count: number
  message_count: number
  file_count: number
  knowledge_count: number
  storage_used: number
  storage_limit?: number
  activity_trend?: number[]
}

export interface Activity {
  id: string
  type: 'login' | 'upload' | 'chat'
  content: string
  time: string
}

export interface UserSession {
  id: number
  device_name?: string
  ip_address?: string
  user_agent?: string
  is_active: boolean
  last_seen_at?: string
  created_at?: string
}

export interface UserAuditLog {
  id: number
  action: string
  target_type?: string
  target_id?: string
  detail?: string
  ip_address?: string
  created_at?: string
}

export const getUserStats = async (): Promise<AxiosResponse<UserStats>> => {
  return request.get('/users/stats')
}

export const getUserActivities = async (): Promise<AxiosResponse<Activity[]>> => {
  return request.get('/users/activities')
}

export const getMySessions = async (): Promise<AxiosResponse<UserSession[]>> => {
  return request.get('/users/me/sessions')
}

export const revokeMySession = async (sessionId: number): Promise<AxiosResponse<{ message: string }>> => {
  return request.delete(`/users/me/sessions/${sessionId}`)
}

export const getMyActivityLogs = async (): Promise<AxiosResponse<UserAuditLog[]>> => {
  return request.get('/users/me/activity-logs')
}

export const uploadAvatar = async (file: File): Promise<AxiosResponse<{ url: string }>> => {
  const formData = new FormData()
  formData.append('file', file)
  return request.post('/files/upload/avatar', formData, {
    headers: {
      'Content-Type': undefined
    }
  })
}

export const updateUserProfile = async (data: {
  nickname?: string
  phone?: string
  avatar?: string
  email?: string
  bio?: string
  preferences?: Record<string, unknown>
}): Promise<AxiosResponse<{ data: User }>> => {
  const payload = {
    full_name: data.nickname,
    phone: data.phone,
    avatar_url: data.avatar,
    email: data.email,
    bio: data.bio,
    preferences: data.preferences
  }
  return request.put('/users/me', payload)
}

export const updatePassword = async (data: {
  old_password?: string
  new_password?: string
  current_password?: string
}) => {
  return request.put('/users/password', {
    old_password: data.old_password || data.current_password,
    new_password: data.new_password
  })
}

export const deleteUser = async (id: number): Promise<AxiosResponse<ApiResponse<unknown>>> => {
  return request.delete(`/users/${id}`)
}

export const resetUserPassword = async (id: number, password: string): Promise<AxiosResponse<ApiResponse<unknown>>> => {
  return request.put(`/users/${id}/password`, {
    new_password: password
  })
}

export const generateApiKey = async (): Promise<AxiosResponse<{ api_key: string }>> => {
  return request.post('/users/api-key')
}

export const revokeApiKey = async (): Promise<AxiosResponse<{ message: string }>> => {
  return request.delete('/users/api-key')
}

// --- User Settings ---

export interface UserSettings {
  id?: number
  user_id?: number
  language: string
  theme: string
  preferences?: Record<string, unknown>
  created_at?: string
  updated_at?: string
}

export const getUserSettings = async (): Promise<AxiosResponse<ApiResponse<UserSettings>>> => {
  return request.get('/user/settings')
}

export const updateUserSettings = async (data: {
  language?: string
  theme?: string
  preferences?: Record<string, unknown>
}): Promise<AxiosResponse<ApiResponse<UserSettings>>> => {
  return request.put('/user/settings', data)
}
