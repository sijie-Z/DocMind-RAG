// API 响应类型定义

// ── 通用 ──────────────────────────────────────────────

export interface ApiError {
  message: string
  code?: number
  detail?: string | ApiErrorDetail[]
  request_id?: string
}

export interface ApiErrorDetail {
  loc: (string | number)[]
  msg: string
  type: string
}

// ── 监控 ──────────────────────────────────────────────

export interface MetricItem {
  name: string
  value: number | string
  unit?: string
  status?: 'healthy' | 'warning' | 'critical'
}

export interface AlertItem {
  id: string
  severity: 'info' | 'warning' | 'critical'
  message: string
  timestamp: string
  resolved?: boolean
}

export interface MonitoringMetrics {
  system: MetricItem[]
  application: MetricItem[]
  knowledge_base: MetricItem[]
}

export interface HealthStatus {
  status: string
  service: string
  version: string
  timestamp?: string
  services?: Record<string, { status: string; latency_ms?: number }>
}

// ── 组织 ──────────────────────────────────────────────

export interface OrganizationMember {
  id: number
  username: string
  email: string
  nickname?: string
  full_name?: string
  role: string
  joined_at?: string
  is_active?: boolean
}

export interface OrganizationDocument {
  id: string
  filename: string
  file_size: number
  file_type?: string
  title?: string
  status: string
  created_at: string
  description?: string
  keywords?: string[]
}

// ── 工作流 ─────────────────────────────────────────────

export interface WorkflowNodeData {
  label: string
  type: string
  config?: Record<string, unknown>
}

export interface WorkflowNodeResult {
  node_id: string
  status: 'pending' | 'running' | 'success' | 'failed'
  output?: unknown
  error?: string
  duration_ms?: number
}

export interface WorkflowExecution {
  id: number
  workflow_id: number
  status: 'pending' | 'running' | 'success' | 'failed'
  input?: Record<string, unknown>
  output?: Record<string, unknown>
  node_results?: WorkflowNodeResult[]
  started_at?: string
  finished_at?: string
}

export interface WorkflowDefinition {
  id: number
  name: string
  description?: string
  flow_data?: {
    nodes: unknown[]
    edges: unknown[]
  }
  created_at?: string
  updated_at?: string
}

// ── 提示词 ─────────────────────────────────────────────

export interface PromptTemplate {
  id: number
  name: string
  content: string
  description?: string
  category?: string
  is_system?: boolean
  created_at?: string
}

// ── 文件上传 ───────────────────────────────────────────

export interface UploadProgressEvent {
  loaded: number
  total?: number
  progress?: number
}

// ── SSE ────────────────────────────────────────────────

export interface SSEMessage {
  type: string
  content?: string
  conversationId?: string | number
  messageId?: string
  sources?: KnowledgeSourceRef[]
  title?: string
  fileIds?: string[]
  rateLimit?: RateLimitInfo
  request_id?: string
  thinkingType?: string
  toolName?: string
  toolDurationMs?: number
}

export interface KnowledgeSourceRef {
  fileId?: number
  filename?: string
  relevanceScore?: number
  snippet?: string
  content?: string
}

export interface RateLimitInfo {
  limit?: number
  remaining?: number
  reset?: number
}

// ── 通知 ────────────────────────────────────────────────

export interface Notification {
  id: number
  user_id: number
  title: string
  content: string
  is_read: boolean
  created_at?: string
}
