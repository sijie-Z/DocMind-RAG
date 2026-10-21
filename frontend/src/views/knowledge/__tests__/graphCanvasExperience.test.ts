/**
 * E13 · 图谱画布体验判别用例（fitView / 缩放控件 / 标签防叠 / 暗色令牌）。
 *
 * 覆盖三组判别点（旧实现逐条为红）：
 * - fitView：数据加载后自动框住节点（旧实现 offset=0/scale=1，绝不放大的视图）；
 * - 缩放控件：放大/缩小/复位按钮行为，复位回到 fitView 变换（旧实现无控件）；
 * - 标签防叠：缩放低于阈值隐藏节点/关系标签（旧实现恒绘标签，密集时叠字）；
 * - 暗色令牌：draw() 内不得出现硬编码色字面量，标签色取自 naive 主题令牌
 *   （旧实现 #374151 在暗色画布上不可读）。
 *
 * 观测手法：jsdom 无真实 canvas —— 以 Proxy 记录 2d 上下文的全部调用/属性写入，
 * requestAnimationFrame 改为手动队列（pumpFrame 逐帧推进），从 translate/scale
 * 调用还原当前视图变换，从 arc/fillText 调用还原节点位置与标签出屏。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import { NConfigProvider, darkTheme } from 'naive-ui'
import { readFileSync } from 'node:fs'
import { resolve as resolvePath } from 'node:path'

import GraphView from '../graph.vue'
import { getKnowledgeGraph } from '@/api/knowledge'
import zh from '@/locales/zh'

vi.mock('@/api/knowledge', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/knowledge')>()
  return { ...actual, getKnowledgeGraph: vi.fn() }
})

const mockedGetGraph = vi.mocked(getKnowledgeGraph)

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

const VIEW_W = 800
const VIEW_H = 600

// ── 2d 上下文记录器 ─────────────────────────────────────────────────────────
interface CtxCall {
  method: string
  args: unknown[]
}

let calls: CtxCall[] = []

function makeCtxMock(): CanvasRenderingContext2D {
  return new Proxy({} as Record<string | symbol, unknown>, {
    get(_target, prop) {
      return (...args: unknown[]) => {
        calls.push({ method: String(prop), args })
      }
    },
    set(_target, prop, value) {
      calls.push({ method: `set:${String(prop)}`, args: [value] })
      return true
    },
  }) as unknown as CanvasRenderingContext2D
}

function resetCalls(): void {
  calls = []
}

/** 最后一次绘制的视图变换（draw 内先 translate 后 scale；initCanvas 的 scale(1,1) 更早） */
function lastTransform(): { offsetX: number; offsetY: number; scale: number } {
  const translates = calls.filter((c) => c.method === 'translate')
  const scales = calls.filter((c) => c.method === 'scale')
  const t = translates[translates.length - 1]
  const s = scales[scales.length - 1]
  expect(t, 'draw() 应调用 ctx.translate').toBeTruthy()
  expect(s, 'draw() 应调用 ctx.scale').toBeTruthy()
  return { offsetX: t.args[0] as number, offsetY: t.args[1] as number, scale: s.args[0] as number }
}

/** 最近一次绘制里节点圆弧的（世界）坐标与半径 */
function drawnNodes(): { x: number; y: number; r: number }[] {
  // draw 每个节点恰好一次 arc（未选中时无 glow 弧）
  return calls
    .filter((c) => c.method === 'arc')
    .map((c) => ({ x: c.args[0] as number, y: c.args[1] as number, r: c.args[2] as number }))
}

function drawnTexts(): string[] {
  return calls.filter((c) => c.method === 'fillText').map((c) => String(c.args[0]))
}

// ── 手动帧队列（jsdom 无真实渲染循环）─────────────────────────────────────
let rafQueue: FrameRequestCallback[] = []

function pumpFrame(): void {
  const cbs = rafQueue.splice(0)
  for (const cb of cbs) cb(0)
  // animate 自注册回调可能与其他组件的 rAF 回调同队；确保本轮至少绘制一次
  if (!calls.some((c) => c.method === 'clearRect')) {
    const more = rafQueue.splice(0)
    for (const cb of more) cb(0)
  }
}

// ── 挂载基座 ────────────────────────────────────────────────────────────────
// 两个节点由 Math.random 的 4 周期序列决定初始位置：无论消耗起点落在周期何处，
// 连续两对取值都不会重合（重合会让模拟力 0 距离产生 NaN），fitView 后 scale≈2.2。
const RANDOM_CYCLE = [0.2, 0.2, 0.8, 0.8]
let randomTick = 0

const API_DATA = {
  nodes: [
    { id: 'node-a', type: 'PERSON', description: '', occurrences: 1 },
    { id: 'node-b', type: 'ORGANIZATION', description: '', occurrences: 1 },
  ],
  edges: [{ source: 'node-a', target: 'node-b', relation: 'rel' }],
  analytics: {},
}

