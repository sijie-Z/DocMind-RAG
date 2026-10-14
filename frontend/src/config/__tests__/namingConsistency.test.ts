/**
 * 命名一致性扫描器（T5 · 命名归一的长效抓手，CI 护栏）。
 *
 * 定则：同一页面的 菜单 / 顶栏 / 面包屑 / h1 四处引用同一个 i18n 键
 * （值以菜单语义为准；面包屑由 T9「定位三件套」落地后纳入本守护）。
 *
 * **锁"一致"，不锁"具体文案"**：全部断言只比较键与键的引用关系
 * （route.meta.title === 菜单键 === 页内主标题的键），不出现任何具体译文——
 * 后续整批改名（如 首页→工作台）只要四处同改即保持绿；只有"同页异键"的
 * 漂移才会红。
 *
 * 三层：
 *  1) 结构层（全菜单）：菜单项 → 路由可解析；route.meta.title === 菜单键
 *     （顶栏 currentRouteTitle 渲染的正是 meta.title）；菜单键/组标题/T5 新增键
 *     四语（zh/en/ja/fr）存在且非空。
 *  2) 页头层（已归一注册表）：注册页面视图源码的主标题（首个 h1/h2 内的
 *     t('…')）必须与菜单同键；提取不到 t() 键（被移除或硬编码回归）亦判红。
 *  3) 分类完备层：所有带 menu.* meta.title 的布局子路由必须在
 *     已归一 / 无页名标题 / 待归一（随 T9）三张清单之一，不许漏网；清单亦不得
 *     滞留已不存在的路由（防过期条目）。
 */
import { describe, it, expect } from 'vitest'
import type { RouteRecordRaw } from 'vue-router'
import { SIDEBAR_MENU_GROUPS, USER_MENU_ITEMS } from '@/config/menu'
import { routes } from '@/router'
import zh from '@/locales/zh'
import en from '@/locales/en'
import ja from '@/locales/ja'
import fr from '@/locales/fr'

/**
 * 视图源码（懒加载 raw）：走 Vite 原生 import.meta.glob，
 * 不依赖 cwd / import.meta.url 的环境差异（vitest 下后者非 file 协议）。
 */
const viewRaw = import.meta.glob('../../views/**/*.vue', {
  query: '?raw',
  import: 'default',
}) as Record<string, () => Promise<string>>

type Locale = Record<string, unknown>

const LOCALES: Array<[string, Locale]> = [
  ['zh', zh as Locale],
  ['en', en as Locale],
  ['ja', ja as Locale],
  ['fr', fr as Locale],
]

const getByPath = (obj: Locale, path: string): unknown =>
  path
    .split('.')
    .reduce<unknown>(
      (o, k) => (o && typeof o === 'object' ? (o as Locale)[k] : undefined),
      obj,
    )

const flatten = (rs: readonly RouteRecordRaw[]): RouteRecordRaw[] =>
  rs.flatMap((r) => [r, ...(r.children ? flatten(r.children as RouteRecordRaw[]) : [])])

const allRoutes = flatten(routes)
const routeByName = new Map<string, RouteRecordRaw>()
for (const r of allRoutes) if (r.name) routeByName.set(String(r.name), r)

/** 菜单键全集：侧边栏 + 用户下拉（routeName → 菜单键） */
const menuKeyOf = new Map<string, string>()
for (const group of SIDEBAR_MENU_GROUPS) {
  for (const item of group.items) menuKeyOf.set(item.routeName, item.i18nKey)
}
for (const item of USER_MENU_ITEMS) {
  if (item.routeName) menuKeyOf.set(item.routeName, item.i18nKey)
}

/** 已归一注册表：routeName → 视图文件（glob 键，相对本文件），主标题必须与菜单同键 */
const NORMALIZED_PAGES: Record<string, string> = {
  Search: '../../views/search.vue',
  Conversations: '../../views/conversations/index.vue',
  AuditLogs: '../../views/admin/audit/index.vue',
  SystemHelp: '../../views/systemHelp/index.vue',
}

/** 无页名标题页面（主标题是问候语/品牌/动态内容，不承载页名；页名由菜单+顶栏承载） */
const NO_NAME_HEADING: Record<string, string> = {
  Dashboard: 'h1 是问候语（dashboard.welcome），页名由菜单/顶栏承载',
  Chat: 'h1 是动态会话标题（ChatHeader）',
  Profile: 'h2 是用户昵称，页名由菜单/顶栏承载',
  SystemAbout: 'h1 是品牌名 DocMind',
}

/** 待归一清单（随 T9「定位三件套」一次性收口）：主标题仍用页域键或硬编码，与菜单键不同源 */
const PENDING_TITLE_UNIFICATION: Record<string, string> = {
  Knowledge: 'h1 用 knowledge.title，与 menu.knowledge 文案亦不同',
  Prompts: 'h1 用 prompts.title（同文本异键）',
  Users: 'h1 用 users.title（同文本异键）',
  Organizations: 'h1 用 org.title（同文本异键）',
  Monitoring: 'h1 用 monitoring.title，与 menu.monitoring 文案亦不同',
  Notifications: 'h1 用 notifications.title，与 menu.notifications 文案亦不同；通知组术语随 T9',
  Agent: 'h1 硬编码；Agent 运行组命名随 T9',
  Workflow: 'h1 硬编码；随 T9',
}

