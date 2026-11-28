/**
 * 公开知识库客户端契约（批次十三/十七）。
 *
 * 钉住：管理面/公开面 URL、silent header（不弹全局 toast）、apiUnwrap 解包、
 * 轮换请求的 expires_in_days 透传、聊天设置的开关/预算透传，以及匿名聊天的
 * POST + fetch-SSE 帧解析（sources/delta/done/error）与 429 错误形状。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const { getMock, postMock } = vi.hoisted(() => ({ getMock: vi.fn(), postMock: vi.fn() }))

vi.mock('@/utils/request', () => ({
  default: { get: getMock, post: postMock },
}))

import { publicKbApi, publicKbPublicApi, PublicKbChatError } from '../publicKb'

const publication = {
  id: 'pub-1',
  knowledge_base_id: 'kb-1',
  knowledge_base_name: '合同库',
  title: '公开制度库',
  description: null,
  enabled: true,
  active: true,
  chat_enabled: true,
  daily_token_budget: 1000,
  expires_at: null,
  revoked_at: null,
  created_at: '2026-11-27T10:00:00Z',
  document_count: 2,
}

function response(payload: unknown) {
  return { data: { success: true, data: payload }, status: 200, statusText: 'OK', headers: {}, config: {} }
}

function makeStreamResponse(chunks: string[]) {
  const encoder = new TextEncoder()
  let i = 0
  const cancel = vi.fn(async () => undefined)
  const releaseLock = vi.fn()
  return {
    response: {
      ok: true,
      status: 200,
      body: {
        getReader: () => ({
          read: async () =>
            i < chunks.length
              ? { done: false, value: encoder.encode(chunks[i++]) }
              : { done: true, value: undefined },
          cancel,
          releaseLock,
        }),
      },
    },
    cancel,
    releaseLock,
  }
}

beforeEach(() => {
  getMock.mockReset()
  postMock.mockReset()
  getMock.mockResolvedValue(response([publication]))
  postMock.mockResolvedValue(response({ ...publication, token: 'tok-1', url: '/public/kb/tok-1' }))
})

describe('publicKb admin API', () => {
  it('list → GET /kb-publications（可选 kb 过滤，silent）', async () => {
    const rows = await publicKbApi.list('kb-1')
    expect(getMock).toHaveBeenCalledWith('/kb-publications', {
      params: { knowledge_base_id: 'kb-1' },
      headers: { 'X-Silent-Error': '1' },
    })
    expect(rows[0].title).toBe('公开制度库')
  })

  it('create → POST /kb-publications 并解包 token/url', async () => {
    const created = await publicKbApi.create({
      knowledge_base_id: 'kb-1',
      title: '公开制度库',
      description: null,
      expires_in_days: 7,
      document_ids: ['doc-a'],
    })
    expect(postMock).toHaveBeenCalledWith(
      '/kb-publications',
      {
        knowledge_base_id: 'kb-1',
        title: '公开制度库',
        description: null,
        expires_in_days: 7,
        document_ids: ['doc-a'],
      },
      { headers: { 'X-Silent-Error': '1' } },
    )
    expect(created.token).toBe('tok-1')
    expect(created.url).toBe('/public/kb/tok-1')
  })

  it('rotate 透传 expires_in_days（null = 保持服务端原有效期）且 URL 编码 id', async () => {
    await publicKbApi.rotate('pub/1', null)
    expect(postMock).toHaveBeenCalledWith(
      '/kb-publications/pub%2F1/rotate',
      { expires_in_days: null },
      { headers: { 'X-Silent-Error': '1' } },
    )
  })

  it('documents / candidates / setDocuments / revoke 的 URL 与 body', async () => {
    getMock.mockResolvedValueOnce(response({ documents: [], document_count: 0 }))
    await publicKbApi.documents('pub-1')
    expect(getMock).toHaveBeenLastCalledWith(
      '/kb-publications/pub-1/documents',
      { headers: { 'X-Silent-Error': '1' } },
    )

    getMock.mockResolvedValueOnce(response({ documents: [], truncated: false }))
    await publicKbApi.candidates('kb-1')
    expect(getMock).toHaveBeenLastCalledWith('/kb-publications/candidates', {
      params: { knowledge_base_id: 'kb-1' },
      headers: { 'X-Silent-Error': '1' },
    })

    postMock.mockResolvedValueOnce(response({ documents: [], document_count: 0 }))
    await publicKbApi.setDocuments('pub-1', 'replace', ['doc-a'])
    expect(postMock).toHaveBeenLastCalledWith(
      '/kb-publications/pub-1/documents',
      { mode: 'replace', document_ids: ['doc-a'] },
      { headers: { 'X-Silent-Error': '1' } },
    )

    await publicKbApi.revoke('pub-1')
    expect(postMock).toHaveBeenLastCalledWith(
      '/kb-publications/pub-1/revoke',
      {},
      { headers: { 'X-Silent-Error': '1' } },
    )
  })

  it('setChatSettings → POST chat-settings，透传开关与预算（null = 全局默认）', async () => {
    postMock.mockResolvedValueOnce(response({ ...publication, chat_enabled: false, daily_token_budget: null }))
    const updated = await publicKbApi.setChatSettings('pub-1', {
      chat_enabled: false,
      daily_token_budget: null,
    })
    expect(postMock).toHaveBeenLastCalledWith(
      '/kb-publications/pub-1/chat-settings',
      { chat_enabled: false, daily_token_budget: null },
      { headers: { 'X-Silent-Error': '1' } },
    )
    expect(updated.chat_enabled).toBe(false)
    expect(updated.daily_token_budget).toBeNull()
  })
})

describe('publicKb anonymous API', () => {
  it('get/search 走 /public/kb/{token}，token URL 编码，silent', async () => {
    getMock.mockResolvedValueOnce(
      response({ title: '公开库', description: null, expires_at: null }),
    )
    await publicKbPublicApi.get('tok/1')
    expect(getMock).toHaveBeenLastCalledWith('/public/kb/tok%2F1', {
      headers: { 'X-Silent-Error': '1' },
    })

    postMock.mockResolvedValueOnce(response({ results: [], total: 0 }))
    await publicKbPublicApi.search('tok-1', '制度', 10)
    expect(postMock).toHaveBeenLastCalledWith(
      '/public/kb/tok-1/search',
      { query: '制度', top_k: 10 },
      { headers: { 'X-Silent-Error': '1' } },
    )
  })
})

describe('publicKb anonymous chat (batch 17)', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('POST + fetch-SSE：跨 chunk 分帧、sources/delta/done 逐帧回调', async () => {
    const stream = makeStreamResponse([
      'event: sources\ndata: {"sources":[{"rank":1,"title":"制度 A","snippet":"正文","score":0.9}]}\n\n',
      'event: del',
      'ta\ndata: {"delta":"回答"}\n\nevent: delta\ndata: {"delta":"完毕"}\n\n',
      'event: done\ndata: {"done":true,"source_count":1}\n\n',
      // done 之后的尾随帧必须被忽略。
      'event: delta\ndata: {"delta":"不应出现"}\n\n',
    ])
    const fetchMock = vi.fn().mockResolvedValue(stream.response)
    vi.stubGlobal('fetch', fetchMock)
    const sources: unknown[] = []
    const deltas: string[] = []
    const done: unknown[] = []
    const controller = new AbortController()

    await publicKbPublicApi.chat(
      'tok/1',
      { message: '制度？', history: [{ role: 'user', content: '上一问' }] },
      {
        onSources: (value) => sources.push(value),
        onDelta: (value) => deltas.push(value),
        onDone: (value) => done.push(value),
      },
      { signal: controller.signal },
    )

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/v1/public/kb/tok%2F1/chat')
    expect(init.method).toBe('POST')
    expect(init.headers.Accept).toBe('text/event-stream')
    expect(init.signal).toBeInstanceOf(AbortSignal)
    expect(JSON.parse(init.body)).toEqual({
      message: '制度？',
      history: [{ role: 'user', content: '上一问' }],
      stream: true,
    })
    expect(sources).toHaveLength(1)
    expect((sources[0] as Array<{ title: string }>)[0].title).toBe('制度 A')
    expect(deltas).toEqual(['回答', '完毕'])
    expect(done).toEqual([{ done: true, source_count: 1 }])
    expect(stream.cancel).toHaveBeenCalled()
  })

  it('HTTP 429 预算错误 → PublicKbChatError(code/detail/status)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 429,
        json: async () => ({
          error_code: 'PUBLIC_KB_CHAT_BUDGET_EXCEEDED',
          message: 'budget',
          detail: { used: 5, budget: 5, reset_at: '2026-11-29T00:00:00+00:00' },
        }),
      }),
    )
    await expect(
      publicKbPublicApi.chat('tok-1', { message: '制度' }, {}),
    ).rejects.toMatchObject({
      name: 'PublicKbChatError',
      code: 'PUBLIC_KB_CHAT_BUDGET_EXCEEDED',
      status: 429,
      detail: { used: 5, budget: 5 },
    })
  })

  it('SSE error 事件 → onError（不是静默结束）', async () => {
    const stream = makeStreamResponse([
      'event: sources\ndata: {"sources":[]}\n\n',
      'event: error\ndata: {"error_code":"PUBLIC_KB_CHAT_GENERATION_FAILED","message":"failed"}\n\n',
    ])
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(stream.response))
    const errors: PublicKbChatError[] = []
    await publicKbPublicApi.chat('tok-1', { message: '制度' }, {
      onError: (error) => errors.push(error),
    })
    expect(errors).toHaveLength(1)
    expect(errors[0].code).toBe('PUBLIC_KB_CHAT_GENERATION_FAILED')
  })

  it('CRLF 帧边界同样可解析（代理重写不吞事件）', async () => {
    const stream = makeStreamResponse([
      'event: sources\r\ndata: {"sources":[]}\r\n\r\n',
      'event: done\r\ndata: {"done":true}\r\n\r\n',
    ])
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(stream.response))
    const events: string[] = []
    await publicKbPublicApi.chat('tok-1', { message: '制度' }, {
      onSources: () => events.push('sources'),
      onDone: () => events.push('done'),
    })
    expect(events).toEqual(['sources', 'done'])
  })

  it('没有 done/error 的截断流 → STREAM_INCOMPLETE（不伪装成完整回答）', async () => {
    const stream = makeStreamResponse(['event: delta\ndata: {"delta":"半句"}\n\n'])
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(stream.response))
    await expect(
      publicKbPublicApi.chat('tok-1', { message: '制度' }, {}),
    ).rejects.toMatchObject({ code: 'STREAM_INCOMPLETE' })
  })

  it('data: null 等非法帧不会抛 TypeError，最终按截断流处理', async () => {
    const stream = makeStreamResponse([
      'event: delta\ndata: null\n\n',
      'event: sources\ndata: [1,2,3]\n\n',
    ])
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(stream.response))
    await expect(
      publicKbPublicApi.chat('tok-1', { message: '制度' }, {}),
    ).rejects.toMatchObject({ code: 'STREAM_INCOMPLETE' })
  })
})

describe('publicKb anonymous API token hygiene (batch 18 review M2)', () => {
  it('公开 get/search 必带 X-Silent-Error，防止 request.ts 错误日志泄露含 token URL', async () => {
    await publicKbPublicApi.get('tok-secret')
    expect(getMock).toHaveBeenLastCalledWith('/public/kb/tok-secret', {
      headers: { 'X-Silent-Error': '1' },
    })

    await publicKbPublicApi.search('tok-secret', 'query', 5)
    expect(postMock).toHaveBeenLastCalledWith(
      '/public/kb/tok-secret/search',
      { query: 'query', top_k: 5 },
      { headers: { 'X-Silent-Error': '1' } },
    )
  })
})
