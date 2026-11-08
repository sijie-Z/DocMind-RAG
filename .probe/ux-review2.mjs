/** Overflow-scenario probe: stop-button clearance, scroll jump on stop, back-to-bottom. */
import { createRequire } from 'node:module'
import { mkdirSync, writeFileSync } from 'node:fs'
const require = createRequire('D:/Desktop/MyProjects/AI_projects/DocMind/_scratch/wt-firsthome/frontend/package.json')
const { chromium } = require('@playwright/test')

const BASE = 'http://localhost:5187'
const OUT = 'D:/Desktop/MyProjects/AI_projects/DocMind/_scratch/wt-firsthome/.probe/ux-review'
mkdirSync(OUT, { recursive: true })
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const results = {}

const USER = {
  id: 1, username: 'demo_reviewer', email: 'demo@example.com', fullName: '演示审阅员',
  nickname: '演示审阅员', isAdmin: false, is_superuser: false, is_org_admin: false,
  organization_id: 1, createdAt: '2026-01-01T00:00:00Z',
  organizations: [{ id: 1, name: '演示组织' }], preferences: { theme: 'light' },
}
function json(route, payload) {
  return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(payload) })
}
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
    const url = new URL(route.request().url()); const p = url.pathname
    if (p.endsWith('/chat/stream')) { await new Promise(() => {}); return }
    if (p.endsWith('/users/me')) return json(route, { success: true, data: USER })
    if (p.endsWith('/users/me/organizations')) return json(route, { success: true, data: { organizations: [], current_organization_id: null } })
    if (p.endsWith('/user/settings')) return json(route, { success: true, data: {} })
    if (p.endsWith('/notifications/unread-count')) return json(route, { success: true, data: { count: 0 } })
    if (/\/conversations(\?|$)/.test(p)) return json(route, { success: true, data: { data: [], total: 0, page: 1, page_size: 50 } })
    if (p.endsWith('/commands')) return json(route, { success: true, data: { commands: [] } })
    return json(route, { success: true, data: null })
  })
  return { ctx, page, errors }
}

const LONG = '请核验这份合同的关键条款差异。'.repeat(60)

async function measure(page, label) {
  return page.evaluate((label) => {
    const scroller = document.querySelector('div[class*="pt-20 pb-40"]')
    const footer = document.querySelector('footer')
    scroller.style.scrollBehavior = 'auto'
    scroller.scrollTop = scroller.scrollHeight
    const inner = scroller.querySelector('.max-w-3xl')
    const sr = scroller.getBoundingClientRect()
    const fr = footer.getBoundingClientRect()
    const stopBtn = [...footer.querySelectorAll('button')].find((b) => (b.textContent || '').includes('停止'))
    const br = stopBtn ? stopBtn.getBoundingClientRect() : null
    const children = inner ? [...inner.children] : []
    const rects = children
      .map((el, i) => {
        const r = el.getBoundingClientRect()
        return {
          i,
          cls: String(el.className).slice(0, 60),
          top: Math.round(r.top), bottom: Math.round(r.bottom),
          left: Math.round(r.left), right: Math.round(r.right),
          h: Math.round(r.height),
          text: (el.textContent || '').trim().slice(0, 20),
        }
      })
      .filter((r) => r.h > 0)
    let contentBottom = -Infinity
    let bottomEl = null
    for (const r of rects) { if (r.bottom > contentBottom) { contentBottom = r.bottom; bottomEl = r } }
    const intersect = (a, b) => {
      if (!a || !b) return null
      const x = Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left))
      const y = Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top))
      return x > 0 && y > 0 ? { w: Math.round(x), h: Math.round(y) } : null
    }
    const overlaps = br
      ? rects.map((r) => ({ i: r.i, cls: r.cls, text: r.text, inter: intersect(br, r) })).filter((x) => x.inter)
      : []
    const bubbles = [...inner.querySelectorAll('[class*="group/bubble"]')].map((el) => {
      const r = el.getBoundingClientRect()
      return { top: Math.round(r.top), bottom: Math.round(r.bottom), left: Math.round(r.left), right: Math.round(r.right), h: Math.round(r.height) }
    })
    const lastBubble = bubbles.length ? bubbles[bubbles.length - 1] : null
    const backBtn = [...scroller.querySelectorAll('button')].find((x) => (x.textContent || '').includes('回到底部'))
    return {
      label,
      viewport: { w: innerWidth, h: innerHeight },
      paddingBottom: getComputedStyle(scroller).paddingBottom,
      scrollTop: Math.round(scroller.scrollTop),
      scrollHeight: scroller.scrollHeight,
      clientHeight: scroller.clientHeight,
      distToBottom: Math.round(scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight),
      scroller: { top: Math.round(sr.top), bottom: Math.round(sr.bottom) },
      footer: { top: Math.round(fr.top), height: Math.round(fr.height) },
      stop: br ? { top: Math.round(br.top), bottom: Math.round(br.bottom), left: Math.round(br.left), right: Math.round(br.right) } : null,
      contentBottom,
      bottomEl,
      clearance: br ? Math.round(br.top - contentBottom) : null,
      overlaps,
      lastBubble,
      backBtn: backBtn ? (() => { const r = backBtn.getBoundingClientRect(); return { top: Math.round(r.top), bottom: Math.round(r.bottom) } })() : null,
    }
  }, label)
}

