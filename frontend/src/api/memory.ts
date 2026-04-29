import request from '@/utils/request'
import type { AxiosResponse } from 'axios'

// 记忆项接口
export interface MemoryItem {
  id: string
  content: string
  memory_type: string
  importance: number
  metadata: Record<string, any>
  created_at: string
  last_accessed: string
  access_count: number
}

export interface MemorySystemData {
  agent_id: string
  short_term: MemoryItem[]
  long_term: Record<string, MemoryItem[]>
  working: Record<string, any>
  reflective: {
    insights: Array<{ content: string; context: Record<string, any>; created_at: string }>
    patterns: Array<{ pattern: string; examples: string[]; created_at: string }>
    lessons: Array<{ lesson: string; trigger?: string; solution?: string; created_at: string }>
  }
  interaction_count: number
}

// 获取 Agent 记忆
export const getAgentMemory = async (agentId: string = 'default') => {
  return request.get(`/memory/${agentId}`)
}

// 存储记忆
export const storeMemory = async (
  agentId: string,
  content: string,
  memoryType: string = 'short_term',
  importance: number = 0.5,
  metadata?: Record<string, any>
) => {
  return request.post(`/memory/${agentId}/remember`, null, {
    params: { content, memory_type: memoryType, importance },
    data: { metadata }
  })
}

// 检索记忆
export const recallMemory = async (
  agentId: string,
  query: string,
  memoryTypes?: string[],
  topK: number = 10
) => {
  return request.post(`/memory/${agentId}/recall`, {
    query,
    memory_types: memoryTypes,
    top_k: topK
  })
}

// 存储交互
export const storeInteraction = async (
  agentId: string,
  userInput: string,
  assistantResponse: string
) => {
  return request.post(`/memory/${agentId}/interaction`, {
    user_input: userInput,
    assistant_response: assistantResponse
  })
}

// 存储经验
export const storeExperience = async (
  agentId: string,
  success: boolean,
  action: string,
  result: string,
  context?: string
) => {
  return request.post(`/memory/${agentId}/experience`, {
    success,
    action,
    result,
    context
  })
}

// 获取记忆上下文
export const getMemoryContext = async (agentId: string, query: string) => {
  return request.get(`/memory/${agentId}/context`, {
    params: { query }
  })
}

// 清空记忆
export const clearMemory = async (agentId: string, memoryType?: string) => {
  return request.delete(`/memory/${agentId}`, {
    params: { memory_type: memoryType }
  })
}

// 导入记忆
export const importMemory = async (agentId: string, data: MemorySystemData) => {
  return request.post(`/memory/${agentId}/import`, data)
}

// 获取洞察
export const getInsights = async (agentId: string, context?: string, topK: number = 10) => {
  return request.get(`/memory/${agentId}/insights`, {
    params: { context, top_k: topK }
  })
}

// 获取经验教训
export const getLessons = async (agentId: string, situation?: string) => {
  return request.get(`/memory/${agentId}/lessons`, {
    params: { situation }
  })
}
