/** 工件 API 客户端契约：URL / 参数 / 编码 / 静默头 / 正文 fetch 形态。 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

const { getMock, postMock, fetchMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  fetchMock: vi.fn(),
}))

vi.mock('@/utils/request', () => ({
  default: { get: getMock, post: postMock },
}))

vi.mock('@/utils/auth', () => ({
  getToken: () => 'test-token',
}))

import { artifactsApi } from '@/api/artifacts'

beforeEach(() => {
  vi.clearAllMocks()
  getMock.mockResolvedValue({ data: { success: true, data: { items: [], total: 0 } } })
  postMock.mockResolvedValue({ data: { success: true, data: {} } })
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
})
