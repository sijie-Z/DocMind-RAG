/**
 * GET /api/v1/knowledge-bases/ 客户端契约（竞品批次六 feature A）。
 *
 * 钉住三件事：trailing slash（避免 307 丢 Header）、apiUnwrap 双层信封、
 * silent 选项透传 X-Silent-Error。客户端不传 organization_id——组织由服务端推导。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

const { getMock } = vi.hoisted(() => ({ getMock: vi.fn() }))

vi.mock('@/utils/request', () => ({
  default: { get: getMock, post: vi.fn() },
}))

import { listKnowledgeBases } from '../knowledgeBases'

const body = {
  success: true,
  data: {
    data: [
      { id: 'kb-1', organization_id: 3, name: '合同库', description: null, created_at: '2026-01-01T00:00:00Z' },
    ],
    total: 1,
    page: 1,
    page_size: 100,
  },
}

beforeEach(() => {
  getMock.mockReset()
  getMock.mockResolvedValue({ data: body, status: 200, statusText: 'OK', headers: {}, config: {} })
})

describe('knowledgeBases API', () => {
  it('请求 /knowledge-bases/（trailing slash）且不带组织 id', async () => {
    const result = await listKnowledgeBases({ search: ' 合同 ', page: 2, pageSize: 25 })
    expect(getMock).toHaveBeenCalledTimes(1)
    const [url, config] = getMock.mock.calls[0]
    expect(url).toBe('/knowledge-bases/')
    expect(config.params).toEqual({ page: 2, page_size: 25, search: '合同' })
    expect(config.params.organization_id).toBeUndefined()
    expect(result).toEqual(body.data)
  })

  it('apiUnwrap 双层信封 → 分页体（data/total/page/page_size 保留）', async () => {
    const result = await listKnowledgeBases()
    expect(result.total).toBe(1)
    expect(result.data[0].name).toBe('合同库')
    expect(result.page).toBe(1)
    expect(result.page_size).toBe(100)
    expect(getMock.mock.calls[0][1].params).toEqual({ page: 1, page_size: 100 })
  })

  it('真实单层信封 {success, data:[...], total} 也归一到分页体', async () => {
    getMock.mockResolvedValueOnce({
      data: { success: true, data: body.data.data, total: 7, page: 3, page_size: 10 },
      status: 200, statusText: 'OK', headers: {}, config: {},
    })
    const result = await listKnowledgeBases()
    expect(result.data).toEqual(body.data.data)
    expect(result.total).toBe(7)
    expect(result.page).toBe(3)
    expect(result.page_size).toBe(10)
  })

  it('silent 选项透传 X-Silent-Error，默认不发', async () => {
    await listKnowledgeBases({ silent: true })
    expect(getMock.mock.calls[0][1].headers).toEqual({ 'X-Silent-Error': '1' })
    await listKnowledgeBases()
    expect(getMock.mock.calls[1][1].headers).toBeUndefined()
  })
})
