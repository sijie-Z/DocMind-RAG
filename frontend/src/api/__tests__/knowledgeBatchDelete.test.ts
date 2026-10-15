/**
 * 知识库批量删除组包判别用例（UI 验收 round2 · A2）。
 *
 * 真现场（B_contract_drift.md A2）：前端只发 {document_ids}，后端
 * knowledge.py:642-646 对非超管强校验
 * `request.organization_id == effective_org_id`（不等于即
 * AuthorizationError → 403）—— 组织管理员/普通用户批量删除**每次必 403**，
 * 按钮形同虚设。修复：从 user store 取 currentOrgId 组包（口径照 search
 * P1-2）；无组织（超管）时不发该字段，由后端 require_organization 解析。
 *
 * 变异点：去掉 organization_id 字段（还原旧组包）→ 第 1 条用例精确红。
 */
import { describe, it, expect } from 'vitest'
import { buildBatchDeletePayload } from '../knowledge'

describe('buildBatchDeletePayload · 与后端 batch-delete 校验对齐（A2 修复）', () => {
  it('有当前组织（非超管必经路径）：组包含 organization_id', () => {
    const payload = buildBatchDeletePayload(['doc-1', 'doc-2'], 3)
    expect(payload).toEqual({ document_ids: ['doc-1', 'doc-2'], organization_id: 3 })
  })

  it('无组织（null/undefined，超管场景）：省略 organization_id，由后端解析组织上下文', () => {
    const p1 = buildBatchDeletePayload(['doc-1'], null)
    const p2 = buildBatchDeletePayload(['doc-1'], undefined)
    expect('organization_id' in p1).toBe(false)
    expect('organization_id' in p2).toBe(false)
    expect(p1).toEqual({ document_ids: ['doc-1'] })
  })

  it('document_ids 原样透传（顺序保留）', () => {
    const payload = buildBatchDeletePayload(['b', 'a', 'c'], 7)
    expect(payload.document_ids).toEqual(['b', 'a', 'c'])
  })
})
