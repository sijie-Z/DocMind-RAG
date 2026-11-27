/**
 * 公开知识库客户端契约（批次十三）。
 *
 * 钉住：管理面/公开面 URL、silent header（不弹全局 toast）、apiUnwrap 解包、
 * 轮换请求的 expires_in_days 透传。公开面不发送 Authorization 由 request
 * 拦截器按登录态决定；token 是唯一权限凭证。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

const { getMock, postMock } = vi.hoisted(() => ({ getMock: vi.fn(), postMock: vi.fn() }))

vi.mock('@/utils/request', () => ({
  default: { get: getMock, post: postMock },
}))

import { publicKbApi, publicKbPublicApi } from '../publicKb'

const publication = {
  id: 'pub-1',
  knowledge_base_id: 'kb-1',
  knowledge_base_name: '合同库',
  title: '公开制度库',
  description: null,
  enabled: true,
  active: true,
  expires_at: null,
  revoked_at: null,
  created_at: '2026-11-27T10:00:00Z',
  document_count: 2,
}

function response(payload: unknown) {
  return { data: { success: true, data: payload }, status: 200, statusText: 'OK', headers: {}, config: {} }
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
