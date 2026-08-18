import request from '@/utils/request'
import type { AxiosResponse } from 'axios'
import type { ApiResponse } from '@/types/common'
import { getToken } from '@/utils/auth'

export interface AuditLog {
  id: number
  user_id: number
  username?: string
  action: string
  target_type?: string
  target_id?: string
  detail?: string
  ip_address?: string
  created_at: string
}

export interface AuditLogResponse {
  items: AuditLog[]
  total: number
}

export const getAuditLogs = async (params?: {
  skip?: number
  limit?: number
  user_id?: number
  action?: string
  search?: string
  start_date?: string
  end_date?: string
}): Promise<AxiosResponse<ApiResponse<AuditLogResponse>>> => {
  return request.get('/users/audit-logs', { params })
}

export const exportAuditLogs = async (params?: {
  user_id?: number
  action?: string
  start_date?: string
  end_date?: string
}): Promise<AxiosResponse<Blob>> => {
  return request.get('/users/audit-logs/export', {
    params,
    responseType: 'blob'
  })
}

export const downloadAuditLogs = (params?: {
  user_id?: number
  action?: string
  start_date?: string
  end_date?: string
}) => {
  const baseUrl = import.meta.env.VITE_API_BASE_URL || ''
  const query = new URLSearchParams()
  if (params?.user_id) query.append('user_id', String(params.user_id))
  if (params?.action) query.append('action', params.action)
  if (params?.start_date) query.append('start_date', params.start_date)
  if (params?.end_date) query.append('end_date', params.end_date)

  // 修复：使用统一 token 读取入口（原代码读取的 access_token/token 键从未被写入，导出恒 401）
  const token = getToken()
  // 修复：与 request.ts 的 baseURL 规则保持一致（VITE_API_BASE_URL 已含 /api/v1 时不重复拼接）
  const apiBase = baseUrl.endsWith('/api/v1') ? baseUrl : `${baseUrl}/api/v1`
  const url = `${apiBase}/users/audit-logs/export${query.toString() ? '?' + query.toString() : ''}`

  fetch(url, {
    headers: {
      'Authorization': token ? `Bearer ${token}` : ''
    }
  })
    .then(res => {
      if (!res.ok) throw new Error('Export failed')
      return res.blob()
    })
    .then(blob => {
      const downloadUrl = window.URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = downloadUrl
      a.download = `audit_logs_${new Date().toISOString().slice(0, 10)}.csv`
      document.body.appendChild(a)
      a.click()
      window.URL.revokeObjectURL(downloadUrl)
      document.body.removeChild(a)
    })
    .catch(() => {
      // Export failed silently
    })
}
