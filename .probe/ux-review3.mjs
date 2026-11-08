/** Clean bottom-state overlap probe + language popover keyboard + handoff focus styling. */
import { createRequire } from 'node:module'
import { mkdirSync, writeFileSync } from 'node:fs'
const require = createRequire('D:/Desktop/MyProjects/AI_projects/DocMind/_scratch/wt-firsthome/frontend/package.json')
const { chromium } = require('@playwright/test')
const BASE = 'http://localhost:5187'
const OUT = 'D:/Desktop/MyProjects/AI_projects/DocMind/_scratch/wt-firsthome/.probe/ux-review'
mkdirSync(OUT, { recursive: true })
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const results = {}
const USER = { id: 1, username: 'demo_reviewer', email: 'demo@example.com', fullName: '演示审阅员', nickname: '演示审阅员', isAdmin: false, is_superuser: false, is_org_admin: false, organization_id: 1, createdAt: '2026-01-01T00:00:00Z', organizations: [{ id: 1, name: '演示组织' }], preferences: { theme: 'light' } }
const json = (route, payload) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(payload) })
async function newPage(browser, viewport, dark = false) {
  const ctx = await browser.newContext({ viewport, colorScheme: dark ? 'dark' : 'light' })
  await ctx.addInitScript(({ user, theme }) => {
    localStorage.setItem('docmind_token', 'fake-review-token')
    localStorage.setItem('docmind_token_expires', String(Date.now() + 7 * 86400000))
    localStorage.setItem('user_info', JSON.stringify(user))
    localStorage.setItem('theme', theme)
    localStorage.setItem('language', 'zh')
  }, { user: USER, theme: dark ? 'dark' : 'light' })
  const page = await ctx.newPage()
  const errors = []
  page.on('pageerror', (e) => errors.push('PAGEERROR ' + String(e).slice(0, 200)))
  await page.route('**/api/v1/**', async (route) => {
    const p = new URL(route.request().url()).pathname
    if (p.endsWith('/chat/stream')) { await new Promise(() => {}); return }
    if (p.endsWith('/users/me')) return json(route, { success: true, data: USER })
    if (p.endsWith('/users/me/organizations')) return json(route, { success: true, data: { organizations: [], current_organization_id: null } })
    if (p.endsWith('/user/settings')) return json(route, { success: true, data: {} })
    if (p.endsWith('/notifications/unread-count')) return json(route, { success: true, data: { count: 0 } })
    if (/\/conversations(\?|$)/.test(p)) return json(route, { success: true, data: { data: [], total: 0, page: 1, page_size: 50 } })
    return json(route, { success: true, data: null })
  })
  return { ctx, page, errors }
}
const LONG = '请核验这份合同的关键条款差异。'.repeat(60)

