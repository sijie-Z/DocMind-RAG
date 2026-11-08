/**
 * UX review probe for fix/chat-ux-pack (chat handoff / language popover /
 * stop-button clearance / header icon / scroll behavior).
 * Read-only against the app: all backend calls are stubbed in-browser.
 */
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
  id: 1,
  username: 'demo_reviewer',
  email: 'demo@example.com',
  fullName: '演示审阅员',
  full_name: '演示审阅员',
  nickname: '演示审阅员',
  isAdmin: false,
  is_superuser: false,
  is_org_admin: false,
  organization_id: 1,
  createdAt: '2026-01-01T00:00:00Z',
  organizations: [{ id: 1, name: '演示组织' }],
  preferences: { theme: 'light' },
}

function json(route, payload, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(payload),
  })
}

async function installStubs(page, opts = {}) {
  const heldStreams = []
  await page.route('**/api/v1/**', async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    const p = url.pathname
    // Hold chat SSE stream open -> isLoading stays true
    if (p.endsWith('/chat/stream')) {
      heldStreams.push(route)
      await new Promise(() => {})
      return
    }
    if (opts.holdAgentChat && p.includes('/agent/chat')) {
      heldStreams.push(route)
      await new Promise(() => {})
      return
    }
    if (p.endsWith('/users/me')) return json(route, { success: true, data: USER })
    if (p.endsWith('/users/me/organizations'))
      return json(route, { success: true, data: { organizations: [], current_organization_id: null } })
    if (p.endsWith('/user/settings')) return json(route, { success: true, data: {} })
    if (p.endsWith('/notifications/unread-count')) return json(route, { success: true, data: { count: 0 } })
    if (/\/conversations(\?|$)/.test(p))
      return json(route, { success: true, data: { data: [], total: 0, page: 1, page_size: 50 } })
    if (p.endsWith('/commands'))
      return json(route, { success: true, data: { commands: [] } })
    if (p.includes('/organizations/') && p.endsWith('/settings'))
      return json(route, { success: true, data: { settings: {} } })
    return json(route, { success: true, data: null })
  })
  return heldStreams
}

async function newPage(browser, { width, height }, { dark = false } = {}) {
  const ctx = await browser.newContext({
    viewport: { width, height },
    colorScheme: dark ? 'dark' : 'light',
  })
  await ctx.addInitScript(
    ({ user, theme }) => {
      localStorage.setItem('docmind_token', 'fake-review-token')
      localStorage.setItem('docmind_token_expires', String(Date.now() + 7 * 86400000))
      localStorage.setItem('user_info', JSON.stringify(user))
      localStorage.setItem('theme', theme)
      localStorage.setItem('language', 'zh')
    },
    { user: USER, theme: dark ? 'dark' : 'light' },
  )
  const page = await ctx.newPage()
  const errors = []
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(m.text().slice(0, 240))
  })
  page.on('pageerror', (e) => errors.push('PAGEERROR ' + String(e).slice(0, 240)))
  const held = await installStubs(page)
  return { ctx, page, errors, held }
}

async function gotoChat(page) {
  await page.goto(BASE + '/chat', { waitUntil: 'domcontentloaded' })
  await page.waitForSelector('.composer-card textarea', { timeout: 30000 })
  await sleep(600)
}

