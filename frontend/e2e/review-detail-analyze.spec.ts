import { test, expect } from '@playwright/test'

/**
 * 核验详情页 · 渲染 + 执行分析 E2E（纯前端 + page.route 打桩）。
 *
 * 桩结构严格照后端：
 *  - 案件摘要：`_case_summary`（review_case_service.py）
 *  - 文档状态：`_document_snapshot`（含 readiness.status / blocking_reasons / 分块计数）
 *  - 核验项：`_item_to_dict`
 *
 * 两个踩过的坑（别再踩）：
 *  1. Playwright **后注册的路由优先**：列表通配必须注册在详情精确路由之前，
 *     否则详情请求被列表桩吃掉，`status` 缺失 → 分析按钮禁用 → 表现为"点击超时"。
 *  2. 详情页对 documents[side].readiness 结构敏感，缺字段会进错误边界（页面渲染异常）。
 */

const USER = {
  id: 1,
  username: 'guest',
  email: 'guest@example.com',
  role: 'user',
  is_active: true,
  is_superuser: true,
  organization_id: 1,
}

const CASE_ID = 'case-1'

const ok = (data: unknown) => ({ success: true, code: 0, message: 'success', data })

const fulfillJson = (body: unknown, status = 200) => ({
  status,
  contentType: 'application/json',
  body: JSON.stringify(body),
})

const docState = (id: string, name: string, label: string) => ({
  document_id: id,
  filename: name,
  doc_role: 'contract',
  family_key: 'fam-e2e',
  version_label: label,
  version_seq: 1,
  effective_from: null,
  effective_to: null,
  content_sha256: 'e3b0c44298fc1c149afbf4c8996fb924',
  parsed_at: '2026-11-13T09:00:00+08:00',
  indexed_at: '2026-11-13T09:00:00+08:00',
  missing: false,
  live_status: 'indexed',
  live_filename: name,
  identity_changed: false,
  readiness: {
    status: 'reviewable',
    blocking_reasons: [],
    warnings: [],
    total_chunks: 12,
    original_text_chunks: 12,
    page_number_chunks: 0,
    clause_candidate_chunks: 6,
    legacy_chunk_count: 0,
  },
})

const caseSummary = {
  id: CASE_ID,
  title: 'E2E 采购合同核验',
  status: 'ready',
  checklist_version: 'v1',
  policy_document_id: 'doc-policy',
  base_document_id: 'doc-base',
  revised_document_id: 'doc-revised',
  base_version_label: 'V1',
  revised_version_label: 'V2',
  blocking_reasons: [],
  item_count: 0,
  decided_count: 0,
  changed_count: 0,
  unidentified_count: 0,
  needs_human_count: 0,
  analysis_pending_count: 0,
  all_analyzed: false,
  created_by: 1,
  assignee_id: null,
  created_at: '2026-11-13T10:00:00+08:00',
  completed_at: null,
  archived_at: null,
}

const caseDetail = {
  ...caseSummary,
  organization_id: 1,
  documents: {
    policy: docState('doc-policy', '采购制度.md', 'V1'),
    base: docState('doc-base', '合同-V1.md', 'V1'),
    revised: docState('doc-revised', '合同-V2.md', 'V2'),
  },
  items: [],
  live_source_stale: false,
  stale_evidence_count: 0,
  lock_version: 1,
  updated_at: '2026-11-13T10:00:00+08:00',
}

const LIST_PAYLOAD = {
  success: true,
  data: { items: [caseSummary], total: 1, page: 1, page_size: 20 },
}

async function stubShell(page: import('@playwright/test').Page) {
  await page.addInitScript(() => {
    localStorage.setItem('docmind_token', 'e2e-fake-token')
    localStorage.setItem('docmind_token_expires', String(Date.now() + 3600_000))
  })
  await page.route('**/api/v1/**', (route) => route.fulfill(fulfillJson(ok({}))))
  await page.route('**/api/v1/user/settings', (route) =>
    route.fulfill(fulfillJson(ok({ language: 'zh', theme: 'light' }))),
  )
  await page.route('**/api/v1/users/me', (route) => route.fulfill(fulfillJson(ok(USER))))
  await page.route('**/api/v1/notifications**', (route) =>
    route.fulfill(fulfillJson(ok({ items: [], total: 0, unread_count: 0 }))),
  )
  // 宽泛在前、精确在后（后注册者优先）
  await page.route('**/api/v1/reviews**', (route) => route.fulfill(fulfillJson(LIST_PAYLOAD)))
  await page.route(`**/api/v1/reviews/${CASE_ID}/exports`, (route) =>
    route.fulfill(fulfillJson(ok({ items: [], total: 0 }))),
  )
  await page.route(`**/api/v1/reviews/${CASE_ID}`, (route) =>
    route.fulfill(fulfillJson(ok(caseDetail))),
  )
}

test('核验详情 · 从列表进入后渲染案件与分析入口', async ({ page }) => {
  await stubShell(page)

  await page.goto('/reviews')
  await page.getByText('E2E 采购合同核验').first().click()

  await expect(page).toHaveURL(new RegExp(`/reviews/${CASE_ID}`))
  await expect(page.getByText('E2E 采购合同核验').first()).toBeVisible()
  await expect(page.locator('[data-testid="review-analyze"]')).toBeVisible()
})

test('核验详情 · 执行分析真的发出 analyze 请求', async ({ page }) => {
  const analyzeCalls: string[] = []
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().includes(`/reviews/${CASE_ID}/analyze`)) {
      analyzeCalls.push(request.url())
    }
  })

  await stubShell(page)
  await page.route(`**/api/v1/reviews/${CASE_ID}/analyze`, (route) =>
    route.fulfill(fulfillJson(ok({ ...caseDetail, analysis_pending_count: 0, all_analyzed: true }))),
  )

  await page.goto('/reviews')
  await page.getByText('E2E 采购合同核验').first().click()
  await page.locator('[data-testid="review-analyze"]').click()

  await expect.poll(() => analyzeCalls.length).toBeGreaterThan(0)
})
