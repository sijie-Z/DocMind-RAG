import { describe, expect, it } from 'vitest'
import { buildDocumentRoute } from '../search/documentLink'
import type { SearchResult } from '@/api/search'

const base = (overrides: Partial<SearchResult> = {}): SearchResult => ({
  id: 'doc-from-id',
  text: '命中文本',
  score: 1,
  ...overrides
})

describe('检索命中 → 文档深链', () => {
  it('优先使用 source.document_id，并携带 chunk_id', () => {
    const target = buildDocumentRoute(base({
      source: { document_id: 'doc-authoritative' },
      chunk_id: 'chunk-7'
    }))
    expect(target).toEqual({
      name: 'KnowledgeDocument',
      params: { documentId: 'doc-authoritative' },
      query: { chunk: 'chunk-7' }
    })
  })

  it('旧响应无 source 时退回 result.id；无 chunk 时不带 query', () => {
    const target = buildDocumentRoute(base({ chunk_id: null }))
    expect(target).toEqual({
      name: 'KnowledgeDocument',
      params: { documentId: 'doc-from-id' },
      query: {}
    })
  })

  it('没有可用文档 id 时返回 null（调用方不跳转）', () => {
    expect(buildDocumentRoute(base({ id: '', source: {} }))).toBeNull()
  })
})
