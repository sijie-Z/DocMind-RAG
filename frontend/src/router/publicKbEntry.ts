/**
 * 公开知识库入口文档的导航边界（批次十八）。
 *
 * nginx 只为 `/public/kb/` 文档下发 CSP `frame-ancestors *` 且不发
 * X-Frame-Options；CSP 是文档级响应头，SPA 内的客户端路由跳转不会重新协商。
 * 若该文档被第三方 iframe 加载后还能跳转到管理/登录界面，这些界面就会
 * 变成可被第三方框住的页面，违背「管理/登录不得放开」的边界。
 *
 * 因此在公开知识库入口文档里禁止导航到：
 *  - 任何 requiresAuth / requiresAdmin / requiresPlatformAdmin 路由；
 *  - 登录、注册、营销首页（匿名路由，但界面不属于公开 Knowledge Base）。
 *
 * 入口判定有两道证据：
 *  1. nginx 在公开 location 注入的 `<meta name="docmind-public-kb-entry">`
 *     标记（服务器证据，不受 URL 编码/重复斜杠差异影响）；
 *  2. 前端兜底：按 nginx location 匹配口径规范化 pathname 后再比对前缀
 *     （覆盖本地 vite dev 等没有 nginx 注入标记的场景）。
 * 公开页自身的检索/聊天不触发路由跳转，正常使用不受影响。
 */

export const PUBLIC_KB_ENTRY_PREFIX = '/public/kb/'
export const PUBLIC_KB_ENTRY_META_NAME = 'docmind-public-kb-entry'

export interface PublicKbEntryGuardFlags {
  requiresAuth: boolean
  requiresAdmin: boolean
  requiresPlatformAdmin: boolean
}

/**
 * 按 nginx location 匹配口径规范化路径：
 *  - 百分号解码（nginx 对 URI 解码后再匹配 location）；
 *  - 合并连续斜杠（merge_slashes 默认 on）；
 *  - 解析 `.` / `..`（nginx 在解码后解析点段）。
 * 不能直接用 `location.pathname.startsWith('/public/kb/')`：`//public/kb/x`、
 * `/%70ublic/kb/x` 等写法同样会被 nginx 的公开 location 命中。
 */
export function normalizePathForNginxLocation(pathname: string): string {
  let decoded = pathname
  try {
    decoded = decodeURIComponent(pathname)
  } catch {
    // 非法百分号编码：nginx 会返回 400，不进入任何 SPA location。
  }
  const merged = decoded.replace(/\/{2,}/g, '/')
  const segments: string[] = []
  let trailingSlash = merged.endsWith('/')
  for (const segment of merged.split('/')) {
    if (!segment) continue
    if (segment === '.') {
      trailingSlash = true
      continue
    }
    if (segment === '..') {
      segments.pop()
      trailingSlash = true
      continue
    }
    segments.push(segment)
    trailingSlash = false
  }
  if (segments.length === 0) return '/'
  return `/${segments.join('/')}${trailingSlash ? '/' : ''}`
}

/** 是否为 nginx `location /public/kb/` 命中的路径（无尾斜杠的 /public/kb 不算）。 */
export function isPublicKbEntryPath(pathname: string): boolean {
  return normalizePathForNginxLocation(pathname).startsWith(PUBLIC_KB_ENTRY_PREFIX)
}

/** 入口文档判定：nginx 注入标记优先；无标记时（如 vite dev）退回规范化路径。 */
export function isPublicKbEntryDocument(hasServerMarker: boolean, pathname: string): boolean {
  return hasServerMarker || isPublicKbEntryPath(pathname)
}

export function isBlockedPublicKbEntryTarget(
  name: unknown,
  flags: PublicKbEntryGuardFlags,
): boolean {
  if (flags.requiresAuth || flags.requiresAdmin || flags.requiresPlatformAdmin) return true
  return name === 'Login' || name === 'Register' || name === 'FirstHome'
}
