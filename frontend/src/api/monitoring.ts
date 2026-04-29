import request from '@/utils/request'

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
  api_endpoints: Record<string, any>
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
    system: any[]
    application: any[]
    knowledge_base: any[]
  }
  alerts: Alert[]
  timestamp: number
}

export const getMonitoringDashboard = async (): Promise<{ data: MonitoringDashboard }> => {
  return request.get('/monitoring/dashboard')
}

export const getMetrics = async (
  metricType: 'system' | 'application' | 'knowledge_base',
  timeRange: string = '1h'
): Promise<{ data: any }> => {
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
  return request.get('/monitoring/llm-stats')
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
}

export interface OrgSummary {
  org_id: number
  org_name: string
  user_count: number
  doc_count: number
}

export const getAdminSummary = async (): Promise<{ data: AdminSummary }> => {
  return request.get('/monitoring/admin-summary')
}

export const getOrgSummary = async (): Promise<{ data: { organizations: OrgSummary[] } }> => {
  return request.get('/monitoring/org-summary')
}

export const getRAGStats = async (): Promise<{ data: RAGStats }> => {
  return request.get('/knowledge/rag-stats')
}