async function run() {
  const browser = await chromium.launch({
    executablePath: 'C:/Users/lenovo/AppData/Local/ms-playwright/chromium-1248/chrome-win64/chrome.exe',
  })
  for (const [w, h, dark, tag] of [[1440, 900, false, '1440'], [768, 1024, false, '768'], [375, 812, false, '375'], [1440, 900, true, '1440-dark'], [375, 812, true, '375-dark']]) {
    try {
      const { ctx, page, errors } = await newPage(browser, { width: w, height: h }, dark)
      await page.goto(BASE + '/chat', { waitUntil: 'domcontentloaded' })
      await page.waitForSelector('.composer-card textarea', { timeout: 30000 })
      await sleep(500)
      await page.locator('.composer-card textarea').fill(LONG)
      await sleep(200)
      await page.locator('footer button', { hasText: '发送' }).last().click()
      await page.waitForSelector('[data-testid="generation-loading"]', { timeout: 8000 })
      await sleep(600)
      const loading = await measure(page, tag + '-loading')
      // scroll up to reveal back-to-bottom, then re-scroll to bottom and measure again
      const scrollTest = await page.evaluate(() => {
        const s = document.querySelector('div[class*="pt-20 pb-40"]')
        s.style.scrollBehavior = 'auto'
        s.scrollTop = Math.max(0, s.scrollTop - 500)
        s.dispatchEvent(new Event('scroll'))
        return s.scrollTop
      })
      await sleep(400)
      const backVisible = await page.evaluate(() => [...document.querySelectorAll('button')].some((b) => (b.textContent || '').includes('回到底部')))
      const backPos = await page.evaluate(() => {
        const b = [...document.querySelectorAll('button')].find((x) => (x.textContent || '').includes('回到底部'))
        if (!b) return null
        const r = b.getBoundingClientRect()
        const s = document.querySelector('div[class*="pt-20 pb-40"]')
        const f = document.querySelector('footer')
        const sr = s.getBoundingClientRect(); const fr = f.getBoundingClientRect()
        return { top: Math.round(r.top), bottom: Math.round(r.bottom), scrollerBottom: Math.round(sr.bottom), footerTop: Math.round(fr.top) }
      })
      await page.screenshot({ path: OUT + `/overflow-${tag}-loading.png` })
      // back to bottom, record last bubble position, then stop and compare
      const before = await page.evaluate(() => {
        const s = document.querySelector('div[class*="pt-20 pb-40"]')
        s.style.scrollBehavior = 'auto'
        s.scrollTop = s.scrollHeight
        const bubbles = [...s.querySelectorAll('[class*="group/bubble"]')]
        const last = bubbles[bubbles.length - 1]
        return { scrollTop: Math.round(s.scrollTop), lastTop: last ? Math.round(last.getBoundingClientRect().top) : null, lastBottom: last ? Math.round(last.getBoundingClientRect().bottom) : null }
      })
      await page.locator('footer button', { hasText: '停止' }).first().click()
      await sleep(800)
      const after = await measure(page, tag + '-after-stop')
      const delta = {
        scrollTop: after.scrollTop - before.scrollTop,
        lastTop: after.lastBubble && before.lastTop != null ? after.lastBubble.top - before.lastTop : null,
        lastBottom: after.lastBubble && before.lastBottom != null ? after.lastBubble.bottom - before.lastBottom : null,
      }
      results[tag] = { loading, scrollTest, backVisible, backPos, before, after, delta, errors: errors.slice(0, 6) }
      await page.screenshot({ path: OUT + `/overflow-${tag}-after-stop.png` })
      await ctx.close()
    } catch (e) {
      results[tag] = { error: String(e) }
    }
  }
  await browser.close()
  writeFileSync(OUT + '/results-overflow.json', JSON.stringify(results, null, 2))
  console.log(JSON.stringify(results, null, 2))
}
run().catch((e) => { console.error('FATAL', e); process.exit(1) })
