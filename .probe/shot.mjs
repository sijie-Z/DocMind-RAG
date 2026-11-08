import { createRequire } from 'node:module'
import { mkdirSync } from 'node:fs'
const require = createRequire('D:/Desktop/MyProjects/AI_projects/DocMind/_scratch/wt-firsthome/frontend/package.json')
const { chromium } = require('@playwright/test')

const OUT = 'D:/Desktop/MyProjects/AI_projects/DocMind/_scratch/wt-firsthome/.probe/screens'
mkdirSync(OUT, { recursive: true })
const BASE = 'http://localhost:5182'

const browser = await chromium.launch({
  executablePath: 'C:/Users/lenovo/AppData/Local/ms-playwright/chromium-1248/chrome-win64/chrome.exe',
})
const report = {}

async function auditPage(page, label) {
  const metrics = await page.evaluate(() => {
    const doc = document.documentElement
    const small = []
    for (const btn of document.querySelectorAll('button')) {
      const r = btn.getBoundingClientRect()
      const style = getComputedStyle(btn)
      if (r.width > 0 && r.height > 0 && style.visibility !== 'hidden') {
        if (r.height < 43.5 || r.width < 43.5) {
          small.push({ text: (btn.textContent || '').trim().slice(0, 30), w: Math.round(r.width), h: Math.round(r.height) })
        }
      }
    }
    return {
      scrollWidth: doc.scrollWidth,
      clientWidth: doc.clientWidth,
      overflowX: doc.scrollWidth - doc.clientWidth,
      smallButtons: small,
      bg: getComputedStyle(document.querySelector('.landing')).backgroundColor,
      accent: (() => {
        const el = document.querySelector('.hero-title-accent')
        return el ? getComputedStyle(el).color : null
      })(),
    }
  })
  report[label] = metrics
}

// 1440 light
{
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const page = await ctx.newPage()
  await page.goto(BASE + '/firsthome', { waitUntil: 'networkidle' })
  await page.waitForSelector('.hero-title')
  await page.screenshot({ path: OUT + '/firsthome-1440.png' })
  await page.screenshot({ path: OUT + '/firsthome-1440-full.png', fullPage: true })
  await auditPage(page, 'firsthome-1440')
  await ctx.close()
}

// 375 light
{
  const ctx = await browser.newContext({ viewport: { width: 375, height: 812 } })
  const page = await ctx.newPage()
  await page.goto(BASE + '/firsthome', { waitUntil: 'networkidle' })
  await page.waitForSelector('.hero-title')
  await page.screenshot({ path: OUT + '/firsthome-375.png' })
  await page.screenshot({ path: OUT + '/firsthome-375-full.png', fullPage: true })
  await auditPage(page, 'firsthome-375')
  await ctx.close()
}

// 768 light
{
  const ctx = await browser.newContext({ viewport: { width: 768, height: 1024 } })
  const page = await ctx.newPage()
  await page.goto(BASE + '/firsthome', { waitUntil: 'networkidle' })
  await page.waitForSelector('.hero-title')
  await page.screenshot({ path: OUT + '/firsthome-768-full.png', fullPage: true })
  await auditPage(page, 'firsthome-768')
  await ctx.close()
}

// 1440 dark
{
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: 'dark' })
  const page = await ctx.newPage()
  await page.addInitScript(() => localStorage.setItem('theme', 'dark'))
  await page.goto(BASE + '/firsthome', { waitUntil: 'networkidle' })
  await page.waitForSelector('.hero-title')
  await page.screenshot({ path: OUT + '/firsthome-1440-dark.png' })
  await auditPage(page, 'firsthome-1440-dark')
  await ctx.close()
}

// 375 dark
{
  const ctx = await browser.newContext({ viewport: { width: 375, height: 812 }, colorScheme: 'dark' })
  const page = await ctx.newPage()
  await page.addInitScript(() => localStorage.setItem('theme', 'dark'))
  await page.goto(BASE + '/firsthome', { waitUntil: 'networkidle' })
  await page.waitForSelector('.hero-title')
  await page.screenshot({ path: OUT + '/firsthome-375-dark-full.png', fullPage: true })
  await ctx.close()
}

// Sidebar with injected local session (backend may be offline; layout still renders)
{
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const page = await ctx.newPage()
  const logs = []
  page.on('pageerror', (e) => logs.push('pageerror: ' + e.message))
  page.on('console', (m) => { if (m.type() === 'error') logs.push('console: ' + m.text().slice(0, 160)) })
  await page.route('**/api/v1/**', (route) => {
    if (route.request().url().includes('/users/me')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          code: 0, message: 'ok',
          data: { id: 1, username: 'probe', email: 'probe@example.com', is_superuser: true, is_org_admin: true, organization_id: 1 },
        }),
      })
    }
    return route.abort()
  })
  await page.addInitScript(() => {
    const exp = Date.now() + 86400000
    localStorage.setItem('docmind_token', 'probe-token')
    localStorage.setItem('docmind_token_expires', String(exp))
    localStorage.setItem('user_info', JSON.stringify({
      id: 1, username: 'probe', email: 'probe@example.com',
      is_superuser: true, is_org_admin: true, organization_id: 1,
    }))
  })
  await page.goto(BASE + '/dashboard', { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(2500)
  const hasSidebar = await page.locator('[data-testid="sidebar-collapse-toggle"]').count()
  if (hasSidebar) {
    await page.screenshot({ path: OUT + '/sidebar-expanded-1440.png' })
    await page.locator('[data-testid="sidebar-collapse-toggle"]').click()
    await page.waitForTimeout(400)
    await page.screenshot({ path: OUT + '/sidebar-collapsed-1440.png' })
    const expandVisible = await page.locator('[data-testid="sidebar-expand-toggle"]').isVisible()
    report['sidebar'] = { collapseToggleFound: true, expandToggleVisibleAfterCollapse: expandVisible }
  } else {
    await page.screenshot({ path: OUT + '/sidebar-1440-fallback.png' })
    report['sidebar'] = { collapseToggleFound: false, url: page.url(), logs: logs.slice(0, 10) }
  }
  await ctx.close()
}

await browser.close()
console.log(JSON.stringify(report, null, 2))
