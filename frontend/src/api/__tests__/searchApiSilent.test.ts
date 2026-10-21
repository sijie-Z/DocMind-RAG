/**
 * E14/D2 判别用例：searchKnowledge 必须带 X-Silent-Error。
 *
 * 搜索页失败时改为页面内联错误态（含重试）呈现；若请求不带 silent 头，
 * 响应拦截器会再弹一条全局 toast —— 同一失败出现两个提示表面。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

const { postMock } = vi.hoisted(() => ({ postMock: vi.fn() }))

vi.mock('@/utils/request', () => ({
  default: { post: postMock },
  FIRST_SCREEN_REQUEST_CONFIG: {},
}))

import { searchKnowledge } from '../search'

beforeEach(() => {
  postMock.mockReset()
  postMock.mockResolvedValue({ data: { results: [] } })
})

describe('search API · 错误提示单一表面', () => {
  it('searchKnowledge 请求带 X-Silent-Error: 1（失败由页面内联错误态呈现）', async () => {
    await searchKnowledge({ query: 'q', organization_id: 1 })
    expect(postMock).toHaveBeenCalledTimes(1)
    const [url, body, config] = postMock.mock.calls[0]
    expect(url).toBe('/knowledge/search')
    expect(body).toEqual({ query: 'q', organization_id: 1 })
    expect(config?.headers?.['X-Silent-Error']).toBe('1')
  })
})
