import request from '@/utils/request'
import { apiUnwrap } from '@/utils/apiUnwrap'

/**
 * 公开知识库只读检索（批次十三）。
 *
 * 管理面（登录 + 组织管理员）：创建/轮换/撤销发布、维护白名单。
 * token 明文只在创建/轮换响应返回一次；状态/列表永不回显 token。
 * 公开面（匿名）：token 即全部权限，失败一律按「不存在或已失效」处理。
 */
export interface KbPublication {
  id: string
  knowledge_base_id: string
  knowledge_base_name: string | null
  title: string
  description: string | null
  enabled: boolean
  active: boolean
  expires_at: string | null
  revoked_at: string | null
  created_at: string | null
  document_count: number
}

export interface KbPublicationWithToken extends KbPublication {
  token: string
  url: string
}

export interface KbCandidateDocument {
  id: string
  title: string
}

export interface KbPublishedDocument {
  id: string
  title: string
  status: string
  searchable: boolean
}

export interface KbCandidateList {
  documents: KbCandidateDocument[]
  truncated: boolean
}

export interface KbDocumentList {
  documents: KbPublishedDocument[]
  document_count: number
}

export interface PublicKbMeta {
  title: string
  description: string | null
  expires_at: string | null
}

export interface PublicKbSearchHit {
  rank: number
  title: string
  snippet: string
  score: number
}

export interface PublicKbSearchResponse {
  results: PublicKbSearchHit[]
  total: number
}

export type PublicationDocumentMode = 'replace' | 'add' | 'remove'

const SILENT_HEADERS = { headers: { 'X-Silent-Error': '1' } } as const

export const publicKbApi = {
  /** 本组织发布列表（可按知识库过滤）；响应不含 token。 */
  async list(knowledgeBaseId?: string): Promise<KbPublication[]> {
    const params: Record<string, string> = {}
    if (knowledgeBaseId) params.knowledge_base_id = knowledgeBaseId
    const res = await request.get('/kb-publications', { params, ...SILENT_HEADERS })
    return apiUnwrap<KbPublication[]>(res) ?? []
  },

  async create(payload: {
    knowledge_base_id: string
    title: string
    description?: string | null
    expires_in_days?: number | null
    document_ids?: string[]
  }): Promise<KbPublicationWithToken> {
    const res = await request.post('/kb-publications', payload, SILENT_HEADERS)
    return apiUnwrap<KbPublicationWithToken>(res)
  },

  async get(id: string): Promise<KbPublication> {
    const res = await request.get(`/kb-publications/${encodeURIComponent(id)}`, SILENT_HEADERS)
    return apiUnwrap<KbPublication>(res)
  },

  /** 轮换 token：旧链接立即失效；新 token 只在本响应出现一次。 */
  async rotate(id: string, expiresInDays?: number | null): Promise<KbPublicationWithToken> {
    const res = await request.post(
      `/kb-publications/${encodeURIComponent(id)}/rotate`,
      { expires_in_days: expiresInDays ?? null },
      SILENT_HEADERS,
    )
    return apiUnwrap<KbPublicationWithToken>(res)
  },

  async revoke(id: string): Promise<KbPublication> {
    const res = await request.post(
      `/kb-publications/${encodeURIComponent(id)}/revoke`,
      {},
      SILENT_HEADERS,
    )
    return apiUnwrap<KbPublication>(res)
  },

  async candidates(knowledgeBaseId: string): Promise<KbCandidateList> {
    const res = await request.get('/kb-publications/candidates', {
      params: { knowledge_base_id: knowledgeBaseId },
      ...SILENT_HEADERS,
    })
    return apiUnwrap<KbCandidateList>(res)
  },

  async documents(id: string): Promise<KbDocumentList> {
    const res = await request.get(
      `/kb-publications/${encodeURIComponent(id)}/documents`,
      SILENT_HEADERS,
    )
    return apiUnwrap<KbDocumentList>(res)
  },

  async setDocuments(
    id: string,
    mode: PublicationDocumentMode,
    documentIds: string[],
  ): Promise<KbDocumentList> {
    const res = await request.post(
      `/kb-publications/${encodeURIComponent(id)}/documents`,
      { mode, document_ids: documentIds },
      SILENT_HEADERS,
    )
    return apiUnwrap<KbDocumentList>(res)
  },
}

/** 公开只读检索（匿名，无 Authorization；token 即能力凭证）。 */
export const publicKbPublicApi = {
  async get(token: string): Promise<PublicKbMeta> {
    const res = await request.get(`/public/kb/${encodeURIComponent(token)}`, SILENT_HEADERS)
    return apiUnwrap<PublicKbMeta>(res)
  },

  async search(token: string, query: string, topK = 5): Promise<PublicKbSearchResponse> {
    const res = await request.post(
      `/public/kb/${encodeURIComponent(token)}/search`,
      { query, top_k: topK },
      SILENT_HEADERS,
    )
    return apiUnwrap<PublicKbSearchResponse>(res)
  },
}
