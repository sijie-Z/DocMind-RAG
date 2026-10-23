import { test, expect } from '@playwright/test'

/**
 * 终端面板 E2E（issue #477）：只验证「面板空态 / 不可用提示」，不伪造终端会话。
 *
 * 真实 PTY 需要后端 + Docker daemon，CI 环境不可保证；因此本 spec 用
 * page.route 打桩：
 * - 登录态：直接写 localStorage token（路由守卫只校验存在性）
 * - POST /terminal/sessions 返回 503（功能未启用 / Docker 不可用）
 * - 断言：抽屉可打开、xterm 容器渲染、错误提示可见、**不会**尝试建立 WS
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

const ok = (data: unknown) => ({ code: 0, message: 'success', data })

const fulfillJson = (body: unknown, status = 200) => ({
  status,
  contentType: 'application/json',
  body: JSON.stringify(body),
})

test('终端面板：后端不可用时打开抽屉显示错误提示且不建 WS', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('docmind_token', 'e2e-fake-token')
  })

  const sockets: string[] = []
  page.on('websocket', (ws) => sockets.push(ws.url()))

  // 兜底在前、精确匹配在后（Playwright 后注册的 handler 优先）。
  await page.route('**/api/v1/**', (route) => route.fulfill(fulfillJson(ok({}))))
  await page.route('**/api/v1/users/me', (route) => route.fulfill(fulfillJson(ok(USER))))
  await page.route('**/api/v1/user/settings', (route) =>
    route.fulfill(fulfillJson(ok({ language: 'zh', theme: 'light' }))),
  )
  await page.route('**/api/v1/terminal/sessions', (route) => {
    if (route.request().method() === 'POST') {
      return route.fulfill(
        fulfillJson(
          {
            success: false,
            code: 503,
            error_code: 'SERVICE_UNAVAILABLE',
            message: 'terminal disabled',
            detail: 'terminal_disabled',
            data: null,
          },
          503,
        ),
      )
    }
    return route.fulfill(fulfillJson(ok({ sessions: [] })))
  })

  await page.goto('/agent')

  await page.locator('[data-testid="agent-terminal-toggle"]').click()
  await expect(page.locator('[data-testid="terminal-screen"]')).toBeVisible()
  await expect(page.locator('.terminal-note--error')).toBeVisible()

  // 创建失败时绝不尝试连接 WS（票据都没拿到）。
  expect(sockets).toEqual([])
})
