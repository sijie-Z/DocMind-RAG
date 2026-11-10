/**
 * 设计令牌与动效原语判别用例。
 *
 * 修前：视图层消费 var(--c-primary/--c-text-2/--c-text-4) 共 97 处，但全仓只有首屏
 * 局部定义 surface/border，主色与灰阶令牌**无定义** → 面包屑 hover/灰阶静默失效；
 * 滚动条是浏览器默认灰（#c1c1c1），与纸墨调色板脱节；各页动效时长/缓动各写一套。
 *
 * 变异点：删掉 :root/.dark 的 --c-primary / --c-text-4 定义、把滚动条改回固定灰、
 * 删掉 .reveal 的 reduced-motion 兜底 → 对应断言红。
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const CSS = readFileSync(resolve(process.cwd(), 'src/styles/index.css'), 'utf-8')
const LOGIN = readFileSync(resolve(process.cwd(), 'src/views/login/index.vue'), 'utf-8')

/** 取出某个选择块的内容（到下一个同级右括号为止的近似扫描，够用且稳定） */
function block(selector: string): string {
  const start = CSS.indexOf(selector)
  if (start < 0) return ''
  const open = CSS.indexOf('{', start)
  let depth = 0
  for (let i = open; i < CSS.length; i++) {
    if (CSS[i] === '{') depth++
    else if (CSS[i] === '}') {
      depth--
      if (depth === 0) return CSS.slice(open + 1, i)
    }
  }
  return ''
}

describe('全局设计令牌（light/dark 双套）', () => {
  it(':root 与 .dark 都定义主色与四级灰阶（修前无定义 → 红）', () => {
    const light = block(':root {')
    const dark = block('.dark {')
    for (const token of ['--c-primary:', '--c-text-1:', '--c-text-2:', '--c-text-3:', '--c-text-4:', '--c-surface:', '--c-border:']) {
      expect(light, `:root 缺 ${token}`).toContain(token)
      expect(dark, `.dark 缺 ${token}`).toContain(token)
    }
    // 动作蓝真值不得漂移
    expect(light).toContain('--c-primary: #2563eb')
  })

  it('动效时长/缓动令牌齐全，供全站复用', () => {
    const light = block(':root {')
    for (const token of ['--dur-1:', '--dur-2:', '--dur-3:', '--dur-4:', '--ease-out:', '--ease-spring:']) {
      expect(light, `缺动效令牌 ${token}`).toContain(token)
    }
  })
})

describe('滚动条取调色板自身颜色', () => {
  it('thumb 与 hover 走令牌，不再写死浏览器灰', () => {
    const thumb = block('::-webkit-scrollbar-thumb {')
    const hover = block('::-webkit-scrollbar-thumb:hover {')
    expect(thumb).toContain('var(--c-scroll-thumb)')
    expect(hover).toContain('var(--c-scroll-thumb-hover)')
    expect(CSS).not.toContain('#c1c1c1')
    expect(CSS).not.toContain('#a8a8a8')
    // light/dark 各自定义滚动条令牌
    expect(block(':root {')).toContain('--c-scroll-thumb:')
    expect(block('.dark {')).toContain('--c-scroll-thumb:')
  })
})

describe('进场与微交互原语', () => {
  it('.reveal 有减少动态兜底（绝不把内容藏死）', () => {
    const reduced = CSS.slice(CSS.indexOf('@media (prefers-reduced-motion: reduce) {', CSS.indexOf('.reveal {')))
    expect(reduced).toContain('.reveal')
    expect(reduced).toContain('opacity: 1')
  })

  it('登录主 CTA 回到动作蓝，不再用 slate 灰（与全站 #2563eb 脱节）', () => {
    expect(LOGIN).toContain('login-cta')
    expect(LOGIN).not.toContain('bg-slate-800 hover:bg-slate-700')
    expect(LOGIN).toContain('#2563eb')
  })
})