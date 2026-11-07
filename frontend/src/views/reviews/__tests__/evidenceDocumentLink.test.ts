import { describe, expect, it } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import { buildEvidenceLinkTarget } from '../evidenceLink'

const row = { document_id: 'doc-base', chunk_id_at_capture: 'chunk-1' }

describe('证据行 → 文档深链', () => {
  it('生成带文档与分块的深链目标', () => {
    expect(buildEvidenceLinkTarget(row)).toEqual({
      name: 'KnowledgeDocument',
      params: { documentId: 'doc-base' },
      query: { chunk: 'chunk-1' }
    })
  })

  it('文档已删除/缺失时不生成链接', () => {
    expect(buildEvidenceLinkTarget(row, { missing: true })).toBeNull()
  })

  it('缺 document_id 或 chunk id 时不生成链接', () => {
    expect(buildEvidenceLinkTarget({ document_id: '', chunk_id_at_capture: 'chunk-1' })).toBeNull()
    expect(buildEvidenceLinkTarget({ document_id: 'doc-base', chunk_id_at_capture: '' })).toBeNull()
  })
})

describe('真实路由解析', () => {
  it('文档与分块中的特殊字符会被编码', () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        {
          path: '/knowledge/:documentId',
          name: 'KnowledgeDocument',
          component: { template: '<div />' }
        }
      ]
    })
    const target = buildEvidenceLinkTarget({
      document_id: 'doc 中文',
      chunk_id_at_capture: 'c 1#x'
    })
    expect(target).not.toBeNull()

    const href = router.resolve(target!).href
    expect(href).toBe('/knowledge/doc%20%E4%B8%AD%E6%96%87?chunk=c+1%23x')
  })
})
