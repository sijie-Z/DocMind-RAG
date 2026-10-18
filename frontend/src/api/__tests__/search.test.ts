/**
 * P1 修复回归（UI 验收 round2 待办 #2）：搜索页组包 100% 422。
 *
 * 旧组包 {query, search_type, limit}：缺后端 schema 必填的 organization_id
 * （422 Field required），且 search_type / limit 均为 schema 不识别的过时字段
 * （被 pydantic 静默丢弃）。修复后 buildSearchPayload 与
 * backend/app/schemas/knowledge.py 的 KnowledgeSearchRequest 全字段对齐。
 *
 * 变异点：拿掉 organization_id / top_k 改回 limit / auto 时携带 search_mode
 * → 对应用例精确红。
 *
 * B4（D5）更新：top_k 不再由前端固化发送。缺省时由后端解析「组织设置 >
 * settings.TOP_K_RESULTS=10」——组织未设置时行为与旧版发送 10 一致；显式
 * topK 入参仍原样透传。
 */
import { describe, it, expect } from 'vitest'
import { buildSearchPayload } from '../search'

describe('buildSearchPayload · 与后端 schema 对齐（P1 搜索 422 修复）', () => {
  it('全字段：query + organization_id + 显式 top_k + 显式 search_mode + file_types', () => {
    const payload = buildSearchPayload({
      query: '架构',
      organizationId: 3,
      topK: 10,
      searchMode: 'keyword',
      fileTypes: ['md'],
    })
    expect(payload).toEqual({
      query: '架构',
      organization_id: 3,
      top_k: 10,
      search_mode: 'keyword',
      file_types: ['md'],
    })
  })

  it('top_k 缺省不发送：交给组织设置 / 平台默认解析（B4/D5）', () => {
    const payload = buildSearchPayload({ query: 'q', organizationId: 7 })
    expect('top_k' in payload).toBe(false)
  })

  it('auto（自动路由）：完全不发送 search_mode，与后端「缺省=自动」语义一致', () => {
    const payload = buildSearchPayload({ query: 'q', organizationId: 7, searchMode: 'auto' })
    expect('search_mode' in payload).toBe(false)
    expect(payload).toEqual({ query: 'q', organization_id: 7 })
  })

  it('过时字段 search_type / limit 一律不出现；空 file_types 不发', () => {
    const payload = buildSearchPayload({ query: 'q', organizationId: 7, fileTypes: [] })
    expect(payload).not.toHaveProperty('search_type')
    expect(payload).not.toHaveProperty('limit')
    expect('file_types' in payload).toBe(false)
  })
})
