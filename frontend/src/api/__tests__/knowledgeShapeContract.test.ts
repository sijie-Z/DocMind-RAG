/**
 * knowledge 列表响应形状契约（T11 · 方案 B）。
 *
 * 钉住事实：GET /api/v1/knowledge/ 为形态③双层——
 * { success, message, data: { data: [...], total, page, page_size } }。
 * 取样方式：backend/app/api/v1/endpoints/knowledge.py:170-179 源码逐字（2026-10-15 复核），
 * 与当日真机实测「data.data 双层」一致；分页体形状同时对齐前端声明
 * knowledge.ts 的 KnowledgeBaseListResponse。
 *
 * 兼容面：apiUnwrap(resp) 必须解一层到分页体（total/page/page_size 保留、不再多剥），
 * 且与既有视图读取路径 response.data.data（旧）恒等——知识库视图 3 处调用点的
 * 迁移正是建立在这个等价上（见 views/knowledge/index.vue）。
 */
import { describe, it, expect } from 'vitest'
import { apiUnwrap } from '@/utils/apiUnwrap'
import { getKnowledgeBases } from '@/api/knowledge'
import type { KnowledgeBase, KnowledgeBaseListResponse } from '@/api/knowledge'

/** 真机响应对象的最小化身（axios 响应挂 status/statusText/headers）。 */
const respOf = <B>(body: B) => ({ data: body, status: 200, statusText: 'OK', headers: {}, config: {} })

// 形状逐字对齐 knowledge.py:155-168（可空字段保留真形；raw_status 为词表收敛期冗余键）
const REAL_DOC_DATA = {
  id: 'doc-1',
  title: 'architecture.md',
  file_name: 'architecture.md',
  file_type: 'word',
  file_size: 1578,
  tags: ['技术文档'],
  description: null,
  parse_error: null,
  created_at: '2026-10-08T05:29:00',
  updated_at: '2026-10-08T05:30:00',
  status: 'indexed' as const,
  raw_status: 'indexed',
}

// 形状逐字对齐 knowledge.py:170-179
const REAL_BODY = {
  success: true,
  message: '获取成功',
  data: { data: [REAL_DOC_DATA], total: 1, page: 1, page_size: 10 },
}

describe('knowledge 列表响应形状契约 · 形态③ {data:{data}}', () => {
  it('模块导出面 + 核心字段锚点：列表项含 KnowledgeBase 必需字段（编译期+运行期）', () => {
    expect(typeof getKnowledgeBases).toBe('function')
    const docPin: Pick<KnowledgeBase, 'id' | 'title' | 'file_name' | 'file_type' | 'file_size' | 'tags' | 'created_at' | 'updated_at' | 'status'> = REAL_DOC_DATA
    expect(docPin.status).toBe('indexed')
  })

  it('真实响应对象 → apiUnwrap 解一层到分页体（total/page/page_size 保留，不再多剥）', () => {
    expect(apiUnwrap(respOf(REAL_BODY))).toBe(REAL_BODY.data)
  })

  it('最小假响应 {data: body}（本仓既有视图测试 mock 手法）同样解到分页体', () => {
    expect(apiUnwrap({ data: REAL_BODY })).toBe(REAL_BODY.data)
  })

  it('迁移等价性：既有视图路径 response.data.data 与 apiUnwrap(response) 恒等', () => {
    const resp = respOf(REAL_BODY)
    expect(apiUnwrap(resp)).toBe(resp.data.data)
  })

  it('分页体形状即 KnowledgeBaseListResponse：data 为数组、携带分页元数据', () => {
    const pageBody = apiUnwrap<KnowledgeBaseListResponse>(respOf(REAL_BODY))
    expect(Array.isArray(pageBody.data)).toBe(true)
    expect(pageBody.page).toBe(1)
    expect(pageBody.page_size).toBe(10)
  })
})
