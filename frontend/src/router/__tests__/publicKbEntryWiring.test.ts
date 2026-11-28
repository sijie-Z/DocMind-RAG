/**
 * 公开入口守卫接线（批次十八 · 复审 M1）。
 *
 * 现有 helper 测试只验证纯函数；本用例用真实 DOM meta 启动真实 router，
 * 钉住 `router/index.ts` 的接线本身：nginx 注入
 * `<meta name="docmind-public-kb-entry" content="1">` 后，公开文档内
 * push Login 必须被 `next(false)` 拦下。
 *
 * 变异点：把 `startedOnPublicKbEntry` 写死 false、或删除 beforeEach 中的
 * 守卫 if 块 → 本用例红。
 *
 * 稳健性：只做一次被守卫拦下的导航（不加载任何懒加载页面组件），避免并行
 * 运行时被大模块编译拖到超时；显式 60s 超时给全量并行留余量。普通文档下
 * Login 可达由既有 router 测试覆盖，这里不重复加载 Login 页面。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

beforeEach(async () => {
  window.history.replaceState({}, '', '/')
  document.head.innerHTML = ''
  localStorage.clear()
  vi.resetModules()
  // 必须在 resetModules 之后导入 pinia：router 的 store 与本文件必须
  // 共用同一个 pinia 模块实例，否则守卫里 useUserStore() 找不到 active pinia。
  const { createPinia, setActivePinia } = await import('pinia')
  setActivePinia(createPinia())
})

afterEach(() => {
  document.head.innerHTML = ''
  vi.restoreAllMocks()
})

describe('public KB entry router wiring (review M1)', () => {
  it('真实 meta 标记 → 公开入口文档内阻止跳转 Login', async () => {
    document.head.innerHTML =
      '<meta name="docmind-public-kb-entry" content="1">'
    const { default: router } = await import('../index')

    await router.push({ name: 'Login' })
    // 守卫 next(false)：导航未落地，当前仍停留在初始路由而非 Login。
    expect(router.currentRoute.value.name).not.toBe('Login')
  }, 60_000)
})
