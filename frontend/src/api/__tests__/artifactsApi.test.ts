/** 工件 API 客户端契约：URL / 参数 / 编码 / 静默头 / 正文 fetch 形态。 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

const { getMock, postMock, deleteMock, fetchMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  deleteMock: vi.fn(),
  fetchMock: vi.fn(),
}))

vi.mock('@/utils/request', () => ({
  default: { get: getMock, post: postMock, delete: deleteMock },
}))

vi.mock('@/utils/auth', () => ({
  getToken: () => 'test-token',
}))

import { artifactsApi, publicArtifactsApi } from '@/api/artifacts'

beforeEach(() => {
  vi.clearAllMocks()
  getMock.mockResolvedValue({ data: { success: true, data: { items: [], total: 0 } } })
  postMock.mockResolvedValue({ data: { success: true, data: {} } })
  deleteMock.mockResolvedValue({ data: { success: true, data: {} } })
  globalThis.fetch = fetchMock as unknown as typeof fetch
  fetchMock.mockResolvedValue({ ok: true, text: async () => 'x' })
})

describe('artifactsApi · 端点契约', () => {
  it('list：GET /artifacts 并透传会话/任务/上限参数', async () => {
    await artifactsApi.list({ conversation_id: 'conv-1', task_id: 'task-1', limit: 50 })
    expect(getMock).toHaveBeenCalledWith('/artifacts', {
      params: { conversation_id: 'conv-1', task_id: 'task-1', limit: 50 },
    })
  })

  it('get：id 编码进路径', async () => {
    await artifactsApi.get('a/b')
    expect(getMock).toHaveBeenCalledWith('/artifacts/a%2Fb')
  })

  it('create：POST 载荷原样上送', async () => {
    const payload = { kind: 'markdown', title: 'T', content: '# C' }
    await artifactsApi.create(payload)
    expect(postMock).toHaveBeenCalledWith('/artifacts', payload)
  })

  it('fetchContent：带 Bearer 与静默头，返回原始 Response（不走全局 toast）', async () => {
    const res = await artifactsApi.fetchContent('a/b')
    expect(res.ok).toBe(true)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/v1/artifacts/a%2Fb/content')
    expect(init.headers).toMatchObject({
      Authorization: 'Bearer test-token',
      'X-Silent-Error': '1',
    })
  })

  it('createShare：POST /share + expires_in_days + 静默头', async () => {
    await artifactsApi.createShare('a/b', 30)
    expect(postMock).toHaveBeenCalledWith(
      '/artifacts/a%2Fb/share',
      { expires_in_days: 30 },
      { headers: { 'X-Silent-Error': '1' } },
    )
  })

  it('createShare：未选有效期时显式传 null（服务端理解为不过期）', async () => {
    await artifactsApi.createShare('art-1')
    expect(postMock).toHaveBeenCalledWith(
      '/artifacts/art-1/share',
      { expires_in_days: null },
      { headers: { 'X-Silent-Error': '1' } },
    )
  })

  it('getShare：GET 状态 + 静默头', async () => {
    await artifactsApi.getShare('art-1')
    expect(getMock).toHaveBeenCalledWith('/artifacts/art-1/share', {
      headers: { 'X-Silent-Error': '1' },
    })
  })

  it('revokeShare：DELETE + 静默头', async () => {
    await artifactsApi.revokeShare('art-1')
    expect(deleteMock).toHaveBeenCalledWith('/artifacts/art-1/share', {
      headers: { 'X-Silent-Error': '1' },
    })
  })

  it('publicArtifactsApi.get：token 编码进路径 + 静默头', async () => {
    await publicArtifactsApi.get('t/k')
    expect(getMock).toHaveBeenCalledWith('/public/artifacts/t%2Fk', {
      headers: { 'X-Silent-Error': '1' },
    })
  })

  it('publicArtifactsApi.fetchContent：匿名 fetch（无 Authorization）', async () => {
    const res = await publicArtifactsApi.fetchContent('t/k')
    expect(res.ok).toBe(true)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/v1/public/artifacts/t%2Fk/content')
    expect(init.headers).toEqual({ 'X-Silent-Error': '1' })
  })
})
