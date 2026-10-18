/**
 * E9 上传取消语义 · API 层判别用例（B7）。
 *
 * 判别点：
 * - uploadKnowledgeBase 必须把第三参 AbortSignal 透传给 axios 配置（视图据此真取消）；
 * - 上传请求带 X-Silent-Error 头：用户取消时 axios 以「无 response 的 reject」收场，
 *   请求拦截器不再误报全局「网络连接失败」（上传错误由调用方自行呈现）。
 *
 * 变异点：删掉 signal 透传 / 删掉静默头 → 对应用例红。
 */
import { describe, it, expect, vi } from 'vitest'

const { post } = vi.hoisted(() => ({
  post: vi.fn().mockResolvedValue({ data: { success: true } }),
}))
vi.mock('@/utils/request', () => ({ default: { post } }))

import { uploadKnowledgeBase } from '../knowledge'

describe('uploadKnowledgeBase · 取消上传支持（E9/B7）', () => {
  it('第三参 AbortSignal 透传给 axios 配置；进度回调保留', async () => {
    const controller = new AbortController()
    const onProgress = vi.fn()
    const formData = new FormData()

    await uploadKnowledgeBase(formData, onProgress, controller.signal)

    expect(post).toHaveBeenCalledTimes(1)
    const [url, data, config] = post.mock.calls[0] as [string, FormData, Record<string, unknown>]
    expect(url).toBe('/documents/upload')
    expect(data).toBe(formData)
    expect(config.signal).toBe(controller.signal)
    expect(config.onUploadProgress).toBe(onProgress)
  })

  it('上传请求标记静默错误：取消不再触发全局「网络连接失败」误报', async () => {
    await uploadKnowledgeBase(new FormData(), undefined, new AbortController().signal)

    const config = post.mock.calls[0][2] as { headers: Record<string, unknown> }
    expect(config.headers['X-Silent-Error']).toBe('1')
  })
})