async function bottomState(page) {
  await page.evaluate(() => {
    const s = document.querySelector('div[class*="pt-20 pb-40"]')
    s.style.scrollBehavior = 'auto'
    s.scrollTop = s.scrollHeight
  })
  await sleep(400)
  await page.evaluate(() => {
    const s = document.querySelector('div[class*="pt-20 pb-40"]')
    s.scrollTop = s.scrollHeight
  })
  await sleep(200)
  return page.evaluate(() => {
    const s = document.querySelector('div[class*="pt-20 pb-40"]')
    const f = document.querySelector('footer')
    const sr = s.getBoundingClientRect(); const fr = f.getBoundingClientRect()
    const stop = [...f.querySelectorAll('button')].find((b) => (b.textContent || '').includes('停止'))
    const br = stop ? stop.getBoundingClientRect() : null
    const loadingRow = s.querySelector('[data-testid="generation-loading"]')
    const lr = loadingRow ? loadingRow.getBoundingClientRect() : null
    const avatar = loadingRow ? loadingRow.firstElementChild.getBoundingClientRect() : null
    const dots = loadingRow ? loadingRow.querySelector('.flex.space-x-1\\.5')?.getBoundingClientRect() : null
    const bubbles = [...s.querySelectorAll('[class*="group/bubble"]')].map((el) => {
      const r = el.getBoundingClientRect()
      return { top: Math.round(r.top), bottom: Math.round(r.bottom), left: Math.round(r.left), right: Math.round(r.right) }
    })
    const inter = (a, b) => {
      if (!a || !b) return null
      const w = Math.min(a.right, b.right) - Math.max(a.left, b.left)
      const h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top)
      return w > 0 && h > 0 ? { w: Math.round(w), h: Math.round(h) } : null
    }
    const stopR = br ? { top: br.top, bottom: br.bottom, left: br.left, right: br.right } : null
    const under = []
    if (br) {
      for (const [fx, fy, label] of [[0.5, 0.1, 'top-center'], [0.15, 0.5, 'left-mid'], [0.85, 0.5, 'right-mid'], [0.5, 0.5, 'center']]) {
        const x = br.left + br.width * fx, y = br.top + br.height * fy
        const els = document.elementsFromPoint(x, y).slice(0, 4).map((el) => ({
          tag: el.tagName,
          cls: String(el.className).slice(0, 70),
          text: (el.textContent || '').trim().slice(0, 24),
        }))
        under.push({ label, x: Math.round(x), y: Math.round(y), els })
      }
    }
    return {
      viewport: { w: innerWidth, h: innerHeight },
      padding: getComputedStyle(s).paddingBottom,
      scrollTop: Math.round(s.scrollTop), scrollHeight: s.scrollHeight, clientHeight: s.clientHeight,
      distToBottom: Math.round(s.scrollHeight - s.scrollTop - s.clientHeight),
      footer: { top: Math.round(fr.top), height: Math.round(fr.height) },
      stop: stopR ? { top: Math.round(stopR.top), bottom: Math.round(stopR.bottom), left: Math.round(stopR.left), right: Math.round(stopR.right) } : null,
      loadingRow: lr ? { top: Math.round(lr.top), bottom: Math.round(lr.bottom), left: Math.round(lr.left), right: Math.round(lr.right) } : null,
      loadingAvatar: avatar ? { top: Math.round(avatar.top), bottom: Math.round(avatar.bottom), left: Math.round(avatar.left), right: Math.round(avatar.right) } : null,
      loadingDots: dots ? { top: Math.round(dots.top), bottom: Math.round(dots.bottom), left: Math.round(dots.left), right: Math.round(dots.right) } : null,
      bubbleOverlaps: bubbles.map((b) => ({ bubble: b, inter: inter(stopR, b) })).filter((x) => x.inter),
      avatarOverlap: avatar ? inter(stopR, avatar) : null,
      dotsOverlap: dots ? inter(stopR, dots) : null,
      loadingRowOverlap: lr ? inter(stopR, lr) : null,
      lastBubble: bubbles.length ? bubbles[bubbles.length - 1] : null,
      under,
    }
  })
}

