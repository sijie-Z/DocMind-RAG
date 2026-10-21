/**
 * T8 路由级懒加载失败重试判别用例（R3 修复清单③ / 首载 30s 无 DOM 兜底）。
 *
 * 复核证据：:5173 实况冷直访 /monitoring → `Failed to fetch dynamically imported
 * module: .../views/monitoring/index.vue` + ERR_CONNECTION_REFUSED →
 * 整页白屏（_uisweep/shots/w2t8_01_monitor_cold_load.png），重试即好。
 * 修法：router.onError 捕获 chunk 加载失败 → 同一路径整页重载一次
 * （sessionStorage 防死循环；成功后清除标记，允许下次再兜底）。
 * 变异点：删 reload 调用 → 用例 1 红；删防循环 → 用例 2 红；删成功清除 → 用例 3 红。
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { isChunkLoadError, installChunkLoadRetry } from '@/utils/chunkLoadRetry'

/** 极简 fake router：捕获 onError / afterEach 回调 */
function fakeRouter() {
  const handlers: { error?: (e: unknown, to: { fullPath: string }) => void; after?: (to: { fullPath: string }) => void } = {}
  return {
    handlers,
    onError(fn: (e: unknown, to: { fullPath: string }) => void) { handlers.error = fn },
    afterEach(fn: (to: { fullPath: string }) => void) { handlers.after = fn },
  }
}

function fakeStorage() {
  const m = new Map<string, string>()
  return {
    getItem: (k: string) => (m.has(k) ? m.get(k)! : null),
    setItem: (k: string, v: string) => void m.set(k, v),
    removeItem: (k: string) => void m.delete(k),
    _map: m,
  }
}

describe('isChunkLoadError · 识别动态 import 失败', () => {
  it.each([
    'Failed to fetch dynamically imported module: http://localhost:5173/src/views/monitoring/index.vue',
    'Importing a module script failed.',
    'error loading dynamically imported module',
    'TypeError: Failed to fetch dynamically imported module: http://x/y.vue',
  ])('命中：%s', (msg) => {
    expect(isChunkLoadError(new Error(msg))).toBe(true)
  })

  it.each(['Network Error', 'Cannot read properties of undefined (reading x)', '404 Not Found'])(
    '不误伤：%s',
    (msg) => {
      expect(isChunkLoadError(new Error(msg))).toBe(false)
    }
  )
})

describe('installChunkLoadRetry · 整页重载一次 + 防死循环', () => {
  let reloadedPaths: string[]
  let reload: (p: string) => void
  let storage: ReturnType<typeof fakeStorage>
  let router: ReturnType<typeof fakeRouter>

  beforeEach(() => {
    reloadedPaths = []
    reload = (p: string) => { reloadedPaths.push(p) }
    storage = fakeStorage()
    router = fakeRouter()
    installChunkLoadRetry(router as never, { reload, storage })
  })

  it('chunk 失败 → 对目标路径重载一次', () => {
    router.handlers.error!(new Error('Failed to fetch dynamically imported module: /x.vue'), { fullPath: '/monitoring' })
    expect(reloadedPaths).toEqual(['/monitoring'])
  })

  it('同路径再次失败 → 不重复重载（防死循环）', () => {
    const err = new Error('Failed to fetch dynamically imported module: /x.vue')
    router.handlers.error!(err, { fullPath: '/monitoring' })
    router.handlers.error!(err, { fullPath: '/monitoring' })
    expect(reloadedPaths).toHaveLength(1)
  })

  it('重载成功后清除标记 → 之后同类失败仍可再兜底一次', () => {
    const err = new Error('Importing a module script failed.')
    router.handlers.error!(err, { fullPath: '/monitoring' })
    expect(reloadedPaths).toHaveLength(1)
    // 导航成功（afterEach）→ 清标记
    router.handlers.after!({ fullPath: '/monitoring' })
    // 下一次失败（新会话/新失败）→ 再兜底
    router.handlers.error!(err, { fullPath: '/monitoring' })
    expect(reloadedPaths).toHaveLength(2)
  })

  it('非 chunk 错误不触发重载', () => {
    router.handlers.error!(new Error('Network Error'), { fullPath: '/monitoring' })
    expect(reloadedPaths).toEqual([])
  })
})
