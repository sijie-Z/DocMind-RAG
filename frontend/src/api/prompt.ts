import request from '@/utils/request'
import type { AxiosResponse } from 'axios'

export interface PromptTemplate {
  id: number
  name: string
  content: string
  description?: string
  is_active: boolean
  is_system: boolean
  // B4（D6）：system / organization / personal
  scope?: 'system' | 'organization' | 'personal'
  category: string
  version?: number
  created_at: string
  updated_at?: string
  creator_id?: number
}

export interface PromptTemplateVersion {
  id: number
  prompt_id: number
  version: number
  name: string
  content: string
  description?: string
  change_note?: string
  created_at: string
  creator_id?: number
}

/**
 * E14/D1：提示词页自管理全部错误呈现（页内错误态 + 唯一 toast），
 * 所有请求带 X-Silent-Error，避免响应拦截器再弹一次全局 toast（双提示）。
 */
const SILENT = { headers: { 'X-Silent-Error': '1' } } as const

export const getPrompts = async (params?: {
  skip?: number
  limit?: number
  category?: string
}): Promise<AxiosResponse<PromptTemplate[]>> => {
  return request.get('/prompts/', { params, ...SILENT })
}

export const createPrompt = async (data: Partial<PromptTemplate>): Promise<AxiosResponse<PromptTemplate>> => {
  return request.post('/prompts/', data, SILENT)
}

export const updatePrompt = async (id: number, data: Partial<PromptTemplate> & { change_note?: string }): Promise<AxiosResponse<PromptTemplate>> => {
  return request.put(`/prompts/${id}`, data, SILENT)
}

export const deletePrompt = async (id: number): Promise<void> => {
  return request.delete(`/prompts/${id}`, SILENT)
}

export const seedDefaultPrompts = async (): Promise<void> => {
  return request.post('/prompts/seed', undefined, SILENT)
}

export const getPromptVersions = async (promptId: number): Promise<AxiosResponse<PromptTemplateVersion[]>> => {
  return request.get(`/prompts/${promptId}/versions`, SILENT)
}

export const restorePromptVersion = async (promptId: number, version: number): Promise<AxiosResponse<PromptTemplate>> => {
  return request.post(`/prompts/${promptId}/restore/${version}`, undefined, SILENT)
}

export const getPromptVersionDetail = async (promptId: number, version: number): Promise<AxiosResponse<PromptTemplateVersion>> => {
  return request.get(`/prompts/${promptId}/version/${version}`, SILENT)
}
