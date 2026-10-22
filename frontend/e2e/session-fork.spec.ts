import { test, expect, type Page, type Route } from '@playwright/test'

/**
 * Session Fork (#476) E2E：纯前端 + page.route 打桩（与 auth-smoke 同款）。
 *
 * 覆盖 issue #476 任务 6 的验收点：
 * - 从第 2 条消息（assistant 边界）分叉：POST body 携带 message_id；
 * - 分叉成功后跳转子会话，子会话消息数/内容来自服务端（复制前缀）；
 * - 子会话展示 seed 说明；409 边界非法时如实展示原因且不跳转。
 *
 * 不依赖真实后端：端点契约按 backend/app/api/v1/endpoints/sessions.py 与
 * session_fork_service.py 的响应字段打桩。
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

const PARENT_SESSION = {
  id: 's1',
  title: 'Parent session',
  message_count: 4,
  created_at: '',
  updated_at: '',
  parent_session_id: null,
  root_session_id: 's1',
  fork_point_message_id: null,
  fork_reason: null,
  is_fork: false,
  is_seeded: false,
}

const CHILD_SESSION = {
  id: 'c1',
  title: 'Parent session (fork)',
  message_count: 2,
  created_at: '2026-10-22T10:00:00',
  updated_at: '2026-10-22T10:00:00',
  parent_session_id: 's1',
  root_session_id: 's1',
  fork_point_message_id: 'm2',
  fork_reason: 'user_fork',
  is_fork: true,
  is_seeded: true,
}

const PARENT_MESSAGES = [
  { id: 'm1', content: 'First question', message_type: 'user', created_at: '' },
  { id: 'm2', content: 'First answer', message_type: 'assistant', created_at: '' },
  { id: 'm3', content: 'Second question', message_type: 'user', created_at: '' },
  { id: 'm4', content: 'Second answer', message_type: 'assistant', created_at: '' },
]

const CHILD_MESSAGES = [
  { id: 'c-m1', content: 'First question', message_type: 'user', created_at: '' },
  { id: 'c-m2', content: 'First answer', message_type: 'assistant', created_at: '' },
]

const SESSION_DETAIL = (session: typeof PARENT_SESSION | typeof CHILD_SESSION, messages: unknown[]) =>
  ok({
    ...session,
    messages,
    config: null,
  })

const FORK_RESULT = ok({
  id: 'c1',
  title: 'Parent session (fork)',
  parent_session_id: 's1',
  root_session_id: 's1',
  fork_point_message_id: 'm2',
  fork_reason: 'user_fork',
  message_count: 2,
  copied_message_count: 2,
  boundary_seq: 2,
  is_seeded: true,
  checkpoint: { cloned: false, task_id: null, source_task_id: null },
  idempotent_replay: false,
})

async function stubApi(page: Page, forkHandler?: (route: Route) => Promise<void> | void) {
  // 兜底在前、精确匹配在后（Playwright 后注册的 handler 优先）
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
  await page.route('**/api/v1/agent/sessions*', (route) => {
    const url = new URL(route.request().url())
    if (url.searchParams.get('parent_session_id') === 's1') {
      return route.fulfill(json(ok({ sessions: [CHILD_SESSION], total: 1, page: 1, page_size: 50 })))
    }
    return route.fulfill(
      json(ok({ sessions: [PARENT_SESSION, CHILD_SESSION], total: 2, page: 1, page_size: 50 })),
    )
  })
  await page.route('**/api/v1/agent/sessions/s1', (route) =>
    route.fulfill(json(SESSION_DETAIL(PARENT_SESSION, PARENT_MESSAGES))),
  )
  await page.route('**/api/v1/agent/sessions/c1', (route) =>
    route.fulfill(json(SESSION_DETAIL(CHILD_SESSION, CHILD_MESSAGES))),
  )
  await page.route('**/api/v1/sessions/s1/fork', (route) => {
    if (forkHandler) return forkHandler(route)
    return route.fulfill(json(FORK_RESULT))
  })
}

async function openParentSession(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem('docmind_token', 'e2e-fake-token')
    localStorage.setItem('docmind_token_expires', String(Date.now() + 3_600_000))
  })
  await page.goto('/agent')
  const select = page.locator('.session-select')
  await expect(select).toBeVisible()
  await select.selectOption('s1')
  await expect(page.locator('[data-testid="fork-here"]')).toHaveCount(4)
  return select
}

test('从第 2 条消息分叉：请求边界 message_id，跳转子会话并保留 seed 说明', async ({ page }) => {
  const forkBodies: unknown[] = []
  await stubApi(page, async (route) => {
    forkBodies.push(route.request().postDataJSON())
    await route.fulfill(json(FORK_RESULT))
  })

  const select = await openParentSession(page)
  await page.locator('[data-testid="fork-here"]').nth(1).click()

  const note = page.locator('[data-testid="fork-seed-note"]')
  await expect(note).toBeVisible()
  await expect(note).toContainText('2')
  await expect(note).toContainText('2 条消息')
  expect(forkBodies).toEqual([{ message_id: 'm2' }])

  // 跳转到子会话：选择器值更新，消息来自子会话详情（复制前缀 2 条）
  await expect(select).toHaveValue('c1')
  await expect(page.locator('.user-bubble')).toHaveCount(1)
  await expect(page.locator('.agent-msg')).toHaveCount(1)
  await expect(page.locator('.user-bubble')).toContainText('First question')
})

test('409 边界非法：如实展示原因且不跳转会话', async ({ page }) => {
  await stubApi(page, async (route) => {
    await route.fulfill(
      json(
        {
          success: false,
          code: 409,
          error_code: 'FORK_BOUNDARY_INVALID',
          message: '该 ASSISTANT 消息不是轮次最终消息（后面还有 assistant），不能作为分叉边界',
          detail: { message_id: 'm2' },
        },
        409,
      ),
    )
  })

  const select = await openParentSession(page)
  await page.locator('[data-testid="fork-here"]').nth(1).click()

  const error = page.locator('[data-testid="fork-error"]')
  await expect(error).toBeVisible()
  await expect(error).toContainText('分叉边界非法')
  await expect(select).toHaveValue('s1')
})
