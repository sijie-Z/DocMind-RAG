/**
 * User Commands / Session Fork API 封装契约（#475 #476）。
 *
 * 视图层测试都 mock 了 `@/api/*` 模块，URL/方法/静默错误头这类"线上契约"
 * 无人验证；本文件直接 mock axios 实例（`@/utils/request` 默认导出），钉住：
 * - GET /commands（带 session_id 作用域参数）
 * - POST /commands/execute {line, session_id}（X-Silent-Error，错误由调用方呈现）
 * - POST /sessions/{id}/fork（契约 body 透传）
 * - GET /sessions/{id}/lineage
 *
 * 变异点：URL/方法/参数名/静默头任一被改 → 对应用例红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

const { getMock, postMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
}))

vi.mock('@/utils/request', () => ({
  default: { get: getMock, post: postMock },
}))

// test-setup.ts 为未显式 mock 的视图测试提供了 @/api/commands 的空清单桩；
// 本文件要验证真实封装实现，显式解除该全局 mock。
vi.unmock('@/api/commands')

import { commandsApi } from '@/api/commands'
import { sessionsApi } from '@/api/sessions'

describe('commandsApi · 端点契约', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('list：GET /commands；有会话时带 session_id 查询参数', async () => {
    getMock.mockResolvedValue({ data: { success: true, data: {} } })
    await commandsApi.list('s-1')
    expect(getMock).toHaveBeenCalledWith('/commands', { params: { session_id: 's-1' } })

    await commandsApi.list()
    expect(getMock).toHaveBeenLastCalledWith('/commands', { params: undefined })
  })

  it('execute：POST /commands/execute，line 原样传递、会话可为空、错误由调用方呈现', async () => {
    postMock.mockResolvedValue({ data: { success: true, data: {} } })
    await commandsApi.execute('/export json', 's-1')
    expect(postMock).toHaveBeenCalledWith(
      '/commands/execute',
      { line: '/export json', session_id: 's-1' },
      { headers: { 'X-Silent-Error': '1' } },
    )

    await commandsApi.execute('/help')
    expect(postMock).toHaveBeenLastCalledWith(
      '/commands/execute',
      { line: '/help', session_id: undefined },
      { headers: { 'X-Silent-Error': '1' } },
    )
  })
})

describe('sessionsApi · 端点契约', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('fork：POST /sessions/{id}/fork，边界 message_id 透传', async () => {
    postMock.mockResolvedValue({ data: { success: true, data: {} } })
    await sessionsApi.fork('s-1', { message_id: 'm3' })
    expect(postMock).toHaveBeenCalledWith(
      '/sessions/s-1/fork',
      { message_id: 'm3' },
      { headers: { 'X-Silent-Error': '1' } },
    )
  })

  it('lineage：GET /sessions/{id}/lineage（静默错误，由视图决定展示）', async () => {
    getMock.mockResolvedValue({ data: { success: true, data: {} } })
    await sessionsApi.lineage('s-1')
    expect(getMock).toHaveBeenCalledWith('/sessions/s-1/lineage', {
      headers: { 'X-Silent-Error': '1' },
    })
  })
})
