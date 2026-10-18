import request from '@/utils/request'
import { getToken } from '@/utils/auth'

// 工作流节点类型
export interface WorkflowNodeData {
  label: string
  type: string
  config?: Record<string, unknown>
  [key: string]: unknown
}

export interface WorkflowNode {
  id: string
  type: string
  position: { x: number; y: number }
  data: WorkflowNodeData
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
  engine_type?: string
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
  default_config?: Record<string, unknown>
  input_schema?: Record<string, unknown>
  output_schema?: Record<string, unknown>
  icon?: string
}

export interface NodeResultItem {
  node_id: string
  node_type: string
  // 引擎成功态为 "completed"（ExecutionStatus，workflow_engine.py:37-42）；
  // 旧类型声明 'success' 属词表漂移（B10/C2）
  status: 'pending' | 'running' | 'completed' | 'failed'
  output?: unknown
  error?: string
  // 引擎字段名为 duration（毫秒）（schemas/workflow.py:84-86）；旧声明 duration_ms 为死键
  duration?: number | null
}

export interface ExecutionResult {
  execution_id: number
  status: string
  output?: Record<string, unknown>
  node_results?: NodeResultItem[]
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

export const executeWorkflow = async (id: number, inputData?: Record<string, unknown>) => {
  // 契约对齐（B8/B9，workflow.py:197-220）：
  //  - stream 是 **query** 参数（旧实现塞进 body → 后端不认 → 默认 True 走 SSE 分支，
  //    响应体是事件流 → 走 JSON 解包路径时业务体恒 undefined，面板静默假「执行完成」）；
  //  - 请求体本身就是 input_data（端点 `input_data = body`，引擎读 input["text"]；
  //    旧实现包成 {workflow_id, input_data, stream} → input["text"] 恒空）。
  return request.post(`/workflows/${id}/execute`, inputData ?? {}, { params: { stream: false } })
}

// ── 流式执行（issue #289 修复）────────────────────────────────────────────
// 修复前的实现有三重断点：
//   1. `new EventSource(url)` 发的是 GET，而 `/{id}/execute` 是 POST 路由
//      （backend/app/api/v1/endpoints/workflow.py:197），根本打不通；
//   2. EventSource 无法携带 Authorization 头，而端点依赖 get_current_user
//      （Bearer 认证）——即便路由匹配也会 401；
//   3. `EventSource.onmessage` 只接收**无** `event:` 名的消息，而该端点全部
//      是命名事件（workflow_start/node_start/node_complete/node_error/
//      workflow_complete/complete/error/done/heartbeat）——一个都到不了回调。
// 现改为 fetch + ReadableStream（与 utils/sseService.ts 的既有 fetch-SSE 模式
// 对齐）：POST JSON body（端点直接以 body 为 input_data），Bearer 走请求头，
// 命名事件经纯函数解析器逐帧派发 onEvent(eventName, data)。

export interface WorkflowSSEFrame {
  event: string
  data: unknown
}

/**
 * SSE 文本帧解析器（纯函数工厂，便于单测）：把 fetch ReadableStream 的文本块
 * 切分成 { event, data } 帧。与 sseService 的内联解析同构（buffer 按行处理、
 * `event:` 行命名、`data:` 行解析并派发；后端始终单行 data + `\n\n` 收帧，
 * 故不做 SSE 规范的多行 data 拼接）。坏 JSON 帧跳过（同 sseService 策略）。
 */
export function createWorkflowSSEFrameParser() {
  let buffer = ''
  let currentEvent = 'message'

  return {
    push(chunk: string): WorkflowSSEFrame[] {
      buffer += chunk
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''
      const frames: WorkflowSSEFrame[] = []
      for (const rawLine of lines) {
        const line = rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine
        if (line.startsWith('event:')) {
          currentEvent = line.slice(6).trim()
          continue
        }
        if (line.startsWith('data:')) {
          const jsonStr = line.slice(5).trim()
          if (!jsonStr) continue
          try {
            frames.push({ event: currentEvent, data: JSON.parse(jsonStr) })
          } catch {
            // 坏帧跳过（与 sseService post() 的 catch 同策略）
          }
          currentEvent = 'message'
        }
      }
      return frames
    },
  }
}

export const executeWorkflowStream = (
  id: number,
  inputData: Record<string, unknown>,
  onEvent: (_event: string, _data: ExecutionResult | NodeResultItem | Record<string, unknown>) => void,
  onClose?: () => void,
) => {
  const abortController = new AbortController()
  const parser = createWorkflowSSEFrameParser()

  const run = async () => {
    try {
      // token 解析口径与 sseService.post 一致
      const token = (localStorage.getItem('docmind_token') || localStorage.getItem('paicongming_token') || getToken())?.replace(/"/g, '')
      const response = await fetch(`/api/v1/workflows/${id}/execute?stream=true`, {
        method: 'POST',
        headers: {
          'Accept': 'text/event-stream',
          'Content-Type': 'application/json',
          ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
        },
        body: JSON.stringify(inputData),
        signal: abortController.signal,
      })

      if (!response.ok) {
        onEvent('error', { error: `HTTP ${response.status}` })
        return
      }

      const reader = response.body?.getReader()
      if (!reader) {
        onEvent('error', { error: '响应体不可读（环境不支持流式读取）' })
        return
      }

      const decoder = new TextDecoder()
      for (;;) {
        if (abortController.signal.aborted) { reader.cancel(); break }
        const { done, value } = await reader.read()
        if (done) break
        for (const frame of parser.push(decoder.decode(value, { stream: true }))) {
          onEvent(frame.event, frame.data as ExecutionResult | NodeResultItem)
        }
      }
    } catch {
      // abort 或网络异常：静默结束（未识别帧交 onEvent 语义处理；
      // 业务级失败由后端以 `event: error` 帧下发，经 onEvent 派发）
    } finally {
      // 流关闭统一出口：正常结束、abort、网络异常三条路径都收敛到这里。
      // 消费方（编辑器调试面板）用它收尾执行态，防 executing 悬挂。
      onClose?.()
    }
  }

  void run()

  return () => abortController.abort()
}

export const getNodeDefinitions = async () => {
  return request.get('/workflows/nodes/definitions')
}

export const getExecution = async (executionId: number) => {
  return request.get(`/workflows/executions/${executionId}`)
}
