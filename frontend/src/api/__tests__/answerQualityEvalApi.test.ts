/** 答案质量评测 API 契约：URL / 参数 / 编码 / 长超时写错必须变红。 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const get = vi.fn()
const post = vi.fn()

vi.mock('@/utils/request', () => ({
  default: {
    get: (...args: unknown[]) => get(...args),
    post: (...args: unknown[]) => post(...args)
  }
}))

import {
  ANSWER_QUALITY_CREATE_TIMEOUT_MS,
  answerQualityEvalApi
} from '@/api/answerQualityEval'

beforeEach(() => {
  get.mockReset()
  post.mockReset()
  get.mockResolvedValue({ data: { success: true, data: {} } })
  post.mockResolvedValue({ data: { success: true, data: {} } })
})

describe('answerQualityEval API', () => {
  it('list 把筛选参数原样交给服务端，不做客户端过滤', async () => {
    await answerQualityEvalApi.list({
      status: 'failed',
      user_id: 7,
      date_from: '2026-11-01',
      date_to: '2026-11-25',
      page: 2,
      page_size: 10
    })
    expect(get).toHaveBeenCalledWith('/eval/answer-quality/runs', {
      params: {
        status: 'failed',
        user_id: 7,
        date_from: '2026-11-01',
        date_to: '2026-11-25',
        page: 2,
        page_size: 10
      }
    })
  })

  it('trend 走独立端点并透传同一组筛选与 limit', async () => {
    await answerQualityEvalApi.trend({ status: 'completed', user_id: 7, limit: 200 })
    expect(get).toHaveBeenCalledWith('/eval/answer-quality/runs/trend', {
      params: { status: 'completed', user_id: 7, limit: 200 }
    })
  })

  it('detail 对 run id 做 URL 编码', async () => {
    await answerQualityEvalApi.get('run/1')
    expect(get).toHaveBeenCalledWith('/eval/answer-quality/runs/run%2F1')
  })

  it('create 携带静默错误头与 120s 长超时（评委逐条调用模型）', async () => {
    const payload = {
      mode: 'fake' as const,
      cases: [
        {
          case_id: 'c1',
          question: 'q',
          answer: 'a',
          context: [],
          fake_scores: { faithfulness: 0.9, relevancy: 0.8, context_precision: 0.7 }
        }
      ]
    }
    await answerQualityEvalApi.create(payload)
    expect(post).toHaveBeenCalledWith('/eval/answer-quality/runs', payload, {
      headers: { 'X-Silent-Error': '1' },
      timeout: ANSWER_QUALITY_CREATE_TIMEOUT_MS
    })
    expect(ANSWER_QUALITY_CREATE_TIMEOUT_MS).toBe(120_000)
  })
})
