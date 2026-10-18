/**
 * extractApiError 测试 —— 覆盖两种后端惯例（detail / message）、
 * FastAPI 422 数组、兜底链。
 *
 * getErrorMessage（E14/D1）——页面 catch 统一出口：
 * - 后端语义 detail/message 原文优先；
 * - 传输层 axios 文案（"Request failed with status code ..." / "Network Error"）
 *   禁止上屏，无后端语义时用调用方 i18n 兜底；
 * - 5xx 不展示后端内部细节（与响应拦截器同口径），用兜底文案。
 */
import { describe, expect, it } from 'vitest'
import { extractApiError, getErrorMessage } from '../apiError'

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

/** axios 错误形状（isAxiosError + config 齐备，与真实 axios reject 同构） */
function axiosError(status: number, data: unknown, message: string) {
  return Object.assign(new Error(message), {
    isAxiosError: true,
    config: { url: '/x' },
    response: status === 0 ? undefined : { status, data },
  })
}

describe('getErrorMessage（E14/D1 页面 catch 统一出口）', () => {
  it('4xx 后端 detail 原文优先上屏', () => {
    const err = axiosError(400, { detail: '文档不存在或已被删除' }, 'Request failed with status code 400')
    expect(getErrorMessage(err, '删除失败')).toBe('文档不存在或已被删除')
  })

  it('FastAPI 422 数组 detail 拼接', () => {
    const err = axiosError(422, { detail: [{ msg: '名称不能为空' }, { msg: '内容过长' }] }, 'Request failed with status code 422')
    expect(getErrorMessage(err, '保存失败')).toBe('名称不能为空; 内容过长')
  })

  it('axios 传输层文案禁止上屏：无后端 detail → i18n 兜底（不是 "Request failed with status code 500"）', () => {
    const err = axiosError(500, {}, 'Request failed with status code 500')
    expect(getErrorMessage(err, '操作失败，请稍后重试')).toBe('操作失败，请稍后重试')
  })

  it('5xx 不展示后端内部细节（与拦截器同口径）→ i18n 兜底', () => {
    const err = axiosError(500, { detail: 'Traceback: /srv/app/internal.py line 42' }, 'Request failed with status code 500')
    expect(getErrorMessage(err, '服务器内部错误，请稍后重试')).toBe('服务器内部错误，请稍后重试')
  })

  it('网络层 axios 错误（无 response）→ i18n 兜底（不是 "Network Error"）', () => {
    const err = axiosError(0, undefined, 'Network Error')
    expect(getErrorMessage(err, '网络连接失败，请稍后重试')).toBe('网络连接失败，请稍后重试')
  })

  it('非 axios 的普通 Error → message 可用', () => {
    expect(getErrorMessage(new Error('文件读取失败'), '兜底')).toBe('文件读取失败')
  })

  it('未知输入 → 兜底', () => {
    expect(getErrorMessage(null, '兜底文案')).toBe('兜底文案')
    expect(getErrorMessage(undefined, '兜底文案')).toBe('兜底文案')
    expect(getErrorMessage({ response: { data: {} } }, '兜底文案')).toBe('兜底文案')
  })
})
