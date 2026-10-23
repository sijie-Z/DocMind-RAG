import { test, expect, type Page } from '@playwright/test'

/**
 * 终端面板 E2E（issue #477）：只验证「面板空态 / 不可用提示」，不伪造终端会话。
 *
 * 纯前端 + page.route 打桩（与 auth-smoke / session-fork 同款），不依赖后端：
 * - token 必须同时写 `docmind_token_expires`，否则路由守卫判定过期 →
 *   silent refresh 失败 → 重定向 /login（此前正是 toggle 超时的原因）；
 * - agent 页初始化所需接口全部按真实响应形状打桩；
 * - POST /terminal/sessions 返回 503（功能未启用 / Docker 不可用），
 *   断言抽屉可打开、xterm 容器渲染、错误提示可见、**不会**尝试建立 WS。
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

const ok = (data: unknown) => ({ success: true, data })

const json = (body: unknown, status = 200) => ({
  status,
  contentType: 'application/json',
  body: JSON.stringify(body),
})

async function stubApi(page: Page): Promise<void> {
  // 兜底在前、精确匹配在后（Playwright 后注册的 handler 优先）。
  await page.route('**/api/v1/**', (route) => route.fulfill(json(ok({}))))
  await page.route('**/api/v1/users/me', (route) => route.fulfill(json(ok(USER))))
  await page.route('**/api/v1/user/settings', (route) =>
    route.fulfill(json(ok({ language: 'zh', theme: 'light' }))),
  )
  await page.route('**/api/v1/agent/tools', (route) => route.fulfill(json(ok([]))))
  await page.route('**/api/v1/agent/config', (route) => route.fulfill(json(ok({}))))
  await page.route('**/api/v1/commands*', (route) =>
    route.fulfill(json(ok({ enabled: true, scope: 'global', commands: [] }))),
  )
  await page.route('**/api/v1/agent/sessions*', (route) =>
    route.fulfill(json(ok({ sessions: [], total: 0, page: 1, page_size: 50 }))),
  )
  await page.route('**/api/v1/terminal/sessions', (route) => {
    if (route.request().method() === 'POST') {
      return route.fulfill(
        json(
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
    return route.fulfill(json(ok({ sessions: [] })))
  })
}

test('终端面板：后端不可用时打开抽屉显示错误提示且不建 WS', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('docmind_token', 'e2e-fake-token')
    localStorage.setItem('docmind_token_expires', String(Date.now() + 3_600_000))
  })

  const sockets: string[] = []
  page.on('websocket', (ws) => sockets.push(ws.url()))

  await stubApi(page)
  await page.goto('/agent')

  // Agent 视图按需编译 + 初始化；toggle 在 header 内恒渲染，不依赖终端接口。
  const toggle = page.locator('[data-testid="agent-terminal-toggle"]')
  await expect(toggle).toBeVisible({ timeout: 20_000 })
  await toggle.click()

  await expect(page.locator('[data-testid="terminal-screen"]')).toBeVisible({ timeout: 15_000 })
  await expect(page.locator('.terminal-note--error')).toBeVisible({ timeout: 15_000 })

  // 创建失败时绝不尝试连接终端 WS（票据都没拿到）。注意其它应用级 WS
  // （Vite HMR / notifications）不属于本断言范围。
  const terminalSockets = sockets.filter((url) => url.includes('/api/v1/terminal/ws/'))
  expect(terminalSockets).toEqual([])
})
