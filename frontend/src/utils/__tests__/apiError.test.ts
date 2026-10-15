/**
 * extractApiError 测试 —— 覆盖两种后端惯例（detail / message）、
 * FastAPI 422 数组、兜底链。
 */
import { describe, expect, it } from 'vitest'
import { extractApiError } from '../apiError'

describe('extractApiError', () => {
  it('优先 data.detail（端点 detail 传文案惯例）', () => {
    const err = { response: { data: { detail: '当前用户未加入任何组织', message: '权限不足' } } }
    expect(extractApiError(err, '兜底')).toBe('当前用户未加入任何组织')
  })

  it('detail 为空时回落 data.message（AppError 首参惯例）', () => {
    const err = { response: { data: { detail: null, message: '需要管理员权限' } } }
    expect(extractApiError(err, '兜底')).toBe('需要管理员权限')
  })

  it('FastAPI 422 数组：拼接 msg 字段', () => {
    const err = {
      response: { data: { detail: [{ msg: 'interval_seconds 不能小于 60' }, { msg: '名称过长' }] } },
    }
    expect(extractApiError(err, '兜底')).toBe('interval_seconds 不能小于 60; 名称过长')
  })

  it('无 response（网络层错误）→ Error.message', () => {
    expect(extractApiError(new Error('Network Error'), '兜底')).toBe('Network Error')
  })

  it('全缺 → fallback', () => {
    expect(extractApiError(null, '兜底文案')).toBe('兜底文案')
    expect(extractApiError({ response: { data: {} } }, '兜底文案')).toBe('兜底文案')
    expect(extractApiError({ response: { data: { detail: '   ' } } }, '兜底文案')).toBe('兜底文案')
  })
})
