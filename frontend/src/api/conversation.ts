import request from '@/utils/request'
import type { AxiosResponse } from 'axios'
import type { ApiResponse } from '@/types/common'

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

export const getConversation = async (id: string): Promise<AxiosResponse<{ data: Conversation & { messages: any[] } }>> => {
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

export const batchDeleteConversations = async (ids: string[]): Promise<void> => {
  return request.delete('/chat/conversations/batch', { data: { ids } })
}
