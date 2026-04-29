import request from '@/utils/request'
import type { AxiosResponse } from 'axios'

// 工作流节点类型
export interface WorkflowNode {
  id: string
  type: string
  position: { x: number; y: number }
  data: Record<string, any>
}

export interface WorkflowEdge {
  id: string
  source: string
  target: string
  sourceHandle?: string
  targetHandle?: string
  label?: string
  animated?: boolean
  markerEnd?: string | { type: string; color?: string }
  type?: string
}

export interface WorkflowConfig {
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
}

export interface Workflow {
  id: number
  name: string
  description?: string
  flow_data?: WorkflowConfig
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface NodeDefinition {
  id: number
  node_type: string
  name: string
  category: string
  description?: string
  default_config?: Record<string, any>
  input_schema?: Record<string, any>
  output_schema?: Record<string, any>
  icon?: string
}

export interface ExecutionResult {
  execution_id: number
  status: string
  output?: any
  node_results?: any[]
}

// API 函数
export const getWorkflows = async (params?: { skip?: number; limit?: number }) => {
  return request.get('/workflows/', { params })
}

export const getWorkflow = async (id: number) => {
  return request.get(`/workflows/${id}`)
}

export const createWorkflow = async (data: { name: string; description?: string; flow_data?: WorkflowConfig }) => {
  return request.post('/workflows/', data)
}

export const updateWorkflow = async (id: number, data: Partial<Workflow>) => {
  return request.put(`/workflows/${id}`, data)
}

export const deleteWorkflow = async (id: number) => {
  return request.delete(`/workflows/${id}`)
}

export const executeWorkflow = async (id: number, inputData?: Record<string, any>) => {
  return request.post(`/workflows/${id}/execute`, { workflow_id: id, input_data: inputData, stream: false })
}

// 流式执行
export const executeWorkflowStream = (id: number, inputData: Record<string, any>, onEvent: (event: string, data: any) => void) => {
  const eventSource = new EventSource(`/api/v1/workflows/${id}/execute?stream=true&input_data=${encodeURIComponent(JSON.stringify(inputData))}`)

  eventSource.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data)
      onEvent(e.type, data)
    } catch (err) {
      console.error('Failed to parse event:', err)
    }
  }

  eventSource.onerror = (err) => {
    console.error('EventSource error:', err)
    eventSource.close()
  }

  return () => eventSource.close()
}

export const getNodeDefinitions = async () => {
  return request.get('/workflows/nodes/definitions')
}

export const getExecution = async (executionId: number) => {
  return request.get(`/workflows/executions/${executionId}`)
}
