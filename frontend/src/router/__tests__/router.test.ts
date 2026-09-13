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
      const resolved = router.resolve({ name: r.name as string })
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
