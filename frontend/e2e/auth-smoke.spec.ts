import { test, expect, type Request } from '@playwright/test'

/**
 * 登录链路 E2E 冒烟：纯前端 + page.route 打桩，不需要任何后端服务。
 *
 * 依据的既有源码事实：
 * - API 走相对路径 /api/v1（src/utils/request.ts）
 * - 登录：POST /api/v1/auth/login/json；用户信息：GET /api/v1/users/me
 * - token 落 localStorage.docmind_token（src/utils/auth.ts）
 * - 路由守卫：无 token 访问受保护页 → /login?redirect=...（src/router/index.ts）
 *
 * 隔离性：Playwright 默认为每个 test 新建独立 browser context，
 * localStorage / cookie 天然不共享，无需 beforeEach 手动清理。
 */

const USER = {
  id: 1,
  username: 'guest',
  email: 'guest@example.com',
  role: 'user',
  is_active: true,
  is_superuser: false,
  organization_id: 1,
}

const LOGIN_DATA = {
  access_token: 'e2e-fake-token',
  token_type: 'bearer',
  expires_in: 3600,
}

/** 与后端统一响应包裹一致：{code, message, data} */
const ok = (data: unknown) => ({ code: 0, message: 'success', data })

const fulfillJson = (body: unknown) => ({
  status: 200,
  contentType: 'application/json',
  body: JSON.stringify(body),
})

const loginEndpointHit = (request: Request) => request.url().includes('/api/v1/auth/login/json')

test('A. 未登录访问 /dashboard 被重定向到登录页并携带 redirect', async ({ page }) => {
  await page.goto('/dashboard')

  await expect(page).toHaveURL(/\/login/)
  // 解析 URL 断言，避免匹配 query 的编码形式（%2F 与否都能过）
  const url = new URL(page.url())
  expect(url.pathname).toBe('/login')
  expect(url.searchParams.get('redirect')).toBe('/dashboard')
})

test('B. 登录页正常渲染且无 JS 报错', async ({ page }) => {
  const pageErrors: Error[] = []
  page.on('pageerror', (error) => pageErrors.push(error))

  await page.goto('/login')

  await expect(page.locator('.n-input input[type="text"]').first()).toBeVisible()
  await expect(page.locator('.n-input input[type="password"]')).toBeVisible()
  await expect(page.locator('button[type="submit"]')).toBeVisible()

  expect(pageErrors.map((e) => e.message)).toEqual([])
})

test('C. 空表单提交只触发校验、不发送登录请求', async ({ page }) => {
  const loginRequests: Request[] = []
  page.on('request', (request) => {
    if (loginEndpointHit(request)) loginRequests.push(request)
  })

  await page.goto('/login')
  await page.locator('button[type="submit"]').click()

  // 校验失败：Naive UI 将 form-item 标为 error 状态（用状态而非 i18n 文案断言）
  await expect(
    page.locator('.n-form-item--error, .n-form-item-feedback:not(:empty)').first(),
  ).toBeVisible()

  // 给潜在的（错误的）请求留一个短窗口，再做负向断言
  await page.waitForTimeout(200)

  expect(new URL(page.url()).pathname).toBe('/login')
  expect(loginRequests).toHaveLength(0)
})

test('D. 登录成功：提交凭据 → 跳转 /dashboard → token 落盘', async ({ page }) => {
  const requests: Request[] = []
  page.on('request', (request) => requests.push(request))

  // 注册顺序：兜底在前、精确匹配在后（Playwright 后注册的 handler 优先匹配）
  await page.route('**/api/v1/**', (route) => route.fulfill(fulfillJson(ok({}))))
  await page.route('**/api/v1/user/settings', (route) =>
    route.fulfill(fulfillJson(ok({ language: 'zh', theme: 'light' }))),
  )
  await page.route('**/api/v1/users/me', (route) => route.fulfill(fulfillJson(ok(USER))))
  await page.route('**/api/v1/auth/login/json', (route) =>
    route.fulfill(fulfillJson(ok(LOGIN_DATA))),
  )

  await page.goto('/login')
  await page.locator('.n-input input[type="text"]').first().fill('guest')
  await page.locator('.n-input input[type="password"]').fill('12345678')
  await page.locator('button[type="submit"]').click()

  await expect(page).toHaveURL(/\/dashboard$/)

  // 表单真的把凭据提交出去了（而不是靠前端短路"假装登录"）
  const loginRequest = requests.find((request) => loginEndpointHit(request))
  expect(loginRequest, '应发出登录请求').toBeDefined()
  expect(loginRequest?.method()).toBe('POST')
  expect(loginRequest?.postDataJSON()).toEqual({ username: 'guest', password: '12345678' })

  await expect
    .poll(() => page.evaluate(() => localStorage.getItem('docmind_token')))
    .toBe('e2e-fake-token')
})
