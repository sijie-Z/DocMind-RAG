import { describe, it, expect } from 'vitest'
import { notificationTargetLocation } from '@/utils/notificationTarget'

describe('notificationTargetLocation', () => {
  it('知识库通知带 target_id → 文档深链', () => {
    expect(
      notificationTargetLocation({
        target_route: 'Knowledge',
        target_id: 'doc-42',
        type: 'knowledge',
      }),
    ).toEqual({ name: 'KnowledgeDocument', params: { documentId: 'doc-42' } })
  })

  it('知识库通知无 target_id → 知识库列表（不拼空 id）', () => {
    expect(notificationTargetLocation({ target_route: 'Knowledge' })).toEqual({
      name: 'Knowledge',
      query: undefined,
    })
  })

  it('其它 target_route 保持旧口径：路由名 + id 查询参数', () => {
    expect(
      notificationTargetLocation({ target_route: 'Agent', target_id: 7 }),
    ).toEqual({ name: 'Agent', query: { id: '7' } })
  })

  it('无 target_route 时按类型兜底', () => {
    expect(notificationTargetLocation({ type: 'knowledge' })).toEqual({ name: 'Knowledge' })
    expect(notificationTargetLocation({ type: 'chat' })).toEqual({ name: 'Chat' })
    expect(notificationTargetLocation({ type: 'account' })).toEqual({ name: 'Profile' })
    expect(notificationTargetLocation({ type: 'agent' })).toEqual({ name: 'Agent' })
    expect(notificationTargetLocation({ type: 'unknown' })).toEqual({ name: 'Notifications' })
  })
})
