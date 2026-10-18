/**
 * E16 · 响应式与暗色打磨 判别用例（H1/H4/H5）。
 *
 * 三组判别点（修复前逐条为红）：
 * - H1 死 CSS：layouts/index.vue 的 `.sidebar-fixed` 断点块在模板中无任何引用，
 *   是死规则（移动端侧栏真实机制是 `max-md:*` 工具类）——删死规则、锁真实机制仍存活；
 * - H1 断点：search / organizations 两页有 1023px 与 767px 断点，窄屏下筛选栏
 *   与左树纵向堆叠（旧实现固定 w-72 / w-1/3+min-w-[300px]，无任何断点）；
 * - H4 暗色：ErrorBoundary 暗色覆盖必须是扁平选择器（旧写法
 *   `html.dark { .x {} }` 依赖原生 CSS 嵌套，构建链未配置降级，低版本
 *   WebView 整块丢弃 → 暗色下整页硬编码亮白），且暗色正文/标题对卡片底的
 *   WCAG 对比度 ≥ 4.5（旧 #64748b 对 #1e293b 仅约 3.0）；
 * - H5 暗色：firsthome 的 --c-* 令牌在暗色下重定义（深底亮字），正文/次要
 *   文字对比度 ≥ 4.5，顶栏/主按钮/品牌图标等硬编码亮底有暗色覆盖。
 *
 * 观测手法：静态读取 SFC 源码，解析 <style> 块中的扁平声明并做对比度计算。
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const ROOT = process.cwd()

function read(rel: string): string {
  return readFileSync(resolve(ROOT, rel), 'utf-8')
}

function styleBlock(src: string): string {
  const m = src.match(/<style[^>]*>([\s\S]*?)<\/style>/)
  expect(m, '应包含 <style> 块').toBeTruthy()
  return m![1]
}

/** 取 `@media (max-width: Npx)` 的块体（花括号配对） */
function mediaBlock(css: string, maxWidth: number): string | null {
  const idx = css.search(new RegExp(`@media\\s*\\(\\s*max-width:\\s*${maxWidth}px\\s*\\)\\s*\\{`))
  if (idx === -1) return null
  const open = css.indexOf('{', idx)
  let depth = 0
  for (let i = open; i < css.length; i += 1) {
    if (css[i] === '{') depth += 1
    else if (css[i] === '}') {
      depth -= 1
      if (depth === 0) return css.slice(open + 1, i)
    }
  }
  return null
}

