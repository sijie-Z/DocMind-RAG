import { defineConfig, devices } from '@playwright/test'

/**
 * Playwright E2E 冒烟配置。
 *
 * 设计前提：前端 API 全部走相对路径 /api/v1（见 src/utils/request.ts），
 * 因此用例用 page.route() 打桩即可，**不需要真实的 backend/db/redis 等任何服务**。
 * 只需要一个 vite dev server 提供页面本身。
 */
// E2E_BASE_URL：本地并行验证时可在另一个端口起 dev server（不干扰 5173 的主栈）；
// 缺省保持既有 5173 口径，CI 行为不变。
const BASE_URL = process.env.E2E_BASE_URL || 'http://localhost:5173'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  // CI 上禁止遗留的 test.only（防止"只跑一条"悄悄放过）
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: 'list',

  // 目标是"快"：单测 20s、断言 5s
  timeout: 20_000,
  expect: { timeout: 5_000 },

  use: {
    baseURL: BASE_URL,
    headless: true,
    // 本地浏览器版本与 Playwright 期望的 revision 不一致时，可显式指定已安装的
    // Chromium 可执行文件（CI 不设置该变量，行为不变）。
    launchOptions: process.env.PW_CHROMIUM_EXECUTABLE
      ? { executablePath: process.env.PW_CHROMIUM_EXECUTABLE }
      : undefined,
    // 失败时留证据
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },

  // 只跑 chromium：多浏览器与本任务"快"的目标冲突
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],

  webServer: {
    command: 'npm run dev',
    url: BASE_URL,
    // 本地已有 dev server 时直接复用；CI 上必须由 Playwright 自己拉起
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
})
