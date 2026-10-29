/**
 * 路由冒烟测试
 *
 * 背景：vue-router 从 4 升到 5 时，全仓库有 14 个文件使用路由 API，
 * 但此前**没有任何测试覆盖路由** —— 构建通过不等于路由行为正确。
 * 本测试补上最基本的两层保证：
 *   1. 路由表完整（具名路由可解析、懒加载写法未被改回静态 import）
 *   2. **导航守卫的真实重定向行为**（未登录拦截）
 *
 * 只做冒烟，不覆盖每条路由的渲染 —— 那需要挂载整个应用。
 *
 * 路由结构备注：鉴权标记在父路由（path: '/'，无 name）的 meta 上，
 * 子路由通过 to.matched 继承，因此不能用 `route.meta.requiresAuth` 逐个筛。
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createRouter, createMemoryHistory, type RouteRecordRaw } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'

// 守卫依赖的鉴权工具。默认「无 token」。
vi.mock('@/utils/auth', () => ({
  getToken: vi.fn(() => undefined),
  setToken: vi.fn(),
  removeToken: vi.fn(),
  isTokenExpired: vi.fn(() => false),
  getRefreshToken: vi.fn(() => undefined),
  setRefreshToken: vi.fn(),
}))

import { routes } from '../index'
import * as auth from '@/utils/auth'

const flatten = (rs: readonly RouteRecordRaw[]): RouteRecordRaw[] =>
  rs.flatMap((r) => [r, ...(r.children ? flatten(r.children as RouteRecordRaw[]) : [])])

describe('路由表', () => {
  const all = flatten(routes)
  const named = all.filter((r) => r.name)

  it('具名路由数量符合预期（防止路由被误删）', () => {
    expect(named.length).toBeGreaterThanOrEqual(20)
  })

  it('每条具名路由都能解析出匹配项', () => {
    const router = createRouter({ history: createMemoryHistory(), routes })
    for (const r of named) {
      // 带参路由（如 reviews/:id）解析时必须补占位参数。
      const params = Object.fromEntries(
        (r.path.match(/:([A-Za-z0-9_]+)/g) ?? []).map((token) => [token.slice(1), 'probe'])
      )
      const resolved = router.resolve({ name: r.name as string, params })
      expect(resolved.matched.length, `路由 ${String(r.name)} 无法解析`).toBeGreaterThan(0)
    }
  })

  it('懒加载写法未被改回静态 import（绝大多数路由是 () => import）', () => {
    const lazy = all.filter((r) => typeof r.component === 'function')
    expect(lazy.length).toBeGreaterThan(15)
  })

  it('存在受保护的路由层级（meta.requiresAuth 在父路由上）', () => {
    const protectedParents = all.filter((r) => r.meta?.requiresAuth)
    expect(protectedParents.length, '没有任何 requiresAuth 标记').toBeGreaterThan(0)
  })
})

describe('导航守卫（真实行为）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('未登录访问受保护路由 → 被重定向到 Login', async () => {
    vi.mocked(auth.getToken).mockReturnValue(undefined)
    const router = (await import('../index')).default

    await router.push({ name: 'Dashboard' })

    expect(router.currentRoute.value.name).toBe('Login')
  })

  it('未登录时重定向带上原本要去的地址（redirect 查询参数）', async () => {
    vi.mocked(auth.getToken).mockReturnValue(undefined)
    const mod = await import('../index')

    await mod.default.push({ name: 'Knowledge' })

    const cur = mod.default.currentRoute.value
    expect(cur.name).toBe('Login')
  })

  it('未标记 requiresAuth 的路由（如 FirstHome）不触发拦截', async () => {
    vi.mocked(auth.getToken).mockReturnValue(undefined)
    const mod = await import('../index')

    await mod.default.push({ name: 'FirstHome' })

    expect(mod.default.currentRoute.value.name).toBe('FirstHome')
  })
})

describe('真实 404（未知路径不再静默弹回营销页）', () => {
  it('未知路径命中具名 NotFound 路由，且停留在原路径上', async () => {
    const router = createRouter({ history: createMemoryHistory(), routes })
    await router.push('/this-page-is-not-registered-xyz')

    expect(router.currentRoute.value.name).toBe('NotFound')
    expect(router.currentRoute.value.path).toBe('/this-page-is-not-registered-xyz')
    // 修复前 catch-all 是 redirect '/firsthome'，命中重定向时 redirectedFrom 会非空
    expect(router.currentRoute.value.redirectedFrom).toBeUndefined()
  })

  it('NotFound 为懒加载组件、不要求登录态', () => {
    const record = flatten(routes).find((r) => r.name === 'NotFound')
    expect(record, '缺少具名 NotFound 路由').toBeTruthy()
    expect(typeof record?.component, 'NotFound 应为懒加载组件').toBe('function')
    expect(record?.meta?.requiresAuth).toBe(false)
  })

  it('catch-all 记录本身不再是 redirect（组件渲染真 404 页）', () => {
    const record = flatten(routes).find((r) => r.path === '/:pathMatch(.*)*')
    expect(record?.name).toBe('NotFound')
    expect(record?.redirect).toBeUndefined()
  })
})

describe('历史写法兼容重定向（放 catch-all 之前，进真路由而非 404/营销页）', () => {
  const cases: Array<[string, string, string]> = [
    ['/systemHelp', '/system-help', 'SystemHelp'],
    ['/help', '/system-help', 'SystemHelp'],
    ['/systemAbout', '/system-about', 'SystemAbout'],
    ['/about', '/system-about', 'SystemAbout'],
  ]

  for (const [from, to, expectedName] of cases) {
    it(`${from} → ${to}`, async () => {
      const router = createRouter({ history: createMemoryHistory(), routes })
      await router.push(from)

      expect(router.currentRoute.value.path).toBe(to)
      expect(router.currentRoute.value.name).toBe(expectedName)
    })
  }

  it('重定向保留查询串与哈希', async () => {
    const router = createRouter({ history: createMemoryHistory(), routes })
    await router.push('/help?topic=faq#install')

    const cur = router.currentRoute.value
    expect(cur.path).toBe('/system-help')
    expect(cur.query.topic).toBe('faq')
    expect(cur.hash).toBe('#install')
  })
})
