import request from '@/utils/request'
import type { AxiosResponse } from 'axios'
import type { ApiResponse } from '@/types/common'

export interface RagMetrics {
  window_seconds: number
  retrieval_total: number
  retrieval_hit: number
  hit_rate: number
  grounded_total: number
  grounded_hit: number
  groundedness: number
  avg_latency_ms: number
  cache_hit: number
  retry_total: number
}

export const getRagMetrics = async (windowSeconds = 0): Promise<AxiosResponse<ApiResponse<RagMetrics>>> => {
  return request.get('/chat/metrics', { params: { window_seconds: windowSeconds } })
}
