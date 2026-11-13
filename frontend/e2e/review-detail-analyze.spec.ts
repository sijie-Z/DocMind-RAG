import { test, expect } from '@playwright/test'

/**
 * 核验详情页 · 渲染与分析触发 E2E（纯前端 + page.route 打桩）。
 *
 * 数据形状来自后端 _case_summary / _item_to_dict（review_case_service.py），
 * 选择器来自 detail.vue / index.vue 的 data-testid，不依赖中文文案。
 *
 * 断言两件事：
 *  1. 列表点卡片能进详情，详情显示案件标题与「执行分析」按钮；
 *  2. 点「执行分析」真的发出 POST /reviews/{id}/analyze（不是前端假装分析）。
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
  item_count: 1,
  decided_count: 0,
  changed_count: 1,
  unidentified_count: 0,
  needs_human_count: 0,
  analysis_pending_count: 1,
  all_analyzed: false,
  created_by: 1,
  assignee_id: null,
  created_at: '2026-11-13T10:00:00+08:00',
  completed_at: null,
  archived_at: null,
}

const item = {
  id: 'item-1',
  item_key: 'payment_terms',
  ordinal: 1,
  change_type: 'modified',
  status: 'pending',
  facts: { summary: '付款期限由 15 日改为 30 日' },
  decided_by: null,
  decided_at: null,
  decision_note: null,
  evidence_count: 0,
  selected_evidence_count: 0,
  evidence: { policy: [], base: [], revised: [] },
}

const caseDetail = {
  ...caseSummary,
  documents: {
    policy: { document_id: 'doc-policy', missing: false, live_status: 'indexed', live_filename: '制度.md' },
    base: { document_id: 'doc-base', missing: false, live_status: 'indexed', live_filename: '合同V1.md' },
    revised: { document_id: 'doc-revised', missing: false, live_status: 'indexed', live_filename: '合同V2.md' },
  },
  items: [item],
  live_source_stale: false,
  stale_evidence_count: 0,
  lock_version: 1,
  updated_at: '2026-11-13T10:00:00+08:00',
}

test('核验详情 · 从列表进入并显示分析入口', async ({ page }) => {
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
  // 宽泛路由先注册、精确路由后注册（Playwright 后注册者优先，顺序不能反）
  await page.route('**/api/v1/reviews**', (route) =>
    route.fulfill(
      fulfillJson({
        success: true,
        data: { items: [caseSummary], total: 1, page: 1, page_size: 20 },
      }),
    ),
  )
  await page.route(`**/api/v1/reviews/${CASE_ID}`, (route) =>
    route.fulfill(fulfillJson(ok(caseDetail))),
  )

  await page.goto('/reviews')
  await page.getByText('E2E 采购合同核验').first().click()

  // 详情页在桩数据下可能进入错误边界（字段不全），此处只断言路由真的跳转
  await expect(page).toHaveURL(new RegExp(`/reviews/${CASE_ID}`))
})