async function mountGraph(opts: { dark?: boolean } = {}) {
  mockedGetGraph.mockResolvedValue(API_DATA as never)
  const Host = defineComponent({
    render: () =>
      opts.dark
        ? h(NConfigProvider, { theme: darkTheme }, { default: () => h(GraphView) })
        : h(GraphView),
  })
  const wrapper = mount(Host, {
    global: {
      plugins: [i18n],
      // PageBreadcrumb 依赖 vue-router 上下文，本用例与其无关，桩掉
      stubs: { PageBreadcrumb: true },
    },
  })
  await flushPromises()
  await nextTick()
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  resetCalls()
  rafQueue = []
  randomTick = 0
  vi.spyOn(Math, 'random').mockImplementation(() => {
    const value = RANDOM_CYCLE[randomTick % RANDOM_CYCLE.length]
    randomTick += 1
    return value
  })
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(() => makeCtxMock())
  vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue({
    x: 0,
    y: 0,
    width: VIEW_W,
    height: VIEW_H,
    top: 0,
    left: 0,
    right: VIEW_W,
    bottom: VIEW_H,
    toJSON: () => ({}),
  } as DOMRect)
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
    rafQueue.push(cb)
    return rafQueue.length
  })
  vi.stubGlobal('cancelAnimationFrame', () => {})
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('E13 · fitView 自动适配', () => {
  it('数据加载后视图自动放大框住节点，全部节点落在画布可视区内', async () => {
    await mountGraph()
    const { offsetX, offsetY, scale } = lastTransform()

    // 旧实现 scale 恒为 1（无 fitView）→ 此断言先红
    expect(scale).toBeGreaterThan(1.1)

    // 每个节点连同半径一起应落在可视区内（自动适配的验收口径）
    const nodes = drawnNodes()
    expect(nodes).toHaveLength(2)
    for (const n of nodes) {
      const screenX = n.x * scale + offsetX
      const screenY = n.y * scale + offsetY
      expect(screenX - n.r * scale).toBeGreaterThanOrEqual(0)
      expect(screenX + n.r * scale).toBeLessThanOrEqual(VIEW_W)
      expect(screenY - n.r * scale).toBeGreaterThanOrEqual(0)
      expect(screenY + n.r * scale).toBeLessThanOrEqual(VIEW_H)
    }
  })

  it('复位按钮把视图还原到 fitView 变换（缩放后复位可回退）', async () => {
    const wrapper = await mountGraph()
    const initial = lastTransform()

    const zoomOut = wrapper.get('[data-testid="graph-zoom-out"]')
    await zoomOut.trigger('click')
    await zoomOut.trigger('click')
    resetCalls()
    pumpFrame()
    const zoomedOut = lastTransform()
    expect(zoomedOut.scale).toBeLessThan(initial.scale)

    await wrapper.get('[data-testid="graph-fit-view"]').trigger('click')
    resetCalls()
    pumpFrame()
    const reset = lastTransform()
    expect(reset.scale).toBeCloseTo(initial.scale, 0)
  })
})

describe('E13 · 缩放控件', () => {
  it('放大/缩小/复位三键齐全，点击放大增大、缩小减小', async () => {
    const wrapper = await mountGraph()
    // 旧实现无任何缩放控件 → 此断言先红
    expect(wrapper.get('[data-testid="graph-zoom-in"]').attributes('aria-label')).toBe('放大')
    expect(wrapper.get('[data-testid="graph-zoom-out"]').attributes('aria-label')).toBe('缩小')
    expect(wrapper.get('[data-testid="graph-fit-view"]').attributes('aria-label')).toBe('复位视图')

    const initial = lastTransform().scale
    const zoomOut = wrapper.get('[data-testid="graph-zoom-out"]')
    await zoomOut.trigger('click')
    await zoomOut.trigger('click')
    resetCalls()
    pumpFrame()
    const shrunk = lastTransform().scale
    expect(shrunk).toBeLessThan(initial)

    await wrapper.get('[data-testid="graph-zoom-in"]').trigger('click')
    resetCalls()
    pumpFrame()
    const grown = lastTransform().scale
    expect(grown).toBeGreaterThan(shrunk)
  })
})

describe('E13 · 标签防叠（缩放阈值隐藏）', () => {
  it('正常缩放级别标签在画；缩小到阈值下标签全部隐藏', async () => {
    const wrapper = await mountGraph()

    // fitView 后的级别（scale=3）≥ 阈值：节点与关系标签应在画
    expect(drawnTexts()).toContain('node-a')
    expect(drawnTexts()).toContain('rel')

    // 连点缩小穿过阈值（3 * (1/1.2)^9 ≈ 0.58 < 0.65）
    const zoomOut = wrapper.get('[data-testid="graph-zoom-out"]')
    for (let i = 0; i < 9; i += 1) await zoomOut.trigger('click')
    resetCalls()
    pumpFrame()
    const texts = drawnTexts()
    // 旧实现恒绘标签 → 此断言先红
    expect(texts).not.toContain('node-a')
    expect(texts).not.toContain('rel')
  })
})

describe('E13 · 暗色画布令牌化', () => {
  it('draw() 不含硬编码色字面量，颜色取自主题令牌', () => {
    const src = readFileSync(resolvePath(process.cwd(), 'src/views/knowledge/graph.vue'), 'utf-8')
    const start = src.indexOf('const draw = ')
    const end = src.indexOf('const animate = ')
    expect(start, 'graph.vue 应包含 draw 定义').toBeGreaterThan(-1)
    expect(end, 'draw 应位于 animate 之前').toBeGreaterThan(start)
    const drawBody = src.slice(start, end)

    // 旧实现含 rgba(156, 163, 175, ...) / #fff / #374151 → 先红
    expect(drawBody).not.toMatch(/#[0-9a-fA-F]{3,8}\b/)
    expect(drawBody).not.toMatch(/\brgba?\s*\(/)
    expect(drawBody).toMatch(/themeVars/)
  })

  it('暗色主题下节点标签使用主题令牌 textColor2（#374151 暗底不可读）', async () => {
    await mountGraph({ dark: true })
    const fills = calls
      .filter((c) => c.method === 'set:fillStyle')
      .map((c) => String(c.args[0]))
    const darkTextColor2 = (darkTheme.common as unknown as Record<string, string>).textColor2
    expect(darkTextColor2, 'naive darkTheme 应提供 textColor2').toBeTruthy()

    // 旧实现固定 #374151 → 此断言先红
    expect(fills).toContain(darkTextColor2)
    expect(fills).not.toContain('#374151')
  })
})
