import type { NavigationFailure, RouteLocationNormalized } from 'vue-router'
import { setPageTitleKey } from '@/locales'

type TitleRoute = Pick<RouteLocationNormalized, 'name' | 'params' | 'meta'>

/**
 * 路由级标题决策：
 * - 被取消/重复/中止的导航（failure 非空）不得覆盖已落地页面的标题；
 * - 同一页面仅 query 变化（如清除 ?chunk=）保留页面级标题；
 * - 其余导航按 meta.title 设置，切语言时由 locales 重新解析。
 */
export function applyRoutePageTitle(
  to: TitleRoute,
  from: TitleRoute,
  failure?: NavigationFailure | void
): void {
  if (failure) return
  const samePage =
    to.name === from.name && JSON.stringify(to.params) === JSON.stringify(from.params)
  if (samePage) return
  const title = to.meta?.title
  setPageTitleKey(typeof title === 'string' ? title : null)
}
