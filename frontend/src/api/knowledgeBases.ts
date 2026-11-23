import request from '@/utils/request'
import type { AxiosResponse } from 'axios'
import { apiUnwrap } from '@/utils/apiUnwrap'

/**
 * 知识库实体（多库）列表项：对应后端 KnowledgeBaseResponse。
 * 组织由服务端从当前用户推导——客户端不得传 organization_id。
 */
export interface KnowledgeBaseEntity {
  id: string
  organization_id: number
  name: string
  description?: string | null
  created_by?: number | null
  created_at: string
  updated_at?: string | null
}

export interface KnowledgeBaseEntityList {
  data: KnowledgeBaseEntity[]
  total: number
  page: number
  page_size: number
}

export interface KnowledgeBaseListOptions {
  /** 页面自理错误：请求带 X-Silent-Error，不弹全局 toast */
  silent?: boolean
  search?: string
  page?: number
  pageSize?: number
}

const SILENT_HEADERS = { headers: { 'X-Silent-Error': '1' } } as const

type KnowledgeBaseListEnvelope =
  | KnowledgeBaseEntityList
  | { data: KnowledgeBaseEntity[]; total?: number; page?: number; page_size?: number }

/**
 * GET /api/v1/knowledge-bases/（trailing slash 必需，否则 307 会丢 Header）。
 * 真实响应是单层信封 {success, data: [...], total, page, page_size}；
 * 历史 mock 也可能给出双层 {success, data:{data, total, ...}}。两者都归一到分页体。
 * 列表语义由服务端按有效组织 + ACL 决定，客户端不做组织级预过滤。
 */
export const listKnowledgeBases = async (
  options: KnowledgeBaseListOptions = {}
): Promise<KnowledgeBaseEntityList> => {
  const page = options.page ?? 1
  const pageSize = options.pageSize ?? 100
  const params: Record<string, string | number> = { page, page_size: pageSize }
  if (options.search?.trim()) params.search = options.search.trim()
  const response: AxiosResponse<unknown> = await request.get('/knowledge-bases/', {
    params,
    ...(options.silent ? SILENT_HEADERS : {})
  })
  const raw = (response.data ?? {}) as Record<string, unknown>
  const firstData = raw.data
  const payload = apiUnwrap<KnowledgeBaseListEnvelope | KnowledgeBaseEntity[]>(response)
  if (Array.isArray(payload)) {
    // apiUnwrap 剥到裸数组：真实响应 {success, data:[...], total...} 的计数在外层，
    // 历史双层 {success, data:{data:[...], total...}} 的计数在内层。
    const source = Array.isArray(firstData)
      ? raw
      : ((firstData ?? {}) as Record<string, unknown>)
    return {
      data: payload,
      total: typeof source.total === 'number' ? source.total : payload.length,
      page: typeof source.page === 'number' ? source.page : page,
      page_size: typeof source.page_size === 'number' ? source.page_size : pageSize,
    }
  }
  return {
    data: payload?.data ?? [],
    total: payload.total ?? 0,
    page: payload.page ?? page,
    page_size: payload.page_size ?? pageSize,
  }
}
