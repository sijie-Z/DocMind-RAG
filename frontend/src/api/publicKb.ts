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
  /** 批次十七：发布级匿名聊天开关（默认关闭）。 */
  chat_enabled: boolean
  /** 每日 token 预算；null = 全局默认，0 = 禁止聊天。 */
  daily_token_budget: number | null
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
  /** 生效能力：全局开关 + 发布开关同时开启才为 true。 */
  chat_enabled: boolean
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

export type PublicKbChatRole = 'user' | 'assistant'

export interface PublicKbChatHistoryItem {
  role: PublicKbChatRole
  content: string
}

export interface PublicKbChatSource {
  rank: number
  title: string
  snippet: string
  score: number
}

export interface PublicKbChatResponse {
  answer: string
  sources: PublicKbChatSource[]
}

export interface PublicKbChatHandlers {
  onSources?: (sources: PublicKbChatSource[]) => void
  onDelta?: (delta: string) => void
  onDone?: (payload: { source_count?: number }) => void
  onError?: (error: PublicKbChatError) => void
}

/** 公开聊天错误：HTTP 失败或 SSE `error` 事件统一成同一形状。 */
export class PublicKbChatError extends Error {
  status: number | null
  code: string
  detail: unknown

  constructor(
    code: string,
    message: string,
    options: { status?: number | null; detail?: unknown } = {},
  ) {
    super(message)
    this.name = 'PublicKbChatError'
    this.code = code
    this.status = options.status ?? null
    this.detail = options.detail ?? null
  }
}

export type PublicationDocumentMode = 'replace' | 'add' | 'remove'

export interface KbChatSettingsPayload {
  chat_enabled: boolean
  daily_token_budget: number | null
}

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

  /** 批次十七：发布级聊天开关 + 日预算（null = 全局默认）。 */
  async setChatSettings(id: string, payload: KbChatSettingsPayload): Promise<KbPublication> {
    const res = await request.post(
      `/kb-publications/${encodeURIComponent(id)}/chat-settings`,
      payload,
      SILENT_HEADERS,
    )
    return apiUnwrap<KbPublication>(res)
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

  /**
   * 匿名聊天（SSE）：POST + fetch 读取（EventSource 不支持 POST/请求体）。
   * 事件契约：sources → delta* → done / error；HTTP 404/429 在首帧前以 JSON 返回。
   */
  async chat(
    token: string,
    payload: { message: string; history?: PublicKbChatHistoryItem[] },
    handlers: PublicKbChatHandlers = {},
    options: { signal?: AbortSignal } = {},
  ): Promise<void> {
    const baseUrl = import.meta.env.VITE_API_BASE_URL || '/api/v1'
    const response = await fetch(
      `${baseUrl}/public/kb/${encodeURIComponent(token)}/chat`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'text/event-stream',
        },
        body: JSON.stringify({ ...payload, stream: true }),
        signal: options.signal,
      },
    )
    if (!response.ok) {
      let body: Record<string, unknown> | null = null
      try {
        body = (await response.json()) as Record<string, unknown>
      } catch {
        body = null
      }
      const code =
        typeof body?.error_code === 'string' ? body.error_code : `HTTP_${response.status}`
      const message =
        typeof body?.message === 'string' ? body.message : `HTTP ${response.status}`
      throw new PublicKbChatError(code, message, {
        status: response.status,
        detail: body?.detail,
      })
    }
    const reader = response.body?.getReader()
    if (!reader) throw new PublicKbChatError('STREAM_UNAVAILABLE', 'No response body')

    const decoder = new TextDecoder()
    let buffer = ''
    let sawTerminal = false
    const dispatch = (frame: string): boolean => {
      let event = ''
      const dataLines: string[] = []
      for (const rawLine of frame.split('\n')) {
        const line = rawLine.replace(/\r$/, '')
        if (line.startsWith('event:')) event = line.slice(6).trim()
        else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
      }
      if (!event || dataLines.length === 0) return false
      let data: unknown
      try {
        data = JSON.parse(dataLines.join('\n'))
      } catch {
        return false
      }
      if (!data || typeof data !== 'object' || Array.isArray(data)) return false
      const payload = data as Record<string, unknown>
      if (event === 'sources') {
        handlers.onSources?.(
          Array.isArray(payload.sources) ? (payload.sources as PublicKbChatSource[]) : [],
        )
      } else if (event === 'delta') {
        if (typeof payload.delta === 'string') handlers.onDelta?.(payload.delta)
      } else if (event === 'done') {
        handlers.onDone?.(payload as { source_count?: number })
        return true
      } else if (event === 'error') {
        const code =
          typeof payload.error_code === 'string' ? payload.error_code : 'PUBLIC_KB_CHAT_FAILED'
        const message = typeof payload.message === 'string' ? payload.message : 'Chat failed'
        handlers.onError?.(
          new PublicKbChatError(code, message, { status: 200, detail: payload }),
        )
        return true
      }
      return false
    }

    try {
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        for (;;) {
          // 兼容 LF 与 CRLF 代理：帧边界只要求空行，不要求具体换行风格。
          const match = /\r?\n\r?\n/.exec(buffer)
          if (!match) break
          const frame = buffer.slice(0, match.index)
          buffer = buffer.slice(match.index + match[0].length)
          if (dispatch(frame)) {
            sawTerminal = true
            return
          }
        }
      }
      if (buffer.trim() && dispatch(buffer)) sawTerminal = true
      if (!sawTerminal) {
        throw new PublicKbChatError(
          'STREAM_INCOMPLETE',
          'Stream ended before done/error',
          { status: 200 },
        )
      }
    } finally {
      // done/error 提前返回时主动取消剩余响应流，避免连接悬挂到 GC。
      try {
        await reader.cancel()
      } catch {
        // 流已关闭：忽略。
      }
      try {
        reader.releaseLock()
      } catch {
        // 读取器已关闭：忽略。
      }
    }
  },
}
