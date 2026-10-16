/**
 * documents 响应形状契约（T11 · 方案 B）。
 *
 * 钉住事实：GET /documents/{id} 与 GET /documents/{id}/content 为形态②
 * 「{success: true, data: 业务体}」——单层信封。
 * 取样方式：backend/app/api/v1/endpoints/documents.py:311-331（详情）与
 * :378-387（全文/预览）源码逐字（2026-10-15 复核）。
 * 前端声明：knowledge.ts 的 getDocumentDetail / getDocumentContent 返回
 * { data: … }；知识库视图既有读取路径为 response.data?.data（本单迁移到
 * apiUnwrap，两式等价由本文件的等价用例钉住）。
 */
import { describe, it, expect } from 'vitest'
import { apiUnwrap } from '@/utils/apiUnwrap'
import { getDocumentDetail, getDocumentContent } from '@/api/knowledge'
import type { DocumentDetail, DocumentContent } from '@/api/knowledge'

/** 真机响应对象的最小化身（axios 响应挂 status/statusText/headers）。 */
const respOf = <B>(body: B) => ({ data: body, status: 200, statusText: 'OK', headers: {}, config: {} })

// 形状逐字对齐 documents.py:311-331；可空字段保留 null 真形（前端声明中
// description?: string 等为既有宽松面、不容 null，不属本单收敛——故类型锚点
// 按核心字段取 Pick）：
const REAL_DETAIL_DATA = {
  id: 'doc-1',
  title: 'architecture.md',
  filename: 'architecture.md',
  file_size: 1578,
  file_type: 'word',
  status: 'indexed',
  raw_status: 'indexed',
  parse_error: null,
  created_at: '2026-10-08T05:29:00',
  updated_at: '2026-10-08T05:30:00',
  parsed_at: '2026-10-08T05:30:00',
  indexed_at: '2026-10-08T05:31:00',
  chunk_count: 12,
  description: null,
  keywords: ['技术文档'],
  upload_source: '知识库上传',
}
const REAL_DETAIL_BODY = { success: true, data: REAL_DETAIL_DATA }

// 形状逐字对齐 documents.py:378-387
const REAL_CONTENT_DATA = {
  id: 'doc-1',
  filename: 'architecture.md',
  content: '全文内容……',
  summary: '摘要',
  suggested_tags: ['标签'],
}
const REAL_CONTENT_BODY = { success: true, data: REAL_CONTENT_DATA }

describe('documents 响应形状契约 · 形态② {success, data}', () => {
  it('模块导出面 + 类型锚点：getDocumentDetail 声明 { data: DocumentDetail }，样例核心字段核对（编译期+运行期）', () => {
    expect([getDocumentDetail, getDocumentContent].every((fn) => typeof fn === 'function')).toBe(true)
    const pin: Pick<DocumentDetail, 'id' | 'title' | 'filename' | 'file_size' | 'file_type' | 'status'> = REAL_DETAIL_DATA
    const bodyPin: Awaited<ReturnType<typeof getDocumentDetail>>['data']['data'] = {
      ...pin,
      parse_error: undefined,
      created_at: REAL_DETAIL_DATA.created_at,
      description: undefined,
      keywords: REAL_DETAIL_DATA.keywords,
      upload_source: REAL_DETAIL_DATA.upload_source,
    }
    expect(bodyPin.id).toBe('doc-1')
    expect(pin.status).toBe('indexed')
  })

  it('详情：真实响应对象 → apiUnwrap 剥一层到业务体（同一引用）', () => {
    expect(apiUnwrap(respOf(REAL_DETAIL_BODY))).toBe(REAL_DETAIL_DATA)
  })

  it('详情：直接传载荷 {success, data} 同样解到业务体', () => {
    expect(apiUnwrap(REAL_DETAIL_BODY)).toBe(REAL_DETAIL_DATA)
  })

  it('详情：最小嵌套假响应 {data:{data:D}}（本仓既有视图测试 mock 手法）兼容', () => {
    expect(apiUnwrap({ data: { data: REAL_DETAIL_DATA } })).toBe(REAL_DETAIL_DATA)
  })

  it('全文/预览：{success, data} 解到业务体，类型锚点核对 DocumentContent', () => {
    const pin: Pick<DocumentContent, 'id' | 'filename' | 'content' | 'suggested_tags'> = REAL_CONTENT_DATA
    expect(apiUnwrap(respOf(REAL_CONTENT_BODY))).toBe(REAL_CONTENT_DATA)
    expect(pin.suggested_tags).toEqual(['标签'])
  })

  it('迁移等价性：既有视图路径 response.data?.data 与 apiUnwrap(response) 恒等', () => {
    const resp = respOf(REAL_DETAIL_BODY)
    expect(apiUnwrap(resp)).toBe(resp.data.data)
  })
})