/** 取扁平规则的声明表（不接受嵌套规则；selector 传入正则） */
function declarationsFor(css: string, selector: RegExp): Record<string, string> {
  // 先剥离 CSS 注释，避免注释里的冒号/分号污染声明解析
  const clean = css.replace(/\/\*[\s\S]*?\*\//g, '')
  const m = clean.match(new RegExp(`${selector.source}\\s*\\{([^}]*)\\}`))
  if (!m) return {}
  const decls: Record<string, string> = {}
  for (const part of m[1].split(';')) {
    const colon = part.indexOf(':')
    if (colon === -1) continue
    decls[part.slice(0, colon).trim()] = part.slice(colon + 1).trim()
  }
  return decls
}

// ── WCAG 2.x 对比度计算 ────────────────────────────────────────────────────
function srgbToLinear(channel: number): number {
  const c = channel / 255
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
}

function hexLuminance(hex: string): number {
  const m = hex.trim().match(/^#([0-9a-f]{6})$/i)
  expect(m, `应为 6 位十六进制色值，实际收到: ${hex}`).toBeTruthy()
  const n = parseInt(m![1], 16)
  return (
    0.2126 * srgbToLinear((n >> 16) & 0xff) +
    0.7152 * srgbToLinear((n >> 8) & 0xff) +
    0.0722 * srgbToLinear(n & 0xff)
  )
}

function contrastRatio(fg: string, bg: string): number {
  const [hi, lo] = [hexLuminance(fg), hexLuminance(bg)].sort((a, b) => b - a)
  return (hi + 0.05) / (lo + 0.05)
}

describe('E16-H1 · layouts 死 CSS 清理', () => {
  const layoutsSrc = read('src/layouts/index.vue')

  it('不再包含 .sidebar-fixed 死规则（模板无此类的任何引用）', () => {
    // 旧样式块: @media (max-width: 767px) { .sidebar-fixed { ... } } → 先红
    expect(layoutsSrc).not.toContain('sidebar-fixed')
  })

  it('移动端侧栏真实机制（max-md:* 工具类）仍在', () => {
    expect(layoutsSrc).toContain('max-md:fixed')
    expect(layoutsSrc).toContain('max-md:-translate-x-full')
  })
})

describe('E16-H1 · 核心页断点补齐（search / organizations）', () => {
  it('search 页具备 1023px 中间断点', () => {
    const css = styleBlock(read('src/views/search.vue'))
    // 旧实现 src/views/search.vue 无任何 @media → 先红
    expect(mediaBlock(css, 1023), '应存在 max-width: 1023px 断点').toBeTruthy()
  })

  it('search 页 767px 下筛选栏纵向堆叠到结果区上方（不再固定 w-72 挤占）', () => {
    const src = read('src/views/search.vue')
    const css = styleBlock(src)
    const mobile = mediaBlock(css, 767)
    expect(mobile, '应存在 max-width: 767px 断点').toBeTruthy()
    // 模板挂 class 钩子
    expect(src).toMatch(/class="[^"]*search-layout/)
    expect(src).toMatch(/class="[^"]*search-filter/)
    // 断点内：主轴改纵向、筛选栏占满宽度
    expect(mobile!).toMatch(/\.search-layout\s*\{[^}]*flex-direction:\s*column/)
    expect(mobile!).toMatch(/\.search-filter\s*\{[^}]*width:\s*100%/)
  })

  it('organizations 页 767px 下左树与列表纵向堆叠、解除 300px 最小宽', () => {
    const src = read('src/views/organizations/index.vue')
    const css = styleBlock(src)
    const mobile = mediaBlock(css, 767)
    expect(mobile, '应存在 max-width: 767px 断点').toBeTruthy()
    expect(src).toMatch(/class="[^"]*org-split/)
    expect(src).toMatch(/class="[^"]*org-tree-panel/)
    expect(mobile!).toMatch(/\.org-split\s*\{[^}]*flex-direction:\s*column/)
    expect(mobile!).toMatch(/\.org-tree-panel\s*\{[^}]*min-width:\s*0/)
  })

  it('organizations 页具备 1023px 中间断点（左树收窄）', () => {
    const css = styleBlock(read('src/views/organizations/index.vue'))
    const tablet = mediaBlock(css, 1023)
    expect(tablet, '应存在 max-width: 1023px 断点').toBeTruthy()
    expect(tablet!).toMatch(/\.org-tree-panel\s*\{[^}]*min-width:\s*240px/)
  })
})

