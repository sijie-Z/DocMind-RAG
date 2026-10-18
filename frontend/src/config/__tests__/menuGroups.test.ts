/**
 * B1：侧边栏收敛为六个场景域，且每个域必须渲染分组标题。
 *
 * 修前现状：SIDEBAR_MENU_GROUPS 有 3 个组、15 个菜单项，但所有组都没有
 * groupI18nKey —— 布局层只在 groupI18nKey 存在时渲染组标题（layouts/index.vue），
 * 因此侧栏实际是 15 项无分组平铺。本测试锁住：
 *  1. 恰好六个场景域，顺序固定（工作台/对话/知识库/自动化/提示词/管理）；
 *  2. 每个域都有 groupI18nKey，且四语（zh/en/ja/fr）存在、非空、不等于键名；
 *  3. 每个菜单项落在预期域；管理域 adminOnly，仅管理员可见；
 *  4. 菜单树快照（防止后续无意识增删项）。
 */
import { describe, it, expect } from 'vitest'
import { SIDEBAR_MENU_GROUPS } from '@/config/menu'
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

const expectedDomains = [
  {
    key: 'menu.groupWorkspace',
    routes: ['Dashboard'],
    adminOnly: false,
  },
  {
    key: 'menu.groupConversation',
    routes: ['Chat', 'Conversations'],
    adminOnly: false,
  },
  {
    key: 'menu.groupKnowledge',
    routes: ['Knowledge', 'Search'],
    adminOnly: false,
  },
  {
    key: 'menu.groupAutomation',
    routes: ['Workflow', 'Agent', 'ScheduledTasks'],
    adminOnly: false,
  },
  {
    key: 'menu.groupPrompts',
    routes: ['Prompts'],
    adminOnly: false,
  },
  {
    key: 'menu.groupManagement',
    routes: ['Users', 'Organizations', 'AuditLogs', 'Monitoring', 'McpServers'],
    adminOnly: true,
  },
]

describe('sidebar menu domains', () => {
  it('has exactly six scenario domains in the agreed order', () => {
    expect(SIDEBAR_MENU_GROUPS.map((g) => g.groupI18nKey)).toEqual(
      expectedDomains.map((d) => d.key),
    )
  })

  it('every domain carries an explicit group title key (no flat 15-item list)', () => {
    const missing = SIDEBAR_MENU_GROUPS.filter(
      (g) => typeof g.groupI18nKey !== 'string' || g.groupI18nKey.length === 0,
    ).map((g) => g.items.map((i) => i.routeName).join(','))
    expect(missing, `domains without groupI18nKey: ${missing.join(' | ')}`).toEqual([])
  })

  it('every domain title key resolves in all four locales', () => {
    const keys = SIDEBAR_MENU_GROUPS.map((g) => g.groupI18nKey as string)
    for (const [name, messages] of LOCALES) {
      const bad = keys.filter((key) => {
        const value = getByPath(messages, key)
        return typeof value !== 'string' || value.length === 0 || value === key
      })
      expect(bad, `${name} missing/invalid group titles: ${bad.join(', ')}`).toEqual([])
    }
  })

  it('routes live in their agreed domain, management is admin-only', () => {
    const actual = SIDEBAR_MENU_GROUPS.map((g) => ({
      key: g.groupI18nKey,
      routes: g.items.map((i) => i.routeName),
      adminOnly: g.adminOnly === true,
    }))
    expect(actual).toEqual(expectedDomains)
  })

  it('menu tree snapshot', () => {
    expect(SIDEBAR_MENU_GROUPS).toMatchSnapshot()
  })
})
