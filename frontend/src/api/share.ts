import request from '@/utils/request'
import type { ApiResponse } from '@/types/common'
export interface SharedMessage {
  id?: string
  content: string
  messageType: 'user' | 'assistant'
  createdAt?: string
  sources?: Array<{ filename?: string; content?: string; score?: number }>
}

export interface ShareSnapshot {
  id: string
  title: string
  messages: SharedMessage[]
  createdAt: string
}

/** 匿名只读端点：不需要登录，也不发送 Authorization；失败走统一 404 文案。 */
export const getSharedConversation = (token: string): Promise<{ data: ApiResponse<ShareSnapshot> }> =>
  request.get(`/chat/shares/${encodeURIComponent(token)}`, { headers: { 'X-Silent-Error': '1' } })

export interface ShareStatus {
  shared: boolean
  createdAt: string | null
  expiresAt: string | null
  expired?: boolean
}

export const getConversationShareStatus = (conversationId: string) =>
  request.get<ApiResponse<ShareStatus>>(`/chat/conversations/${conversationId}/share`)

export interface ShareCreation {
  token: string
  url: string
  createdAt: string
  expiresAt: string | null
}

/**
 * 创建/轮换；token 原文只在本次响应可见。
 * `expiresInDays` 为 null（缺省）时链接永不过期；1-90 有效（后端校验）。
 */
export const createConversationShare = (
  conversationId: string,
  expiresInDays: number | null = null,
) =>
  request.post<ApiResponse<ShareCreation>>(`/chat/conversations/${conversationId}/share`, {
    expires_in_days: expiresInDays,
  })

export const revokeConversationShare = (conversationId: string) =>
  request.delete<ApiResponse<{ shared: boolean }>>(`/chat/conversations/${conversationId}/share`)