describe('E16-H4 · ErrorBoundary 暗色令牌化', () => {
  const src = read('src/components/common/ErrorBoundary.vue')
  const css = styleBlock(src)

  it('暗色覆盖使用扁平选择器（不再依赖原生 CSS 嵌套块）', () => {
    // 旧实现 html.dark { .error-page {...} }：构建链未配置嵌套降级，
    // 低版本 WebView 整块丢弃 → 暗色下整页硬编码亮白。此断言先红。
    expect(css).not.toMatch(/html\.dark\s*\{/)
    expect(css).toMatch(/html\.dark\s+\.error-page\s*\{/)
  })

  it('暗色正文/标题/幽灵按钮对卡片底的对比度 ≥ 4.5', () => {
    const dark = declarationsFor(css, /html\.dark\s+\.error-page/)
    expect(dark['--eb-card-bg'], '应在暗色覆盖块中重定义 --eb-card-bg').toBeTruthy()
    expect(dark['--eb-desc'], '应在暗色覆盖块中重定义 --eb-desc').toBeTruthy()
    expect(dark['--eb-title'], '应在暗色覆盖块中重定义 --eb-title').toBeTruthy()
    expect(dark['--eb-ghost-fg'], '应在暗色覆盖块中重定义 --eb-ghost-fg').toBeTruthy()

    // 卡片底必须是深色
    expect(hexLuminance(dark['--eb-card-bg'])).toBeLessThan(0.1)
    // 旧值 #64748b 对 #1e293b 仅约 3.0 → 先红
    expect(contrastRatio(dark['--eb-desc'], dark['--eb-card-bg'])).toBeGreaterThanOrEqual(4.5)
    expect(contrastRatio(dark['--eb-title'], dark['--eb-card-bg'])).toBeGreaterThanOrEqual(4.5)
    expect(contrastRatio(dark['--eb-ghost-fg'], dark['--eb-card-bg'])).toBeGreaterThanOrEqual(4.5)
  })

  it('亮色基色也收敛为 --eb-* 令牌（暗色覆盖只改变量，不散落色值）', () => {
    const base = declarationsFor(css, /(?:^|\n)\.error-page/)
    expect(base['--eb-page-bg'], '基础 .error-page 应定义 --eb-page-bg').toBeTruthy()
    expect(base['--eb-card-bg'], '基础 .error-page 应定义 --eb-card-bg').toBeTruthy()
    expect(base['--eb-title'], '基础 .error-page 应定义 --eb-title').toBeTruthy()
  })
})

describe('E16-H5 · firsthome 暗色适配', () => {
  const src = read('src/views/firsthome/index.vue')
  const css = styleBlock(src)

  it('暗色下重定义 --c-* 令牌：深底亮字，正文与次要文字对比度 ≥ 4.5', () => {
    // 旧实现无任何暗色覆盖（--c-bg 恒为 #f8fafc）→ 先红
    const dark = declarationsFor(css, /html\.dark\s+\.landing/)
    expect(dark['--c-bg'], '应在 html.dark .landing 中重定义 --c-bg').toBeTruthy()
    expect(dark['--c-text'], '应重定义 --c-text').toBeTruthy()
    expect(dark['--c-text-secondary'], '应重定义 --c-text-secondary').toBeTruthy()
    expect(dark['--c-text-muted'], '应重定义 --c-text-muted').toBeTruthy()

    expect(hexLuminance(dark['--c-bg']), '暗色背景应为深色').toBeLessThan(0.05)
    expect(contrastRatio(dark['--c-text'], dark['--c-bg'])).toBeGreaterThanOrEqual(4.5)
    expect(contrastRatio(dark['--c-text-secondary'], dark['--c-bg'])).toBeGreaterThanOrEqual(4.5)
    expect(contrastRatio(dark['--c-text-muted'], dark['--c-bg'])).toBeGreaterThanOrEqual(4.5)
  })

  it('硬编码亮底有暗色覆盖：顶栏 / 品牌图标 / 主按钮 / 描边按钮 hover', () => {
    expect(css).toMatch(/html\.dark\s+\.nav\s*\{/)
    expect(css).toMatch(/html\.dark\s+\.brand-icon\s*\{/)
    expect(css).toMatch(/html\.dark\s+\.btn-primary\s*\{/)
    expect(css).toMatch(/html\.dark\s+\.btn-primary-sm\s*\{/)
    expect(css).toMatch(/html\.dark\s+\.btn-outline:hover\s*\{/)

    // 主按钮暗色底不得是亮色（旧实现 background: var(--c-text) 在暗色下发白、白字不可见）
    const primary = declarationsFor(css, /html\.dark\s+\.btn-primary/)
    const bg = (primary['background'] || '').match(/#[0-9a-f]{6}/i)?.[0] ?? ''
    expect(bg, 'html.dark .btn-primary 应给出深色/彩色背景').toBeTruthy()
    expect(hexLuminance(bg)).toBeLessThan(0.35)
  })

  it('渐变标题在暗色下换了高对比渐变（不沿用 #1e40af 深蓝）', () => {
    expect(css).toMatch(/html\.dark\s+\.hero-title-accent\s*\{/)
    const accent = declarationsFor(css, /html\.dark\s+\.hero-title-accent/)
    const gradient = accent['background-image'] || accent['background'] || ''
    expect(gradient, '暗色渐变应为亮蓝系').toMatch(/#(?:60a5fa|93c5fd|bfdbfe)/i)
  })
})
