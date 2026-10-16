/**
 * apiUnwrap 宽容解包单测（T11 · 方案 B）。
 *
 * 覆盖成功路径三形态（口径见 types/common.ts）：
 *   ① 裸结构（响应体即业务体，如 GET /notifications/）
 *   ② {data}（至多一层 data，如 GET /documents/{id}）
 *   ③ {data:{data}}（外皮 + 分页体，如 GET /api/v1/knowledge/）
 * 边界：data 为数组 / data 为对象 / success=false / message-only / 多键分页体
 * 不误剥 / 至多剥两层 / 非对象输入原样。
 *
 * 样例形状来源：backend/app/api/v1/endpoints/{notifications,documents,knowledge}.py
 * 的 return 语句逐字（2026-10-15 复核）。
 */
import { describe, it, expect } from 'vitest'
import { apiUnwrap } from '../apiUnwrap'

/** 真机响应对象的最小化身（axios 响应挂 status/statusText/headers）。 */
const respOf = (body: unknown) => ({ data: body, status: 200, statusText: 'OK', headers: {}, config: {} })

// ── 三形态样例（形状逐字对齐后端源码） ──
const NOTIFICATION_LIST = {
  items: [{ id: 1, title: '欢迎', content: '…', type: 'system', is_read: false, created_at: '2026-10-15T00:00:00' }],
  total: 1,
  unread_count: 1,
  skip: 0,
  limit: 20,
}
const DOCUMENT_BODY = {
  success: true,
  data: { id: 'doc-1', title: 'architecture.md', filename: 'architecture.md', file_size: 1578, file_type: 'word', status: 'indexed' },
}
const KNOWLEDGE_LIST_BODY = {
  success: true,
  message: '获取成功',
  data: { data: [{ id: 'doc-1' }], total: 1, page: 1, page_size: 10 },
}

describe('apiUnwrap · 形态① 裸结构（原样透传）', () => {
  it('真实响应对象 → 业务体同一引用（不克隆、不误剥）', () => {
    expect(apiUnwrap(respOf(NOTIFICATION_LIST))).toBe(NOTIFICATION_LIST)
  })

  it('直接传载荷（无响应对象包裹）同样原样', () => {
    expect(apiUnwrap(NOTIFICATION_LIST)).toBe(NOTIFICATION_LIST)
  })

  it('裸数组输入原样返回', () => {
    const arr = [{ id: 1 }]
    expect(apiUnwrap(arr)).toBe(arr)
  })
})

describe('apiUnwrap · 形态② {data}', () => {
  it('真实响应对象（含 status/headers）→ 业务体', () => {
    expect(apiUnwrap(respOf(DOCUMENT_BODY))).toBe(DOCUMENT_BODY.data)
  })

  it('最小假响应 {data: body}（既有测试 mock 手法）同样可解', () => {
    expect(apiUnwrap({ data: DOCUMENT_BODY })).toBe(DOCUMENT_BODY.data)
  })

  it('直接传载荷 {success, data} → 业务体', () => {
    expect(apiUnwrap(DOCUMENT_BODY)).toBe(DOCUMENT_BODY.data)
  })
})

describe('apiUnwrap · 形态③ {data:{data}}', () => {
  it('knowledge 列表真实响应 → 分页体（total/page/page_size 保留）', () => {
    expect(apiUnwrap(respOf(KNOWLEDGE_LIST_BODY))).toBe(KNOWLEDGE_LIST_BODY.data)
  })

  it('最小假响应 {data: body} 同样解到分页体', () => {
    expect(apiUnwrap({ data: KNOWLEDGE_LIST_BODY })).toBe(KNOWLEDGE_LIST_BODY.data)
  })

  it('直接传载荷 → 分页体', () => {
    expect(apiUnwrap(KNOWLEDGE_LIST_BODY)).toBe(KNOWLEDGE_LIST_BODY.data)
  })
})

describe('apiUnwrap · 边界', () => {
  it('data 为数组：{data:[…]} 与 {success, data:[…]} 都解到数组本体', () => {
    const arr = [1, 2, 3]
    expect(apiUnwrap({ data: arr })).toBe(arr)
    expect(apiUnwrap({ success: true, data: arr })).toBe(arr)
  })

  it('data 为对象：{data:{…}} 解到对象本体', () => {
    const obj = { id: 1 }
    expect(apiUnwrap({ data: obj })).toBe(obj)
  })

  it('success=false（错误信封）：解包到 data（null），不抛错', () => {
    const err = { success: false, code: 404, message: '不存在', detail: 'x', data: null, request_id: 'r-1' }
    expect(apiUnwrap(err)).toBeNull()
  })

  it('success=false 且无 data 键：原样返回（宽容，不抛错）', () => {
    const err = { success: false, message: 'boom', detail: 'd' }
    expect(apiUnwrap(err)).toBe(err)
  })

  it('message-only 回执：原样返回，不误剥', () => {
    const ack = { message: '标记成功' }
    const ack2 = { message: '全部已读', updated: 2 }
    expect(apiUnwrap(ack)).toBe(ack)
    expect(apiUnwrap(ack2)).toBe(ack2)
  })

  it('多键分页体 {data, total, …}（无标记）不误剥——它本身就是业务体', () => {
    const pageBody = { data: [1], total: 1, page: 1, page_size: 10 }
    expect(apiUnwrap(pageBody)).toBe(pageBody)
  })

  it('至多剥两层：{data:{data:{data:X}}} 停在第二层，不无限剥', () => {
    const lvl3 = { data: [9] }
    const lvl2 = { data: lvl3 }
    const lvl1 = { data: lvl2 }
    expect(apiUnwrap(lvl1)).toBe(lvl3)
  })

  it('null / undefined / 原始值原样返回', () => {
    expect(apiUnwrap(null)).toBeNull()
    expect(apiUnwrap(undefined)).toBeUndefined()
    expect(apiUnwrap(42)).toBe(42)
    expect(apiUnwrap('x')).toBe('x')
  })
})
