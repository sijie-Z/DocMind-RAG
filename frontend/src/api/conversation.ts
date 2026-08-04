import request from '@/utils/request'
import type { AxiosResponse } from 'axios'
import type { ApiResponse } from '@/types/common'
import type { ChatMessage } from '@/types/chat'

export interface Conversation {
  id: string
  title: string
  created_at: string
  updated_at: string
  message_count: number
  last_message?: string
}

export interface ConversationListResponse {
  data: Conversation[]
  total: number
  page: number
  page_size: number
}

export const getConversations = async (params?: {
  page?: number
  page_size?: number
  search?: string
}): Promise<AxiosResponse<ApiResponse<ConversationListResponse>>> => {
  return request.get('/chat/conversations', { params })
}

export const getConversation = async (id: string): Promise<AxiosResponse<{ data: Conversation & { messages: ChatMessage[] } }>> => {
  return request.get(`/chat/conversations/${id}`)
}

export const createConversation = async (data: {
  title: string
}): Promise<AxiosResponse<{ data: Conversation }>> => {
  return request.post('/chat/conversations', data)
}

export const updateConversation = async (id: string, data: {
  title?: string
}): Promise<AxiosResponse<{ data: Conversation }>> => {
  return request.put(`/chat/conversations/${id}`, data)
}

export const deleteConversation = async (id: string): Promise<void> => {
  return request.delete(`/chat/conversations/${id}`)
}

// 获取对话消息详情（别名，向后兼容）
export const getConversationMessages = getConversation

// 清空会话消息
export const clearConversationMessages = async (conversationId: string) => {
  return request.delete<ApiResponse>(`/chat/conversations/${conversationId}/clear`)
}

// 解除文档绑定
export const unbindConversationDocs = async (conversationId: string) => {
  return request.post<ApiResponse>(`/chat/conversations/${conversationId}/unbind-docs`)
}

// 消息反馈 (点赞/点踩)
export const updateMessageFeedback = async (messageId: string | number, feedback: number, note?: string) => {
  return request.post<ApiResponse>(`/chat/messages/${messageId}/feedback`, { feedback, note })
}
