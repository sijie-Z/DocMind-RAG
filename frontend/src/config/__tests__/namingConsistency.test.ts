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
 *  4) 定位层（T9 定位三件套）：全部布局子路由的"生效高亮键"（meta.activeMenu
 *     ?? route.name）必须落在 menu.ts 键集内——菜单面上不存在的键 = 侧栏
 *     永不高亮（第一轮 P1-18 的根因）；六个隐藏页必须显式声明 meta.activeMenu，
 *     声明其菜单面归属；深页必须接入 <PageBreadcrumb />（页内定位/返回出口）。
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

/** 菜单键全集：侧边栏 + 尾部项 + 用户下拉（routeName → 菜单键） */
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
  // T9 十组页名归一：h1 全部改引菜单键（值以菜单语义为准，裁决依据见 T9 报告）
  Knowledge: '../../views/knowledge/index.vue',
  Prompts: '../../views/prompts/index.vue',
  Users: '../../views/users/index.vue',
  Organizations: '../../views/organizations/index.vue',
  Monitoring: '../../views/monitoring/index.vue',
  McpServers: '../../views/mcp-servers/index.vue',
  ScheduledTasks: '../../views/scheduled-tasks/index.vue',
  Notifications: '../../views/notifications/index.vue',
  Agent: '../../views/agent/index.vue',
  Workflow: '../../views/workflow/index.vue',
}

/** 无页名标题页面（主标题是问候语/品牌/动态内容，不承载页名；页名由菜单+顶栏承载） */
const NO_NAME_HEADING: Record<string, string> = {
  Dashboard: 'h1 是问候语（dashboard.welcome），页名由菜单/顶栏承载',
  Chat: 'h1 是动态会话标题（ChatHeader）',
  Profile: 'h2 是用户昵称，页名由菜单/顶栏承载',
  SystemAbout: 'h1 是品牌名 DocMind',
}

/** 待归一清单（T9 已清零：十组页名全部收口；新页面暂无法归一时登记于此） */
const PENDING_TITLE_UNIFICATION: Record<string, string> = {}

/** T5 本波新增键（同样吃四语存在性守护） */
const T5_EXTRA_KEYS = ['profile.overview']

/**
 * 隐藏页（菜单面没有直接项、经页内入口到达的深页）→ 生效高亮键（其菜单面归属）。
 * 高亮键一律取 menu.ts 的 routeName 空间：侧栏项（Knowledge/Workflow）或
 * 用户下拉项（SystemHelp/SystemAbout/Notifications——铃铛弹层/头像下拉是其菜单面）。
 */
const HIDDEN_PAGES: Record<string, string> = {
  KnowledgeGraph: 'Knowledge',
  WorkflowEditor: 'Workflow',
  AgentMemory: 'Workflow',
  Notifications: 'Notifications',
  SystemHelp: 'SystemHelp',
  SystemAbout: 'SystemAbout',
}

/** 深页面包屑注册表（T9）：这些页面的视图源码必须接入 <PageBreadcrumb /> */
const BREADCRUMB_PAGES: Record<string, string> = {
  KnowledgeGraph: '../../views/knowledge/graph.vue',
  WorkflowEditor: '../../views/workflow/editor.vue',
  AgentMemory: '../../views/workflow/memory.vue',
  Notifications: '../../views/notifications/index.vue',
  SystemHelp: '../../views/systemHelp/index.vue',
  SystemAbout: '../../views/systemAbout/index.vue',
}

/** 深页页头注册表（T9）：h1 必须引用路由 meta.title 键（顶栏/面包屑/h1 同源） */
const DEEP_PAGE_HEADINGS: Record<string, string> = {
  KnowledgeGraph: '../../views/knowledge/graph.vue',
  AgentMemory: '../../views/workflow/memory.vue',
}

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

describe('命名一致性 · 定位层（activeMenu 单一事实源）', () => {
  const layoutRoot = routes.find((r) => r.children && r.children.length > 0)
  const layoutChildren = layoutRoot?.children ?? []

  it('前置条件：布局子路由非空', () => {
    expect(layoutChildren.length).toBeGreaterThan(0)
  })

  it('每个布局子路由的生效高亮键（meta.activeMenu ?? name）落在菜单键集内', () => {
    const broken: string[] = []
    for (const r of layoutChildren) {
      const name = String(r.name)
      const effective = (r.meta?.activeMenu as string | undefined) ?? name
      if (!menuKeyOf.has(effective)) broken.push(`${name} → ${effective}`)
    }
    expect(
      broken,
      `生效高亮键不在 menu.ts 键集中（侧栏将永不高亮）: ${broken.join(', ')}`,
    ).toEqual([])
  })

  it('六个隐藏页显式声明 meta.activeMenu（其菜单面归属）', () => {
    const wrong: string[] = []
    for (const [name, expected] of Object.entries(HIDDEN_PAGES)) {
      const actual = routeByName.get(name)?.meta?.activeMenu
      if (actual !== expected) wrong.push(`${name}: activeMenu=${String(actual)} ≠ ${expected}`)
    }
    expect(wrong, `隐藏页 activeMenu 缺失或错误:\n${wrong.join('\n')}`).toEqual([])
  })
})

describe('命名一致性 · 面包屑层（深页接入定位三件套）', () => {
  for (const [routeName, file] of Object.entries(BREADCRUMB_PAGES)) {
    it(`${routeName}：${file} 接入 <PageBreadcrumb />`, async () => {
      const loader = viewRaw[file]
      expect(loader, `${file} 未在 import.meta.glob 结果中（被移动/删除？）`).toBeTruthy()
      const source = await loader()
      expect(source, `${file} 未接入 <PageBreadcrumb />（深页缺面包屑/返回出口）`).toContain(
        '<PageBreadcrumb',
      )
    })
  }

  for (const [routeName, file] of Object.entries(DEEP_PAGE_HEADINGS)) {
    it(`${routeName}：${file} 的 h1 引用路由 meta.title 键（页名与顶栏/面包屑同源）`, async () => {
      const expectedKey = routeByName.get(routeName)?.meta?.title as string | undefined
      expect(expectedKey, `${routeName} 无 meta.title`).toBeTruthy()
      const loader = viewRaw[file]
      expect(loader, `${file} 未在 import.meta.glob 结果中`).toBeTruthy()
      const source = await loader()
      const headingKey = extractHeadingKey(source)
      expect(
        headingKey,
        `${file} 主标题未找到 t('…') 键（标题被移除或硬编码回归）`,
      ).toBe(expectedKey)
    })
  }

  it('知识库页图谱入口带词条文字（不再是纯图标钮，P1-18）', async () => {
    const loader = viewRaw['../../views/knowledge/index.vue']
    expect(loader, 'knowledge/index.vue 未在 glob 中').toBeTruthy()
    const source = await loader()
    const idx = source.indexOf("'/knowledge/graph'")
    expect(idx, '知识库页应保留图谱入口（→ /knowledge/graph）').toBeGreaterThan(-1)
    const near = source.slice(Math.max(0, idx - 300), idx + 300)
    expect(near, '图谱入口未引用词条文字（仍是纯图标钮，用户找不到）').toContain(
      "t('router.knowledgeGraph')",
    )
  })
})