/** T5 本波新增键（同样吃四语存在性守护） */
const T5_EXTRA_KEYS = ['profile.overview']

/** 从视图源码提取页面主标题（首个 h1/h2）里的第一个 t('…') 键 */
function extractHeadingKey(source: string): string | null {
  const tag = source.match(/<h[12]\b[^>]*>/)
  if (!tag || tag.index === undefined) return null
  const after = source.slice(tag.index, tag.index + 400)
  const m = after.match(/t\(\s*['"]([\w.]+)['"]\s*\)/)
  return m ? m[1] : null
}

describe('命名一致性 · 结构层（菜单 ↔ 路由 ↔ 四语）', () => {
  it('每个菜单项的 routeName 都能解析出路由', () => {
    const missing: string[] = []
    for (const [routeName, i18nKey] of menuKeyOf) {
      if (!routeByName.has(routeName)) missing.push(`${routeName}（${i18nKey}）`)
    }
    expect(missing, `菜单项路由不存在: ${missing.join(', ')}`).toEqual([])
  })

  it('每个菜单项的 route.meta.title 与菜单键一致（顶栏与菜单同键）', () => {
    const mismatched: string[] = []
    for (const [routeName, i18nKey] of menuKeyOf) {
      const metaTitle = routeByName.get(routeName)?.meta?.title
      if (metaTitle !== i18nKey) {
        mismatched.push(`${routeName}: meta.title=${String(metaTitle)} ≠ 菜单键=${i18nKey}`)
      }
    }
    expect(mismatched, mismatched.join('\n')).toEqual([])
  })

  it('菜单键 / 组标题 / T5 新增键四语存在且非空', () => {
    const keys = [
      ...menuKeyOf.values(),
      ...SIDEBAR_MENU_GROUPS.map((g) => g.groupI18nKey).filter((k): k is string => typeof k === 'string'),
      ...T5_EXTRA_KEYS,
    ]
    for (const [name, messages] of LOCALES) {
      const missing: string[] = []
      for (const key of keys) {
        const v = getByPath(messages, key)
        if (typeof v !== 'string' || v.length === 0 || v === key) missing.push(key)
      }
      expect(missing, `${name} 缺失或非法的键: ${missing.join(', ')}`).toEqual([])
    }
  })
})

describe('命名一致性 · 页头层（已归一页面主标题与菜单同键）', () => {
  for (const [routeName, file] of Object.entries(NORMALIZED_PAGES)) {
    it(`${routeName}：${file} 主标题引用菜单键`, async () => {
      const expectedKey = menuKeyOf.get(routeName)
      expect(expectedKey, `${routeName} 不在菜单配置中`).toBeTruthy()
      const loader = viewRaw[file]
      expect(loader, `${file} 未在 import.meta.glob 结果中（文件被移动/删除？）`).toBeTruthy()
      const source = await loader()
      const headingKey = extractHeadingKey(source)
      expect(
        headingKey,
        `${file} 主标题未找到 t('…') 键（标题被移除或硬编码回归）`,
      ).toBeTruthy()
      expect(headingKey, `${file} 主标题键与菜单键不一致（命名漂移）`).toBe(expectedKey)
    })
  }
})

describe('命名一致性 · 分类完备层（menu.* 路由不许漏网）', () => {
  const layoutRoot = routes.find((r) => r.children && r.children.length > 0)
  const menuRoutes = (layoutRoot?.children ?? []).filter(
    (r) => typeof r.meta?.title === 'string' && (r.meta.title as string).startsWith('menu.'),
  )

  it('存在 menu.* 布局子路由（前置条件）', () => {
    expect(menuRoutes.length).toBeGreaterThan(0)
  })

  it('每个 menu.* 路由都被归类：已归一 / 无页名标题 / 待归一', () => {
    const unclassified: string[] = []
    for (const r of menuRoutes) {
      const name = String(r.name)
      if (
        !(name in NORMALIZED_PAGES) &&
        !(name in NO_NAME_HEADING) &&
        !(name in PENDING_TITLE_UNIFICATION)
      ) {
        unclassified.push(`${name}（meta.title=${String(r.meta?.title)}）`)
      }
    }
    expect(
      unclassified,
      `未归类（新页面？请登记到注册表/清单）: ${unclassified.join(', ')}`,
    ).toEqual([])
  })

  it('三张清单无过期条目（routeName 仍存在于 menu.* 路由中）', () => {
    const menuRouteNames = new Set(menuRoutes.map((r) => String(r.name)))
    const stale: string[] = []
    for (const name of [
      ...Object.keys(NORMALIZED_PAGES),
      ...Object.keys(NO_NAME_HEADING),
      ...Object.keys(PENDING_TITLE_UNIFICATION),
    ]) {
      if (!menuRouteNames.has(name)) stale.push(name)
    }
    expect(stale, `清单中存在已不存在/非 menu.* 的路由: ${stale.join(', ')}`).toEqual([])
  })
})
