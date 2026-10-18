import request from '@/utils/request'
import type { AxiosRequestConfig, AxiosResponse } from 'axios'
import type { ApiResponse } from '@/types/common'

/**
 * 用户列表项 —— 与后端 GET /users/ 的实际返回逐字对齐
 * （backend/app/api/v1/endpoints/users.py:784-790）。
 * 注意：列表接口返回裸扁平结构 {total, skip, limit, users}（无 success/data 信封），
 * 且没有 nickname/status/phone/updated_at 字段（status 的真身是布尔 is_active）。
 */
export interface User {
  id: number
  username: string
  email: string
  full_name: string | null
  organization_id: number | null
  role: 'admin' | 'user'
  is_active: boolean
  created_at: string | null
  last_login_at: string | null
}

export interface UserListResponse {
  total: number
  skip: number
  limit: number
  users: User[]
}

/**
 * GET /users/me 的真实形状（users.py:429-446）：含 nickname/avatar_url/bio/
 * api_key/preferences，无 status（真身 is_active 布尔）；与列表 item 形状不同，
 * 不继承列表的 User。
 */
export interface UserProfileResponse {
  id: number
  username: string
  email: string
  full_name: string | null
  nickname: string | null
  phone: string | null
  avatar_url: string | null
  bio: string | null
  organization_id: number | null
  role: 'admin' | 'user'
  is_superuser: boolean
  is_active: boolean
  created_at: string | null
  last_login_at: string | null
  preferences?: Record<string, unknown> | null
  api_key?: string | null
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

/**
 * 用户列表。后端端点仅声明 skip/limit 两个查询参数
 * （users.py:755-761），不存在的 page/page_size/search 会被 FastAPI 静默忽略
 * —— 不发送装样子的假参数；服务端搜索当前不可用（见 users 视图内注释）。
 */
export const getUsers = async (params?: {
  skip?: number
  limit?: number
}): Promise<AxiosResponse<UserListResponse>> => {
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
}, config?: AxiosRequestConfig): Promise<AxiosResponse<ApiResponse<{ items: AuditLogEntry[]; total: number }>>> => {
  return request.get('/users/audit-logs', { params, ...config })
}

export const createUser = async (data: {
  username: string
  email: string
  password: string
  nickname: string
  phone?: string
  role: 'admin' | 'user'
  status: 'active' | 'inactive'
  organization_ids?: number[]
  remark?: string
}): Promise<AxiosResponse<{ data: User }>> => {
  return request.post('/users/', data)
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

export const getUserStats = async (config?: AxiosRequestConfig): Promise<AxiosResponse<UserStats>> => {
  return request.get('/users/stats', config)
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

// 个人 API Key 生成/撤销通道随 B3 死设定清理移除：Profile UI 入口已删，
// 后端 POST/DELETE /users/api-key 仅保留一个 minor 并标记 deprecated。

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

/** AI Provider 清单项（后端 user_settings.py _AVAILABLE_PROVIDERS 形状） */
export interface ProviderDef {
  id: string
  name: string
  base_url: string
  models: string[]
  requires_key: boolean
}

export interface ProviderCurrent {
  provider: string
  model: string
  api_key?: string
  base_url?: string
}

export interface ProvidersResponse {
  success?: boolean
  providers: ProviderDef[]
  current?: ProviderCurrent | null
}

/**
 * GET /user/settings/providers（request baseURL=/api/v1 → 完整路径
 * /api/v1/user/settings/providers）。统一客户端负责鉴权头、401 刷新与错误提示。
 */
export const getUserSettingsProviders = async (): Promise<AxiosResponse<ProvidersResponse>> => {
  return request.get('/user/settings/providers')
}
