import { createRequire } from 'node:module'
const require = createRequire('D:/Desktop/MyProjects/AI_projects/DocMind/_scratch/wt-firsthome/frontend/package.json')
const { chromium } = require('@playwright/test')
const browser = await chromium.launch({ executablePath: 'C:/Users/lenovo/AppData/Local/ms-playwright/chromium-1248/chrome-win64/chrome.exe' })
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
const page = await ctx.newPage()
await page.route('**/api/v1/**', (r) => r.request().url().includes('/users/me')
  ? r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ code: 0, message: 'ok', data: { id: 1, username: 'probe', is_superuser: true, is_org_admin: true, organization_id: 1 } }) })
  : r.abort())
await page.addInitScript(() => {
  localStorage.setItem('docmind_token', 'probe-token')
  localStorage.setItem('docmind_token_expires', String(Date.now() + 86400000))
  localStorage.setItem('user_info', JSON.stringify({ id: 1, username: 'probe', is_superuser: true, is_org_admin: true, organization_id: 1 }))
})
await page.goto('http://localhost:5182/dashboard', { waitUntil: 'domcontentloaded' })
await page.waitForSelector('[data-testid="sidebar-collapse-toggle"]')
const s = await page.locator('[data-testid="sidebar-collapse-toggle"]').evaluate((el) => {
  const c = getComputedStyle(el)
  return { border: c.border, background: c.backgroundColor, outline: c.outline, boxShadow: c.boxShadow }
})
console.log(JSON.stringify(s))
await browser.close()