async function measureChatLayout(page, label) {
  return page.evaluate((label) => {
    const scroller = document.querySelector('div[class*="pt-20 pb-40"]')
    const footer = document.querySelector('footer')
    if (!scroller || !footer) return { error: 'missing scroller/footer' }
    // measure at absolute bottom, instant scroll
    const prev = scroller.style.scrollBehavior
    scroller.style.scrollBehavior = 'auto'
    scroller.scrollTop = scroller.scrollHeight
    const inner = scroller.querySelector('.max-w-3xl')
    const contentEls = inner ? [...inner.children] : []
    let contentBottom = -Infinity
    let contentTop = Infinity
    for (const el of contentEls) {
      const r = el.getBoundingClientRect()
      if (r.height < 1) continue
      contentBottom = Math.max(contentBottom, r.bottom)
      contentTop = Math.min(contentTop, r.top)
    }
    const stopBtn = [...footer.querySelectorAll('button')].find((b) => (b.textContent || '').includes('停止'))
    const sr = scroller.getBoundingClientRect()
    const fr = footer.getBoundingClientRect()
    const br = stopBtn ? stopBtn.getBoundingClientRect() : null
    const cs = getComputedStyle(scroller)
    const out = {
      label,
      viewport: { w: innerWidth, h: innerHeight },
      paddingBottom: cs.paddingBottom,
      paddingTop: cs.paddingTop,
      scrollTop: Math.round(scroller.scrollTop),
      scrollHeight: scroller.scrollHeight,
      clientHeight: scroller.clientHeight,
      distToBottom: Math.round(scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight),
      scroller: { top: Math.round(sr.top), bottom: Math.round(sr.bottom), height: Math.round(sr.height) },
      footer: { top: Math.round(fr.top), bottom: Math.round(fr.bottom), height: Math.round(fr.height), position: getComputedStyle(footer).position },
      content: { top: Math.round(contentTop), bottom: Math.round(contentBottom) },
      gapContentToFooter: Math.round(fr.top - contentBottom),
      stopBtn: br
        ? {
            top: Math.round(br.top),
            bottom: Math.round(br.bottom),
            height: Math.round(br.height),
            width: Math.round(br.width),
            clearanceAboveContent: Math.round(br.top - contentBottom),
            offsetAboveFooterTop: Math.round(fr.top - br.top),
          }
        : null,
      backToBottom: (() => {
        const b = [...scroller.querySelectorAll('button')].find((x) => (x.textContent || '').includes('回到底部'))
        if (!b) return null
        const r = b.getBoundingClientRect()
        return { top: Math.round(r.top), bottom: Math.round(r.bottom), offsetAboveScrollerBottom: Math.round(sr.bottom - r.bottom) }
      })(),
    }
    scroller.style.scrollBehavior = prev
    return out
  }, label)
}

async function sendAndHold(page) {
  const ta = page.locator('.composer-card textarea')
  await ta.fill('请核验这两份合同的关键条款差异')
  await sleep(120)
  const send = page.locator('footer button', { hasText: '发送' }).last()
  await send.click()
  await page.waitForSelector('[data-testid="generation-loading"]', { timeout: 8000 })
  await sleep(500)
}

