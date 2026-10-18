import request, { FIRST_SCREEN_REQUEST_CONFIG } from '@/utils/request'

export interface SystemMetrics {
  timestamp: number
  cpu_percent: number
  memory_percent: number
  disk_usage: Record<string, number>
  network_io: Record<string, number>
  process_count: number
  load_average: number[]
}

export interface ApplicationMetrics {
  timestamp: number
  active_connections: number
  request_count: number
  error_count: number
  response_time: number
  api_endpoints: Record<string, { count: number; avg_time: number }>
  database_connections: number
  redis_connections: number
  elasticsearch_health: string
}

export interface KnowledgeBaseMetrics {
  timestamp: number
  total_documents: number
  total_chunks: number
  index_size_mb: number
  search_requests: number
  search_response_time: number
  document_types: Record<string, number>
  user_activity: Record<string, number>
}

export interface Alert {
  type: string
  level: 'warning' | 'critical'
  message: string
  value: number
  threshold: number
  timestamp: number
}

export interface MonitoringDashboard {
  current: {
    system: SystemMetrics | null
    application: ApplicationMetrics | null
    knowledge_base: KnowledgeBaseMetrics | null
  }
  trends: {
    system: SystemMetrics[]
    application: ApplicationMetrics[]
    knowledge_base: KnowledgeBaseMetrics[]
  }
  alerts: Alert[]
  timestamp: number
}

export const getMonitoringDashboard = async (): Promise<{ data: MonitoringDashboard }> => {
  return request.get('/monitoring/dashboard')
}

/**
 * 趋势快照元素 —— 真实形状（core/middleware.py:129-136 take_snapshot）：
 * HTTP 管道累计计数器快照，timestamp 为秒级。注意：metric_type 与 time_range
 * 在后端函数体内未被使用（任意 type 返回同一份快照历史，monitoring.py:151-166）。
 */
export interface TrendPoint {
  timestamp: number
  request_count: number
  error_count: number
  response_time: number
  active_connections: number
  slow_request_count: number
  error_rate_percent: number
  p95_response_time_ms: number
  p99_response_time_ms: number
}

export const getMetrics = async (
  metricType: 'system' | 'application' | 'knowledge_base',
  timeRange: string = '1h'
): Promise<{ data: { data: TrendPoint[] } }> => {
  // 旧声明 {metrics, total} 为前端自造形状（后端恒返回 {"data": [...]}，<2 条时 []）
  return request.get(`/monitoring/metrics/${metricType}`, {
    params: { time_range: timeRange }
  })
}

export const getMonitoringAlerts = async (limit: number = 10): Promise<{ data: { alerts: Alert[]; total: number } }> => {
  return request.get('/monitoring/alerts', {
    params: { limit }
  })
}

export interface LLMStats {
  total_tokens_today: number
  cost_today_usd: number
  avg_latency_ms: number
  p95_latency_ms: number
  request_count_today: number
  cost_warning: boolean
  token_trend_7d: number[]
}

export const getLLMStats = async (): Promise<{ data: LLMStats }> => {
  return request.get('/monitoring/llm-stats', FIRST_SCREEN_REQUEST_CONFIG)
}

/** LLM 配置状态（B5 onboarding「连模型」步骤数据源） */
export interface LLMStatus {
  configured: boolean
  provider: string
  model: string
  source: 'server' | 'local' | string
}

export const getLLMStatus = async (): Promise<{
  data: { success?: boolean; data: LLMStatus }
}> => {
  return request.get('/monitoring/llm-status', FIRST_SCREEN_REQUEST_CONFIG)
}

export interface RAGStats {
  total_queries_7d: number
  hits_with_documents: number
  hit_rate: number
  avg_documents_retrieved: number
  top_keywords: string[]
  hit_rate_trend_7d: number[]
}

export interface AdminSummary {
  total_users: number
  total_documents: number
  total_sessions: number
  active_users_24h: number
  total_organizations: number
  // W2-T7：会话周环比真值（近 7 日/前 7 日新增；降级态为 null）
  sessions_this_week?: number | null
  sessions_last_week?: number | null
}

export interface OrgSummary {
  org_id: number
  org_name: string
  user_count: number
  doc_count: number
}

export const getAdminSummary = async (): Promise<{ data: AdminSummary }> => {
  return request.get('/monitoring/admin-summary', FIRST_SCREEN_REQUEST_CONFIG)
}

export const getOrgSummary = async (): Promise<{ data: { organizations: OrgSummary[] } }> => {
  return request.get('/monitoring/org-summary', FIRST_SCREEN_REQUEST_CONFIG)
}

export const getRAGStats = async (): Promise<{ data: RAGStats }> => {
  return request.get('/knowledge/rag-stats', FIRST_SCREEN_REQUEST_CONFIG)
}

/**
 * 系统健康检查（monitoring.py:461-505，无需认证）。
 * status: 'healthy'（各服务非 disconnected）/ 'degraded'（任一 disconnected）；
 * services.elasticsearch 取值域：'green'|'yellow'|'red'|'unknown'|'disconnected'
 * （与 dashboard 的 elasticsearch_health 同源，无 'ok'/'error'）。
 */
export interface MonitoringHealth {
  status: 'healthy' | 'degraded'
  timestamp: string
  services: {
    database: string
    redis: string
    elasticsearch: string
  }
}

export const getMonitoringHealth = async (): Promise<{ data: MonitoringHealth }> => {
  return request.get('/monitoring/health', FIRST_SCREEN_REQUEST_CONFIG)
}