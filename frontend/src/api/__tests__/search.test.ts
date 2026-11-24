/**
 * P1 修复回归（UI 验收 round2 待办 #2）：搜索页组包 100% 422。
 *
 * 旧组包 {query, search_type, limit}：缺后端 schema 必填的 organization_id
 * （422 Field required），且 search_type / limit 均为 schema 不识别的过时字段
 * （被 pydantic 静默丢弃）。修复后 buildSearchPayload 与
 * backend/app/schemas/knowledge.py 的 KnowledgeSearchRequest 全字段对齐。
 *
 * B4（D5）更新：top_k 不再由前端固化发送。缺省时由后端解析「组织设置 >
 * settings.TOP_K_RESULTS=10」——组织未设置时行为与旧版发送 10 一致；显式
 * topK 入参仍原样透传。
 *
 * 竞品批次六（feature A）：metadata_filters / knowledge_base_id 接线；
 * tags 去空去重（上限 20），扩展名映射后端 DocumentType 枚举，
 * 不再发送顶层 file_types。
 */
import { describe, it, expect } from 'vitest'
import { buildSearchPayload, normalizeSearchTags, normalizeSearchFileTypes } from '../search'

describe('buildSearchPayload · 与后端 schema 对齐（P1 搜索 422 修复）', () => {
  it('全字段：query + organization_id + 显式 top_k + 显式 search_mode + metadata_filters + KB', () => {
    const payload = buildSearchPayload({
      query: '架构',
      organizationId: 3,
      topK: 10,
      searchMode: 'keyword',
      fileTypes: ['docx', 'PDF'],
      tags: ['技术文档'],
      knowledgeBaseId: 'kb-1',
    })
    expect(payload).toEqual({
      query: '架构',
      organization_id: 3,
      top_k: 10,
      search_mode: 'keyword',
      metadata_filters: {
        tags: ['技术文档'],
        file_type: ['word', 'pdf'],
      },
      knowledge_base_id: 'kb-1',
    })
    expect('file_types' in payload).toBe(false)
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

  it('过时字段 search_type / limit / file_types 一律不出现；空筛选不发', () => {
    const payload = buildSearchPayload({
      query: 'q', organizationId: 7, fileTypes: [], tags: [],
      knowledgeBaseId: '   ',
    })
    expect(payload).not.toHaveProperty('search_type')
    expect(payload).not.toHaveProperty('limit')
    expect(payload).not.toHaveProperty('file_types')
    expect(payload).not.toHaveProperty('metadata_filters')
    expect(payload).not.toHaveProperty('knowledge_base_id')
    expect(payload).toEqual({ query: 'q', organization_id: 7 })
  })

  it('规范：trim / 去空 / 去重 / 上限 20；未知扩展名剔除', () => {
    expect(normalizeSearchTags([' a ', '', 'a', '  ', 'b'])).toEqual(['a', 'b'])
    expect(normalizeSearchFileTypes(['docx', 'DOCX', ' xlsx ', 'png', '', 'pdf']))
      .toEqual(['word', 'excel', 'pdf'])
    const payload = buildSearchPayload({
      query: 'q', organizationId: 7,
      tags: Array.from({ length: 25 }, (_, i) => ` tag-${i} `),
    })
    expect(payload.metadata_filters?.tags).toHaveLength(20)
  })

  it('tags 按后端取值域过滤：空格 / # / 超长一律不发，避免 422', () => {
    const valid = ['合同', 'ok-2024.v1', '文档/归档', 'naïve']
    const invalid = ['bad tag', '#topic', 'x'.repeat(101), '', '  ']
    expect(normalizeSearchTags([...valid, ...invalid])).toEqual(valid)
    const payload = buildSearchPayload({
      query: 'q', organizationId: 7, tags: [...invalid, ...valid],
    })
    expect(payload.metadata_filters?.tags).toEqual(valid)
    // 全部非法时整个 tags 字段省略，而不是发空数组
    const allInvalid = buildSearchPayload({ query: 'q', organizationId: 7, tags: invalid })
    expect(allInvalid).not.toHaveProperty('metadata_filters')
  })

  it('csv 映射到后端 DocumentType.TXT（上传路径同样归为 txt）', () => {
    expect(normalizeSearchFileTypes(['csv', '.CSV', 'md'])).toEqual(['txt'])
    const payload = buildSearchPayload({ query: 'q', organizationId: 7, fileTypes: ['csv'] })
    expect(payload.metadata_filters).toEqual({ file_type: ['txt'] })
  })

  it('检索自测：document_ids/debug 透传；空数组不发送', () => {
    const payload = buildSearchPayload({
      query: '付款条款',
      organizationId: 7,
      documentIds: ['doc-1', 'doc-2'],
      debug: true,
    })
    expect(payload).toEqual({
      query: '付款条款',
      organization_id: 7,
      document_ids: ['doc-1', 'doc-2'],
      debug: true,
    })

    const empty = buildSearchPayload({ query: 'q', organizationId: 7, documentIds: [] })
    expect('document_ids' in empty).toBe(false)
    expect('debug' in empty).toBe(false)
  })
})
