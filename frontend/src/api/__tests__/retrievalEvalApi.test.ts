/** 评测运行历史 API 契约：URL/参数/编码写错必须变红。 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const get = vi.fn()
const post = vi.fn()

vi.mock('@/utils/request', () => ({
  default: { get: (...args: unknown[]) => get(...args), post: (...args: unknown[]) => post(...args) }
}))

import {
  exportEvalRun,
  getEvalRun,
  getEvalTrend,
  listEvalRuns,
  runEval
} from '@/api/retrievalEval'

beforeEach(() => {
  get.mockReset()
  post.mockReset()
  get.mockResolvedValue({ data: { success: true, data: {} } })
  post.mockResolvedValue({ data: { success: true, data: {} } })
})

describe('retrievalEval API', () => {
  it('list 把 case_set_hash 交给服务端（不做客户端过滤的隐式截断）', async () => {
    await listEvalRuns({ page: 2, page_size: 10, case_set_hash: 'a'.repeat(64) })
    expect(get).toHaveBeenCalledWith('/knowledge/eval/runs', {
      params: { page: 2, page_size: 10, case_set_hash: 'a'.repeat(64) }
    })
  })

  it('detail/trend 走组织隔离端点', async () => {
    await getEvalRun('run/1')
    expect(get).toHaveBeenCalledWith('/knowledge/eval/runs/run%2F1')

    await getEvalTrend({ case_set_hash: 'b'.repeat(64), limit: 20 })
    expect(get).toHaveBeenCalledWith('/knowledge/eval/runs/trend', {
      params: { case_set_hash: 'b'.repeat(64), limit: 20 }
    })
  })

  it('默认评测不持久化；显式 persist 才写历史', async () => {
    await runEval({ case_ids: ['c1'], top_k: 5 })
    expect(post).toHaveBeenCalledWith('/knowledge/eval/run', { case_ids: ['c1'], top_k: 5 })

    await runEval({ case_ids: ['c1'], top_k: 5, persist: true })
    expect(post).toHaveBeenLastCalledWith('/knowledge/eval/run', {
      case_ids: ['c1'],
      top_k: 5,
      persist: true
    })
  })

  it('导出固定 format 参数并以 blob 接收', async () => {
    await exportEvalRun('run-1', 'csv')
    expect(get).toHaveBeenCalledWith('/knowledge/eval/runs/run-1/export', {
      params: { format: 'csv' },
      responseType: 'blob'
    })
  })
})