async function run() {
  const browser = await chromium.launch({ executablePath: 'C:/Users/lenovo/AppData/Local/ms-playwright/chromium-1248/chrome-win64/chrome.exe' })

  for (const [w, h, tag] of [[1440, 900, '1440'], [768, 1024, '768'], [375, 812, '375']]) {
    try {
      const { ctx, page, errors } = await newPage(browser, { width: w, height: h })
      await page.goto(BASE + '/chat', { waitUntil: 'domcontentloaded' })
      await page.waitForSelector('.composer-card textarea', { timeout: 30000 })
      await sleep(500)
      await page.locator('.composer-card textarea').fill(LONG)
      await sleep(150)
      await page.locator('footer button', { hasText: '发送' }).last().click()
      await page.waitForSelector('[data-testid="generation-loading"]', { timeout: 8000 })
      await sleep(1200)
      const loading = await bottomState(page)
      await page.screenshot({ path: OUT + `/bottom-${tag}-loading.png` })
      await page.locator('footer button', { hasText: '停止' }).first().click()
      await sleep(1600)
      const after = await page.evaluate(() => {
        const s = document.querySelector('div[class*="pt-20 pb-40"]')
        s.style.scrollBehavior = 'auto'
        s.scrollTop = s.scrollHeight
        return { scrollTop: Math.round(s.scrollTop), scrollHeight: s.scrollHeight, clientHeight: s.clientHeight }
      })
      await sleep(400)
      const after2 = await bottomState(page)
      results[tag] = { loading, afterStopScroll: after, after: after2, errors: errors.slice(0, 5) }
      await page.screenshot({ path: OUT + `/bottom-${tag}-after-stop.png` })
      await ctx.close()
    } catch (e) { results[tag] = { error: String(e) } }
  }

  // Language popover keyboard behavior
  try {
    const { ctx, page } = await newPage(browser, { width: 1440, height: 900 })
    await page.goto(BASE + '/chat', { waitUntil: 'domcontentloaded' })
    await page.waitForSelector('.composer-card textarea', { timeout: 30000 })
    await sleep(500)
    const globe = page.locator('button[aria-label="语言设置"]').first()
    await globe.focus()
    const focusBefore = await page.evaluate(() => document.activeElement?.getAttribute('aria-label'))
    await page.keyboard.press('Enter')
    await sleep(400)
    const openByKeyboard = await page.evaluate(() => [...document.querySelectorAll('.n-popover')].some((el) => el.offsetParent !== null))
    await page.keyboard.press('Escape')
    await sleep(300)
    const closedByEscape = await page.evaluate(() => ![...document.querySelectorAll('.n-popover')].some((el) => el.offsetParent !== null))
    // reopen and pick via keyboard
    await globe.focus()
    await page.keyboard.press('Enter')
    await sleep(400)
    const enBtn = page.locator('.n-popover button', { hasText: 'English' }).first()
    await enBtn.focus()
    await page.keyboard.press('Enter')
    await sleep(900)
    const afterSwitch = await page.evaluate(() => ({
      popoverOpen: [...document.querySelectorAll('.n-popover')].some((el) => el.offsetParent !== null),
      active: document.activeElement ? { tag: document.activeElement.tagName, aria: document.activeElement.getAttribute?.('aria-label'), text: (document.activeElement.textContent || '').trim().slice(0, 20) } : null,
      htmlLang: document.documentElement.lang,
    }))
    results.languageKeyboard = { focusBefore, openByKeyboard, closedByEscape, afterSwitch }
    await ctx.close()
  } catch (e) { results.languageKeyboard = { error: String(e) } }

  // Handoff focus styling
  try {
    const { ctx, page } = await newPage(browser, { width: 1440, height: 900 })
    await page.goto(BASE + '/chat', { waitUntil: 'domcontentloaded' })
    await page.waitForSelector('.composer-card textarea', { timeout: 30000 })
    await sleep(400)
    await page.locator('.composer-card textarea').fill('测试接力提示词')
    await page.locator('[data-testid="agent-workbench-entry"]').click()
    await page.waitForSelector('textarea.agent-textarea', { timeout: 15000 })
    await sleep(600)
    results.handoffFocusStyle = await page.evaluate(() => {
      const ta = document.querySelector('textarea.agent-textarea')
      const cs = getComputedStyle(ta)
      return {
        focused: document.activeElement === ta,
        outline: cs.outlineStyle + ' ' + cs.outlineWidth + ' ' + cs.outlineColor,
        border: cs.borderTopWidth + ' ' + cs.borderTopStyle + ' ' + cs.borderTopColor,
        caret: ta.selectionStart,
        valueLen: ta.value.length,
        ariaLabel: ta.getAttribute('aria-label'),
        placeholder: ta.getAttribute('placeholder'),
      }
    })
    await ctx.close()
  } catch (e) { results.handoffFocusStyle = { error: String(e) } }

  await browser.close()
  writeFileSync(OUT + '/results-clean.json', JSON.stringify(results, null, 2))
  console.log(JSON.stringify(results, null, 2))
}
run().catch((e) => { console.error('FATAL', e); process.exit(1) })
