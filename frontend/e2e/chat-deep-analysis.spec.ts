import { test, expect, type Page, type Route } from '@playwright/test'

/**
 * issue #508（方案 B）E2E：从 Chat 触发深度分析必须跳转 /agent 工作台。
 *
 * 纯前端 + page.route 打桩（与 auth-smoke / session-fork 同款），不需要后端：
 * - 进入 /chat，输入一段文本；
 * - 断言入口文案「前往 Agent 工作台（支持审批/回放）」；
 * - 点击入口 → 明确提示「深度分析已移至 Agent 工作台…」→ 路由跳到 /agent，
 *   当前输入经 ?prompt= 保留；
 * - 负向断言：整个过程没有向 /chat/stream 发送任何请求（尤其 useAgent=true）。
 */

const PROMPT = '分析这份财报的现金流与风险'

const USER = {
  id: 1,
  username: 'guest',
  email: 'guest@example.com',
  role: 'user',
  is_active: true,
  is_superuser: false,
  organization_id: 1,
}

const ok = (data: unknown) => ({ success: true, data })

const json = (body: unknown, status = 200) => ({
  status,
  contentType: 'application/json',
  body: JSON.stringify(body),
})

async function stubApi(page: Page, streamRequests: unknown[]) {
  // 兜底在前、精确匹配在后（Playwright 后注册的 handler 优先）
  await page.route('**/api/v1/**', (route) => route.fulfill(json(ok({}))))
  await page.route('**/api/v1/users/me', (route) => route.fulfill(json(ok(USER))))
  await page.route('**/api/v1/user/settings', (route) =>
    route.fulfill(json(ok({ language: 'zh', theme: 'light' }))),
  )
  await page.route('**/api/v1/conversations**', (route) =>
    route.fulfill(json(ok({ conversations: [], total: 0 }))),
  )
  await page.route('**/api/v1/chat/stream', (route: Route) => {
    streamRequests.push(route.request().postDataJSON())
    return route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' })
  })
}

test('从 Chat 触发深度分析：提示并跳转 /agent，保留当前输入，不发送 useAgent 请求', async ({ page }) => {
  const streamRequests: unknown[] = []
  await stubApi(page, streamRequests)

  await page.addInitScript(() => {
    localStorage.setItem('docmind_token', 'e2e-fake-token')
    localStorage.setItem('docmind_token_expires', String(Date.now() + 3_600_000))
  })

  await page.goto('/chat')
  const textarea = page.locator('textarea').first()
  // 本地 vite 首次按需编译 Chat 视图可能超过默认 5s；放宽首个可见性等待。
  await expect(textarea).toBeVisible({ timeout: 15_000 })
  await textarea.fill(PROMPT)

  const entry = page.locator('[data-testid="agent-workbench-entry"]')
  await expect(entry).toBeVisible({ timeout: 15_000 })
  await expect(entry).toContainText('前往 Agent 工作台（支持审批/回放）')

  await entry.click()

  // 明确提示（naive message 全局挂载，路由切换后仍可见）
  await expect(page.getByText('深度分析已移至 Agent 工作台（支持审批/回放）')).toBeVisible()

  // 跳转 /agent，并保留当前输入（?prompt=）
  await page.waitForURL((url) => url.pathname === '/agent', { timeout: 15_000 })
  const url = new URL(page.url())
  expect(url.pathname).toBe('/agent')
  expect(url.searchParams.get('prompt')).toBe(PROMPT)

  // 负向断言：Chat 不再以 useAgent=true 发送到 /chat/stream
  expect(streamRequests).toHaveLength(0)
})
