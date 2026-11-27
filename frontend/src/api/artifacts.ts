import request from '@/utils/request'
import { getToken } from '@/utils/auth'
import type { AxiosResponse } from 'axios'
import type { ApiResponse } from '@/types/common'
import type {
  AgentArtifact,
  ArtifactListResponse,
  ArtifactShareCreation,
  ArtifactShareListResponse,
  ArtifactShareStatus,
  PublicArtifactMeta,
} from '@/types/artifact'

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

  /** 创建/轮换分享；token 原文只在本次响应出现一次（旧链接立即失效）。 */
  createShare(
    artifactId: string,
    expiresInDays?: number | null,
  ): Promise<AxiosResponse<ApiResponse<ArtifactShareCreation>>> {
    return request.post(
      `/artifacts/${encodeURIComponent(artifactId)}/share`,
      { expires_in_days: expiresInDays ?? null },
      { headers: { 'X-Silent-Error': '1' } },
    )
  },

  /** 查询分享状态（不返回 token）。 */
  getShare(artifactId: string): Promise<AxiosResponse<ApiResponse<ArtifactShareStatus>>> {
    return request.get(`/artifacts/${encodeURIComponent(artifactId)}/share`, {
      headers: { 'X-Silent-Error': '1' },
    })
  },

  /** 当前用户所有有效分享（未撤销、未过期）；列表不含 token。 */
  listShares(): Promise<AxiosResponse<ApiResponse<ArtifactShareListResponse>>> {
    return request.get('/artifacts/shares', {
      headers: { 'X-Silent-Error': '1' },
    })
  },

  /** 撤销分享；撤销后旧链接立即失效。 */
  revokeShare(artifactId: string): Promise<AxiosResponse<ApiResponse<ArtifactShareStatus>>> {
    return request.delete(`/artifacts/${encodeURIComponent(artifactId)}/share`, {
      headers: { 'X-Silent-Error': '1' },
    })
  },
}

/** 公开工件分享（匿名，无 Authorization）：token 即能力凭证。 */
export const publicArtifactsApi = {
  get(token: string): Promise<AxiosResponse<ApiResponse<PublicArtifactMeta>>> {
    return request.get(`/public/artifacts/${encodeURIComponent(token)}`, {
      headers: { 'X-Silent-Error': '1' },
    })
  },

  /** 拉公开正文；404（撤销/过期/开关关闭）由页面统一按不可用处理。 */
  async fetchContent(token: string): Promise<Response> {
    const baseUrl = import.meta.env.VITE_API_BASE_URL || '/api/v1'
    return fetch(`${baseUrl}/public/artifacts/${encodeURIComponent(token)}/content`, {
      method: 'GET',
      headers: { 'X-Silent-Error': '1' },
    })
  },
}
