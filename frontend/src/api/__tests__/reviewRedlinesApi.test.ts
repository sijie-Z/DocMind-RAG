/** 合同改稿留痕 API 契约：URL / 编码 / FormData / 静默头 / 请求体写错必须变红。 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

const { getMock, postMock, putMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  putMock: vi.fn()
}))

vi.mock('@/utils/request', () => ({
  default: { get: getMock, post: postMock, put: putMock }
}))

vi.unmock('@/api/reviews')

import { reviewsApi } from '@/api/reviews'

beforeEach(() => {
  vi.clearAllMocks()
  getMock.mockResolvedValue({ data: { success: true, data: {} } })
  postMock.mockResolvedValue({ data: { success: true, data: {} } })
  putMock.mockResolvedValue({ data: { success: true, data: {} } })
})

describe('reviewsApi.redlines · 端点契约', () => {
  it('upload：POST multipart file，覆盖 Content-Type 且静默全局错误', async () => {
    const file = new File(['revision text'], 'contract-v2.docx')
    const response = await reviewsApi.redlines.upload('case/1', file)

    expect(postMock).toHaveBeenCalledTimes(1)
    const [url, body, config] = postMock.mock.calls[0]
    expect(url).toBe('/reviews/case%2F1/redlines')
    expect(body).toBeInstanceOf(FormData)
    expect(body.get('file')).toBe(file)
    expect(config.headers).toEqual({ 'Content-Type': undefined, 'X-Silent-Error': '1' })
    expect(response).toEqual({ data: { success: true, data: {} } })
  })

  it('list：GET 留痕提交列表，caseId 编码', async () => {
    await reviewsApi.redlines.list('case/1')
    expect(getMock).toHaveBeenCalledWith('/reviews/case%2F1/redlines')
  })

  it('get：GET 台账明细，筛选/分页走 query，submissionId 编码', async () => {
    await reviewsApi.redlines.get('case/1', 'sub/2', {
      revision_type: 'insertion',
      disposition: 'pending',
      page: 2,
      page_size: 50
    })
    expect(getMock).toHaveBeenCalledWith('/reviews/case%2F1/redlines/sub%2F2', {
      params: {
        revision_type: 'insertion',
        disposition: 'pending',
        page: 2,
        page_size: 50
      }
    })
  })

  it('updateDisposition：PUT 事务性请求体（乐观锁版本原样透传）', async () => {
    const body = {
      disposition: 'reviewed' as const,
      note: 'checked',
      expected_lock_version: 7,
      item_id: 'item-1',
      evidence_id: 'ev-1'
    }
    await reviewsApi.redlines.updateDisposition('case/1', 'sub/2', 'row/3', body)

    expect(putMock).toHaveBeenCalledWith(
      '/reviews/case%2F1/redlines/sub%2F2/rows/row%2F3/disposition',
      body,
      { headers: { 'X-Silent-Error': '1' } }
    )
  })
})
