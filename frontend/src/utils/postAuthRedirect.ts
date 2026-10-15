/**
 * 认证后落点解析（登录 / 注册共用）—— ISS-1。
 *
 * 背景：
 * - 注册成功曾硬跳 '/'（落到营销页）：token 虽已发放，但用户被丢回无登录态界面；
 * - 登录侧 redirect 校验只查了「以 / 开头、非 // 开头、非 javascript:」，
 *   /\evil.com（浏览器会归一化为协议相对 //evil.com）、/%09、编码反斜杠等
 *   变体均可绕过，构成开放重定向风险。
 *
 * 规则：
 * 1. 只放行站内路径：必须字面以单个 '/' 开头（// 与 /\ 均为协议相对变体，拒）；
 * 2. 任意位置的反斜杠、控制字符一律拒；
 * 3. 逐层解码（最多 3 层）后再校验，拦截 %09 / %5c / %2f%2f 及多重编码变体；
 * 4. 其余情况（缺省、非法类型、空串、非 / 开头）回落默认落点 /dashboard。
 */

/** 认证后默认落点：未携带合法 redirect 时去工作台（而非营销页 '/'） */
export const DEFAULT_POST_AUTH_TARGET = '/dashboard'

/** 是否存在控制字符（TAB / LF / CR / NUL / DEL 等） */
function hasControlChars(value: string): boolean {
  for (const ch of value) {
    const code = ch.codePointAt(0) ?? 0
    if (code < 0x20 || code === 0x7f) return true
  }
  return false
}

/**
 * 站内路径安全校验。
 * 注意不做 trim —— 规范要求「非 / 开头全拒」，带前导空白的值不算 / 开头。
 */
function isSafeInternalPath(path: string): boolean {
  if (!path.startsWith('/')) return false
  if (path.startsWith('//')) return false
  if (path.includes('\\')) return false
  if (hasControlChars(path)) return false
  return true
}

/**
 * 校验并返回可安全用于站内跳转的 redirect 值；不合法时返回 null。
 * 返回值保持原样（不返回解码结果），避免二次编码语义漂移。
 */
export function sanitizeInternalRedirect(raw: unknown): string | null {
  if (typeof raw !== 'string' || !raw) return null
  if (!isSafeInternalPath(raw)) return null

  // 编码逃逸：%09、%5c、%2f%2f 及其多重编码（如 %2509）逐层解出后再查。
  // 无法继续解码（如 '50%off' 这类字面 %）即停止：不存在更深一层的编码逃逸。
  let current = raw
  for (let depth = 0; depth < 3; depth += 1) {
    let decoded: string
    try {
      decoded = decodeURIComponent(current)
    } catch {
      break
    }
    if (decoded === current) break
    if (!isSafeInternalPath(decoded)) return null
    current = decoded
  }
  return raw
}

/** 认证成功后的落点：合法站内 redirect 优先，否则默认 /dashboard */
export function resolvePostAuthTarget(redirect?: unknown): string {
  return sanitizeInternalRedirect(redirect) ?? DEFAULT_POST_AUTH_TARGET
}
