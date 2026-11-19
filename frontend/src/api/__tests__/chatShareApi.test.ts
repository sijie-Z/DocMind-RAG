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

  it('creates via POST /chat/conversations/{id}/share', async () => {
    post.mockResolvedValue({ data: { success: true, data: { token: 'tok', url: '/share/tok' } } })
    const res = await createConversationShare('conv-1')
    expect(post).toHaveBeenCalledWith('/chat/conversations/conv-1/share')
    expect(res.data?.data?.token).toBe('tok')
  })

  it('reads status via GET and returns shared flag', async () => {
    get.mockResolvedValue({ data: { success: true, data: { shared: true, createdAt: null } } })
    const res = await getConversationShareStatus('conv-1')
    expect(get).toHaveBeenCalledWith('/chat/conversations/conv-1/share')
    expect(res.data?.data?.shared).toBe(true)
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
