import { test, expect } from '@playwright/test'

/**
 * 核验工作台 · 列表与建案入口 E2E（纯前端 + page.route 打桩，不需要后端）。
 *
 * 用 data-testid 断言，不依赖文案 —— 上一版按中文文案断言失败（页面在无候选文档时
 * 渲染的是 create-case-empty 而不是 reviews-empty-state），这是本次修正。
 *
 * 断言两件事：
 *  1. 空数据时出现空态（reviews-empty-state），不是白屏；
 *  2. 点「新建核验」真的能打开建案弹窗（create-case-title 可见）。
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

const ok = (data: unknown) => ({ success: true, code: 0, message: 'success', data })

const fulfillJson = (body: unknown, status = 200) => ({
  status,
  contentType: 'application/json',
  body: JSON.stringify(body),
})

test('核验列表 · 空态可见且建案入口可用', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('docmind_token', 'e2e-fake-token')
    localStorage.setItem('docmind_token_expires', String(Date.now() + 3600_000))
  })

  // 兜底在前、精确匹配在后（Playwright 后注册优先）
  await page.route('**/api/v1/**', (route) => route.fulfill(fulfillJson(ok({}))))
  await page.route('**/api/v1/user/settings', (route) =>
    route.fulfill(fulfillJson(ok({ language: 'zh', theme: 'light' }))),
  )
  await page.route('**/api/v1/users/me', (route) => route.fulfill(fulfillJson(ok(USER))))
  await page.route('**/api/v1/notifications**', (route) =>
    route.fulfill(fulfillJson(ok({ items: [], total: 0, unread_count: 0 }))),
  )
  await page.route('**/api/v1/reviews**', (route) =>
    route.fulfill(
      fulfillJson({ success: true, data: { items: [], total: 0, page: 1, page_size: 20 } }),
    ),
  )

  await page.goto('/reviews')

  // 1) 空态可见（用 testid，不依赖具体文案）
  await expect(page.locator('[data-testid="reviews-empty-state"]')).toBeVisible()

  // 2) 建案入口真的能打开弹窗
  await page.locator('[data-testid="create-case-open"]').click()
  await expect(page.locator('[data-testid="create-case-title"]')).toBeVisible()
})
