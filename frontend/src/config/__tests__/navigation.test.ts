/**
 * T9 定位三件套 · 导航解析纯函数用例（高亮 / 面包屑单一事实源）。
 *
 * 定则：面包屑链 = DocMind(→Dashboard) / 归属菜单项(menu.ts 键) / 当前页(meta.title)；
 * 归属与菜单键一律经 config/menu.ts 映射，当前页名取路由 meta.title。
 * 本用例只锁"键与键的引用关系"，不出现任何具体译文（整体改名不碎、单页漂移即红）。
 */
import { describe, it, expect } from 'vitest'
import type { RouteRecordRaw } from 'vue-router'
import {
  breadcrumbChainFor,
  effectiveActiveMenu,
  menuI18nKeyOf,
  menuRouteNames,
} from '@/config/navigation'
import { routes } from '@/router'
import zh from '@/locales/zh'
import en from '@/locales/en'
import ja from '@/locales/ja'
import fr from '@/locales/fr'

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

describe('导航解析 · effectiveActiveMenu（高亮单一事实源）', () => {
  it('显式 activeMenu 优先；缺省/空值回退 route.name', () => {
    expect(effectiveActiveMenu('KnowledgeGraph', 'Knowledge')).toBe('Knowledge')
    expect(effectiveActiveMenu('Knowledge', undefined)).toBe('Knowledge')
    expect(effectiveActiveMenu('Chat', '')).toBe('Chat')
    expect(effectiveActiveMenu(undefined, undefined)).toBe('')
  })
})

describe('导航解析 · 面包屑链（DocMind / 归属菜单 / 当前页）', () => {
  const cases: Array<
    [string, { activeMenu?: string; title?: string }, Array<[string | null, string | null]>]
  > = [
    [
      'KnowledgeGraph',
      { activeMenu: 'Knowledge', title: 'router.knowledgeGraph' },
      [
        [null, 'Dashboard'],
        ['menu.knowledge', 'Knowledge'],
        ['router.knowledgeGraph', null],
      ],
    ],
    [
      'WorkflowEditor',
      { activeMenu: 'Workflow', title: 'router.workflowEditor' },
      [
        [null, 'Dashboard'],
        ['menu.workflow', 'Workflow'],
        ['router.workflowEditor', null],
      ],
    ],
    [
      'AgentMemory',
      { activeMenu: 'Workflow', title: 'router.agentMemory' },
      [
        [null, 'Dashboard'],
        ['menu.workflow', 'Workflow'],
        ['router.agentMemory', null],
      ],
    ],
    [
      'Notifications',
      { activeMenu: 'Notifications', title: 'menu.notifications' },
      [
        [null, 'Dashboard'],
        ['menu.notifications', null],
      ],
    ],
    [
      'SystemHelp',
      { activeMenu: 'SystemHelp', title: 'menu.systemHelp' },
      [
        [null, 'Dashboard'],
        ['menu.systemHelp', null],
      ],
    ],
    [
      'SystemAbout',
      { activeMenu: 'SystemAbout', title: 'menu.systemAbout' },
      [
        [null, 'Dashboard'],
        ['menu.systemAbout', null],
      ],
    ],
  ]

  for (const [routeName, meta, expected] of cases) {
    it(`${routeName}：品牌 / 归属 / 当前 三项键面`, () => {
      const chain = breadcrumbChainFor(routeName, meta)
      expect(chain.map((item) => [item.i18nKey, item.routeName])).toEqual(expected)
    })
  }

  it('链内键不重复（归属=自身时不再插一层）', () => {
    const chain = breadcrumbChainFor('SystemHelp', {
      activeMenu: 'SystemHelp',
      title: 'menu.systemHelp',
    })
    const keys = chain.map((item) => item.i18nKey).filter((k): k is string => !!k)
    expect(new Set(keys).size).toBe(keys.length)
  })
})

describe('导航解析 · 全路由交叉检查（路由 × 菜单 × 四语）', () => {
  const allRoutes = flatten(routes)
  const routeByName = new Map<string, RouteRecordRaw>()
  for (const r of allRoutes) if (r.name) routeByName.set(String(r.name), r)
  const layoutChildren = routes.find((r) => r.children && r.children.length > 0)?.children ?? []

  it('每个布局路由的面包屑链：文案键四语可解析、点击目标路由可解析', () => {
    for (const r of layoutChildren) {
      const chain = breadcrumbChainFor(String(r.name), r.meta)
      expect(chain.length, `${String(r.name)} 面包屑链为空`).toBeGreaterThan(0)
      for (const item of chain) {
        if (item.i18nKey) {
          for (const [lang, messages] of LOCALES) {
            const v = getByPath(messages, item.i18nKey)
            expect(
              typeof v === 'string' && v.length > 0 && v !== item.i18nKey,
              `${lang} 缺面包屑键 ${item.i18nKey}（${String(r.name)}）`,
            ).toBe(true)
          }
        }
        if (item.routeName) {
          expect(
            routeByName.has(item.routeName),
            `${String(r.name)} 面包屑指向未知路由 ${item.routeName}`,
          ).toBe(true)
        }
      }
    }
  })

  it('每个布局路由的生效高亮键落在 menu.ts 键集内', () => {
    const set = menuRouteNames()
    for (const r of layoutChildren) {
      const effective = effectiveActiveMenu(String(r.name), r.meta?.activeMenu)
      expect(set.has(effective), `${String(r.name)} → ${effective} 不在菜单键集`).toBe(true)
    }
  })

  it('menu.ts 键集内每个键都能映射到菜单 i18n 键', () => {
    for (const name of menuRouteNames()) {
      expect(menuI18nKeyOf(name), `${name} 无菜单 i18n 键映射`).toBeTruthy()
    }
  })

  it('common.back（面包屑返回出口文案）四语存在且非空', () => {
    for (const [lang, messages] of LOCALES) {
      const v = getByPath(messages, 'common.back')
      expect(
        typeof v === 'string' && (v as string).length > 0 && v !== 'common.back',
        `${lang} 缺 common.back`,
      ).toBe(true)
    }
  })
})