async function run() {
  const browser = await chromium.launch({
    executablePath: 'C:/Users/lenovo/AppData/Local/ms-playwright/chromium-1248/chrome-win64/chrome.exe',
  })

  // ── Phase 1: 1440 light – stop button clearance + scroll behavior ────────
  try {
    const { ctx, page, errors } = await newPage(browser, { width: 1440, height: 900 })
    await gotoChat(page)
    results.emptyState = await page.evaluate(() => ({
      hasWelcome: !!document.querySelector('.composer-card'),
      bodyScrollWidth: document.documentElement.scrollWidth,
      innerWidth,
    }))
    await page.screenshot({ path: OUT + '/chat-1440-empty.png' })

    await sendAndHold(page)
    results.layout1440Loading = await measureChatLayout(page, '1440-loading')
    await page.screenshot({ path: OUT + '/chat-1440-loading.png' })

    // stop -> padding should revert, view should stay at bottom
    await page.locator('footer button', { hasText: '停止' }).first().click()
    await sleep(800)
    results.layout1440AfterStop = await measureChatLayout(page, '1440-after-stop')
    results.afterStop = await page.evaluate(() => {
      const scroller = document.querySelector('div[class*="pt-20 pb-40"]')
      const back = [...document.querySelectorAll('button')].some((b) => (b.textContent || '').includes('回到底部'))
      return { padding: getComputedStyle(scroller).paddingBottom, backToBottomVisible: back }
    })
    await page.screenshot({ path: OUT + '/chat-1440-after-stop.png' })
    results.errors1440 = errors.slice(0, 12)
    await ctx.close()
  } catch (e) {
    results.phase1Error = String(e)
  }

  // ── Phase 2: language popover (delayed chunk + failure) ──────────────────
  try {
    const { ctx, page, errors } = await newPage(browser, { width: 1440, height: 900 })
    // delay the en locale chunk by 1500ms
    await page.route('**/src/locales/en.ts*', async (route) => {
      await sleep(1500)
      await route.continue()
    })
    await page.goto(BASE + '/chat', { waitUntil: 'domcontentloaded' })
    await page.waitForSelector('.composer-card textarea', { timeout: 30000 })
    await sleep(500)
    const globe = page.locator('button[aria-label="语言设置"]').first()
    await globe.click()
    await sleep(300)
    const popoverBefore = await page.evaluate(() => {
      const pop = [...document.querySelectorAll('.n-popover')].find((el) => el.offsetParent !== null)
      return { open: !!pop, text: pop ? pop.textContent.trim().slice(0, 80) : '' }
    })
    const t0 = Date.now()
    await page.locator('.n-popover button', { hasText: 'English' }).first().click()
    await sleep(200)
    const during = await page.evaluate(() => {
      const pop = [...document.querySelectorAll('.n-popover')].find((el) => el.offsetParent !== null)
      return {
        popoverOpen: !!pop,
        anySpinner: !!(pop && pop.querySelector('.n-spin, [class*="spin"], [class*="loading"]')),
        anyDisabled: !!(pop && pop.querySelector('button[disabled]')),
      }
    })
    await page.waitForFunction(
      () => ![...document.querySelectorAll('.n-popover')].some((el) => el.offsetParent !== null),
      null,
      { timeout: 8000 },
    ).catch(() => {})
    const closeMs = Date.now() - t0
    results.language = {
      popoverBefore,
      duringLoad: during,
      closedAfterMs: closeMs,
      nowEnglish: await page.evaluate(() => document.documentElement.lang || navigator.language),
      errors: errors.slice(0, 10),
    }
    await ctx.close()
  } catch (e) {
    results.phase2Error = String(e)
  }

  // language failure path (abort chunk)
  try {
    const { ctx, page } = await newPage(browser, { width: 1440, height: 900 })
    await page.route('**/src/locales/ja.ts*', (route) => route.abort())
    await page.goto(BASE + '/chat', { waitUntil: 'domcontentloaded' })
    await page.waitForSelector('.composer-card textarea', { timeout: 30000 })
    await sleep(500)
    await page.locator('button[aria-label="语言设置"]').first().click()
    await sleep(300)
    await page.locator('.n-popover button', { hasText: '日本語' }).first().click()
    await sleep(2000)
    results.languageFailure = await page.evaluate(() => {
      const pop = [...document.querySelectorAll('.n-popover')].find((el) => el.offsetParent !== null)
      const msgs = [...document.querySelectorAll('.n-message')].map((m) => m.textContent.trim().slice(0, 100))
      return { popoverStillOpen: !!pop, toasts: msgs, htmlLang: document.documentElement.lang }
    })
    await ctx.close()
  } catch (e) {
    results.phase2bError = String(e)
  }

  // ── Phase 3: chat -> agent handoff ───────────────────────────────────────
  try {
    const { ctx, page, errors } = await newPage(browser, { width: 1440, height: 900 })
    await gotoChat(page)
    const prompt = '请核验这两份合同的关键条款差异'
    await page.locator('.composer-card textarea').fill(prompt)
    await sleep(120)
    const postBefore = []
    page.on('request', (r) => {
      if (r.method() === 'POST' && r.url().includes('/api/v1/')) postBefore.push(r.url())
    })
    await page.locator('[data-testid="agent-workbench-entry"]').click()
    await page.waitForURL('**/agent**', { timeout: 15000 })
    await page.waitForSelector('textarea.agent-textarea', { timeout: 15000 })
    await sleep(700)
    results.handoff = await page.evaluate(() => {
      const ta = document.querySelector('textarea.agent-textarea')
      const active = document.activeElement
      return {
        url: location.pathname + location.search,
        value: ta.value,
        focused: active === ta,
        activeTag: active ? active.tagName + (active.className ? '.' + String(active.className).slice(0, 40) : '') : null,
        caret: { start: ta.selectionStart, end: ta.selectionEnd, len: ta.value.length },
        textareaHeight: Math.round(ta.getBoundingClientRect().height),
        toastTexts: [...document.querySelectorAll('.n-message')].map((m) => m.textContent.trim().slice(0, 80)),
        bodyScrollWidth: document.documentElement.scrollWidth,
      }
    })
    results.handoffPosts = postBefore
    await page.screenshot({ path: OUT + '/agent-1440-prefilled.png' })

    // refresh -> empty input (accepted)
    await page.reload({ waitUntil: 'domcontentloaded' })
    await page.waitForSelector('textarea.agent-textarea', { timeout: 15000 })
    await sleep(500)
    results.handoffAfterRefresh = await page.evaluate(() => ({
      value: document.querySelector('textarea.agent-textarea').value,
      url: location.pathname + location.search,
    }))

    // same text handoff twice (back to chat, retype same, click again)
    await page.goto(BASE + '/chat', { waitUntil: 'domcontentloaded' })
    await page.waitForSelector('.composer-card textarea', { timeout: 15000 })
    await sleep(400)
    await page.locator('.composer-card textarea').fill(prompt)
    await page.locator('[data-testid="agent-workbench-entry"]').click()
    await page.waitForSelector('textarea.agent-textarea', { timeout: 15000 })
    await sleep(600)
    results.handoffSecondSameText = await page.evaluate(() => ({
      value: document.querySelector('textarea.agent-textarea').value,
      url: location.pathname + location.search,
    }))

    // empty input handoff -> no prefill, focus not stolen?
    await page.goto(BASE + '/chat', { waitUntil: 'domcontentloaded' })
    await page.waitForSelector('.composer-card textarea', { timeout: 15000 })
    await sleep(400)
    await page.locator('[data-testid="agent-workbench-entry"]').click()
    await page.waitForSelector('textarea.agent-textarea', { timeout: 15000 })
    await sleep(600)
    results.handoffEmpty = await page.evaluate(() => ({
      value: document.querySelector('textarea.agent-textarea').value,
      focused: document.activeElement === document.querySelector('textarea.agent-textarea'),
      activeTag: document.activeElement ? document.activeElement.tagName : null,
    }))
    results.errorsHandoff = errors.slice(0, 12)
    await ctx.close()
  } catch (e) {
    results.phase3Error = String(e)
  }

  // ── Phase 4: 375 mobile – header icons + stop clearance + dark ───────────
  for (const dark of [false, true]) {
    try {
      const { ctx, page, errors } = await newPage(browser, { width: 375, height: 812 }, { dark })
      await gotoChat(page)
      const tag = dark ? '375-dark' : '375'
      results[`header_${tag}`] = await page.evaluate(() => {
        const hamburger = document.querySelector('[data-testid="mobile-menu-toggle"]')
        const bubbles = document.querySelector('[data-testid="chat-sidebar-toggle"]')
        const rect = (el) => (el ? el.getBoundingClientRect().toJSON() : null)
        const svgOf = (el) => (el ? el.querySelector('svg')?.innerHTML.slice(0, 120) : null)
        return {
          hamburger: { aria: hamburger?.getAttribute('aria-label'), rect: rect(hamburger), svg: svgOf(hamburger) },
          bubbles: { aria: bubbles?.getAttribute('aria-label'), rect: rect(bubbles), svg: svgOf(bubbles) },
          activeElement: document.activeElement?.tagName,
        }
      })
      await page.screenshot({ path: OUT + `/chat-${tag}-header.png`, clip: { x: 0, y: 0, width: 375, height: 220 } })
      await sendAndHold(page)
      results[`layout_${tag}_loading`] = await measureChatLayout(page, `${tag}-loading`)
      await page.screenshot({ path: OUT + `/chat-${tag}-loading.png` })
      await page.locator('footer button', { hasText: '停止' }).first().click()
      await sleep(700)
      results[`layout_${tag}_afterStop`] = await measureChatLayout(page, `${tag}-after-stop`)
      results[`errors_${tag}`] = errors.slice(0, 10)
      await ctx.close()
    } catch (e) {
      results[`phase4_${dark ? 'dark' : 'light'}Error`] = String(e)
    }
  }

  // ── Phase 5: 768 tablet ──────────────────────────────────────────────────
  try {
    const { ctx, page, errors } = await newPage(browser, { width: 768, height: 1024 })
    await gotoChat(page)
    await sendAndHold(page)
    results.layout768Loading = await measureChatLayout(page, '768-loading')
    await page.screenshot({ path: OUT + '/chat-768-loading.png' })
    results.errors768 = errors.slice(0, 10)
    await ctx.close()
  } catch (e) {
    results.phase5Error = String(e)
  }

  // ── Phase 6: 1440 dark ───────────────────────────────────────────────────
  try {
    const { ctx, page, errors } = await newPage(browser, { width: 1440, height: 900 }, { dark: true })
    await gotoChat(page)
    await sendAndHold(page)
    results.layout1440DarkLoading = await measureChatLayout(page, '1440-dark-loading')
    results.darkApplied = await page.evaluate(() => ({
      htmlClass: document.documentElement.className,
      bodyBg: getComputedStyle(document.body).backgroundColor,
      footerBg: document.querySelector('footer') ? getComputedStyle(document.querySelector('footer')).backgroundImage.slice(0, 120) : null,
    }))
    await page.screenshot({ path: OUT + '/chat-1440-dark-loading.png' })
    results.errors1440Dark = errors.slice(0, 10)
    await ctx.close()
  } catch (e) {
    results.phase6Error = String(e)
  }

  await browser.close()
  writeFileSync(OUT + '/results.json', JSON.stringify(results, null, 2))
  console.log(JSON.stringify(results, null, 2))
}

run().catch((e) => {
  console.error('FATAL', e)
  process.exit(1)
})
