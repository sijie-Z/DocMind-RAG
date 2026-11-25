import request from '@/utils/request'
import { getToken } from '@/utils/auth'
import type { AxiosResponse } from 'axios'
import type { ApiResponse } from '@/types/common'
import type { AgentArtifact, ArtifactListResponse } from '@/types/artifact'

/** 工件 API 客户端（批次七）。信封契约同 agentApi：拦截器返回完整 AxiosResponse，
 *  消费者统一用 `apiUnwrap(res)` 解一层。 */
export const artifactsApi = {
  list(params?: {
    conversation_id?: string
    task_id?: string
    limit?: number
  }): Promise<AxiosResponse<ApiResponse<ArtifactListResponse>>> {
    return request.get('/artifacts', { params })
  },

  get(artifactId: string): Promise<AxiosResponse<ApiResponse<AgentArtifact>>> {
    return request.get(`/artifacts/${encodeURIComponent(artifactId)}`)
  },

  create(payload: {
    kind: string
    title: string
    content: string
    filename?: string
    conversation_id?: string
    source_tool?: string
  }): Promise<AxiosResponse<ApiResponse<AgentArtifact & { created: boolean }>>> {
    return request.post('/artifacts', payload)
  },

  /**
   * 拉正文（用于预览/下载）。返回 Response —— 401/403/404 由调用方按状态码处理，
   * 不走全局错误 toast（右栏刷新失败不该打断对话）。
   */
  async fetchContent(artifactId: string): Promise<Response> {
    const token = getToken()
    const baseUrl = import.meta.env.VITE_API_BASE_URL || '/api/v1'
    return fetch(`${baseUrl}/artifacts/${encodeURIComponent(artifactId)}/content`, {
      method: 'GET',
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        'X-Silent-Error': '1',
      },
    })
  },
}
