import request from '@/utils/request'
import type { AxiosResponse } from 'axios'

export interface PromptTemplate {
  id: number
  name: string
  content: string
  description?: string
  is_active: boolean
  is_system: boolean
  category: string
  created_at: string
  updated_at?: string
  creator_id?: number
}

export const getPrompts = async (params?: {
  skip?: number
  limit?: number
  category?: string
}): Promise<AxiosResponse<PromptTemplate[]>> => {
  return request.get('/prompts/', { params })
}

export const createPrompt = async (data: Partial<PromptTemplate>): Promise<AxiosResponse<PromptTemplate>> => {
  return request.post('/prompts/', data)
}

export const updatePrompt = async (id: number, data: Partial<PromptTemplate>): Promise<AxiosResponse<PromptTemplate>> => {
  return request.put(`/prompts/${id}`, data)
}

export const deletePrompt = async (id: number): Promise<void> => {
  return request.delete(`/prompts/${id}`)
}

export const seedDefaultPrompts = async (): Promise<void> => {
  return request.post('/prompts/seed')
}
