/**
 * 导航定位辅助（T9 定位三件套·单一事实源）。
 *
 * - 菜单键集与键映射（routeName → i18nKey）一律来自 config/menu.ts；
 * - 生效高亮键 = route.meta.activeMenu ?? route.name（隐藏页归属其菜单面）；
 * - 面包屑链 = DocMind(→Dashboard) / 归属菜单项(menu.ts 键) / 当前页(route.meta.title)。
 *
 * 本模块为纯函数（无组件/翻译依赖），供 PageBreadcrumb 与扫描器共同消费；
 * 不变量由 namingConsistency / navigation.test.ts 守护。
 */
import { SIDEBAR_MENU_GROUPS, USER_MENU_ITEMS } from './menu'

/**
 * 菜单键集（routeName 空间，naive-ui 菜单 key = route.name 契约）：
 * 侧边栏（含尾部项）+ 用户下拉；纯动作项（logout）无 routeName，不入集。
 */
export function menuRouteNames(): Set<string> {
  const names = new Set<string>()
  for (const group of SIDEBAR_MENU_GROUPS) {
    for (const item of group.items) names.add(item.routeName)
  }
  for (const item of USER_MENU_ITEMS) {
    if (item.routeName) names.add(item.routeName)
  }
  return names
}

/** routeName → 菜单显示名 i18n 键（menu.ts 单一事实源；非菜单路由返回 undefined） */
export function menuI18nKeyOf(routeName: string): string | undefined {
  for (const group of SIDEBAR_MENU_GROUPS) {
    const hit = group.items.find((item) => item.routeName === routeName)
    if (hit) return hit.i18nKey
  }
  return USER_MENU_ITEMS.find((item) => item.routeName === routeName)?.i18nKey
}

/** 生效高亮键：meta.activeMenu 优先（非空字符串），缺省回退 route.name */
export function effectiveActiveMenu(
  routeName: string | null | undefined,
  activeMenu?: unknown,
): string {
  if (typeof activeMenu === 'string' && activeMenu.length > 0) return activeMenu
  return routeName ?? ''
}

/** 面包屑链项 */
export interface BreadcrumbItem {
  /** 显示名 i18n 键；品牌项（DocMind）为 null */
  i18nKey: string | null
  /** 点击目标路由 name；当前页（不可点）为 null */
  routeName: string | null
}

/**
 * 面包屑链：DocMind(→Dashboard) / [归属菜单项(→其路由)] / 当前页(meta.title)。
 * - 归属项：仅当生效高亮键 ≠ 当前 route.name 时插入（菜单页自身不重复一层）；
 *   显示名经 menu.ts 映射，映射缺失时不插入（防断链）。
 * - 当前页：route.meta.title；无标题时退化为纯品牌链。
 */
export function breadcrumbChainFor(
  routeName: string | null | undefined,
  meta: { activeMenu?: unknown; title?: unknown } | undefined,
): BreadcrumbItem[] {
  const chain: BreadcrumbItem[] = [{ i18nKey: null, routeName: 'Dashboard' }]
  const effective = effectiveActiveMenu(routeName, meta?.activeMenu)
  if (effective && effective !== routeName) {
    const key = menuI18nKeyOf(effective)
    if (key) chain.push({ i18nKey: key, routeName: effective })
  }
  const title = typeof meta?.title === 'string' && meta.title.length > 0 ? meta.title : null
  if (title) chain.push({ i18nKey: title, routeName: null })
  return chain
}
