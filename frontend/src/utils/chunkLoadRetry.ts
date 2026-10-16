/**
 * 路由级懒加载（动态 import）失败兜底 —— T8（R3 修复清单③）。
 *
 * 背景（15f681b 实况复核）：冷直访 /monitoring 时浏览器报
 * `Failed to fetch dynamically imported module: .../views/monitoring/index.vue`
 * （伴随 net::ERR_INSUFFICIENT_RESOURCES / ERR_CONNECTION_REFUSED），
 * 路由组件加载失败 → 整页白屏、"30 秒无 DOM"，重试/刷新即好。
 *
 * 兜底策略（业界常规做法）：捕获 chunk 加载失败 → 对目标路径整页重载一次；
 * sessionStorage 记录已重试路径防死循环；导航成功后清除标记（下次失败仍可兜底）。
 *
 * 依赖注入（reload / storage）便于测试，不在模块里直接摸 window 全局。
 */

interface RetryRouter {
  onError: (handler: (error: unknown, to: { fullPath: string }) => void) => void
  afterEach: (handler: (to: { fullPath: string }) => void) => void
}

interface RetryDeps {
  reload: (path: string) => void
  storage: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>
}

const KEY_PREFIX = 'chunk-reload:'

/** 动态 import（chunk）失败的典型错误文案（Vite dev / 生产构建 / 浏览器差异） */
const CHUNK_ERROR_PATTERNS = [
  /Failed to fetch dynamically imported module/i,
  /Importing a module script failed/i,
  /error loading dynamically imported module/i,
]

export function isChunkLoadError(error: unknown): boolean {
  const msg = String((error as { message?: unknown })?.message ?? error ?? '')
  return CHUNK_ERROR_PATTERNS.some((p) => p.test(msg))
}

export function installChunkLoadRetry(router: RetryRouter, deps?: Partial<RetryDeps>): void {
  const reload: RetryDeps['reload'] =
    deps?.reload ?? ((p: string) => { window.location.assign(p) })
  const storage: RetryDeps['storage'] = deps?.storage ?? window.sessionStorage

  router.onError((error, to) => {
    if (!isChunkLoadError(error) || !to?.fullPath) return
    const key = KEY_PREFIX + to.fullPath
    if (storage.getItem(key)) return // 已重试过：不再循环，让错误上抛
    storage.setItem(key, '1')
    reload(to.fullPath)
  })

  router.afterEach((to) => {
    // 导航成功（chunk 加载成功）→ 清标记，允许未来的偶发失败再次兜底
    if (to?.fullPath) storage.removeItem(KEY_PREFIX + to.fullPath)
  })
}
