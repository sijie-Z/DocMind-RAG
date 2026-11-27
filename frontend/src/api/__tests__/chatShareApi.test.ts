import { describe, it, expect, vi, beforeEach } from 'vitest'

const { get, post, del } = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), del: vi.fn(),
}))
vi.mock('@/utils/request', () => ({ default: { get, post, delete: del } }))

import {
  createConversationShare,
  getConversationShareStatus,
  getSharedConversation,
  revokeConversationShare,
} from '../share'

describe('chat share API contract', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('creates via POST /chat/conversations/{id}/share with expiry days', async () => {
    post.mockResolvedValue({
      data: { success: true, data: { token: 'tok', url: '/share/tok', expiresAt: null } },
    })
    const res = await createConversationShare('conv-1', 7)
    expect(post).toHaveBeenCalledWith('/chat/conversations/conv-1/share', {
      expires_in_days: 7,
    })
    expect(res.data?.data?.token).toBe('tok')

    // 缺省 = 永不过期：显式发送 null，与后端「null 永久」契约一致。
    await createConversationShare('conv-1')
    expect(post).toHaveBeenLastCalledWith('/chat/conversations/conv-1/share', {
      expires_in_days: null,
    })
  })

  it('reads status via GET and returns shared/expiry flags', async () => {
    get.mockResolvedValue({
      data: {
        success: true,
        data: { shared: true, createdAt: null, expiresAt: null, expired: false },
      },
    })
    const res = await getConversationShareStatus('conv-1')
    expect(get).toHaveBeenCalledWith('/chat/conversations/conv-1/share')
    expect(res.data?.data?.shared).toBe(true)
    expect(res.data?.data?.expiresAt).toBeNull()
    expect(res.data?.data?.expired).toBe(false)
  })

  it('revokes via DELETE /chat/conversations/{id}/share', async () => {
    del.mockResolvedValue({ data: { success: true, data: { shared: false } } })
    await revokeConversationShare('conv-1')
    expect(del).toHaveBeenCalledWith('/chat/conversations/conv-1/share')
  })

  it('public snapshot call is unauthenticated (silent error, encoded token)', async () => {
    get.mockResolvedValue({ data: { success: true, data: { messages: [] } } })
    await getSharedConversation('a/b+c')
    expect(get).toHaveBeenCalledWith('/chat/shares/a%2Fb%2Bc', {
      headers: { 'X-Silent-Error': '1' },
    })
  })
})
