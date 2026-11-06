/**
 * getDocumentChunk 的 URL/参数契约：路径写错或 chunk_id 参数名漂移必须变红。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const get = vi.fn()

vi.mock('@/utils/request', () => ({
  default: { get: (...args: unknown[]) => get(...args) }
}))

import { getDocumentChunk } from '@/api/knowledge'

beforeEach(() => {
  get.mockReset()
  get.mockResolvedValue({ data: { success: true, data: {} } })
})

describe('getDocumentChunk 契约', () => {
  it('请求 /documents/{id}/chunk 且 chunk_id 走 query 参数', async () => {
    await getDocumentChunk('doc-1', 'doc-1_chunk-1', { silent: true })

    expect(get).toHaveBeenCalledWith('/documents/doc-1/chunk', {
      params: { chunk_id: 'doc-1_chunk-1' },
      headers: { 'X-Silent-Error': '1' }
    })
  })

  it('非静默调用不附加 X-Silent-Error', async () => {
    await getDocumentChunk('doc-2', 'chunk-2')

    expect(get).toHaveBeenCalledWith('/documents/doc-2/chunk', {
      params: { chunk_id: 'chunk-2' }
    })
  })
})
