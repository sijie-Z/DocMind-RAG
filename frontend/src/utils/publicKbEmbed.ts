/**
 * 公开知识库 iframe 嵌入代码生成（批次十八）。
 *
 * 安全口径：公开链接本身是 bearer 凭证，token 必须原样进入 iframe src——
 * 本模块只做 URL 规范化与 HTML 属性转义，不记录日志、不做持久化。
 * 非法输入一律返回 null/空串，由调用方展示提示，绝不猜测 token。
 */

const PUBLIC_KB_PATH = '/public/kb/'

/** 裸 token 只允许 URL 安全字符；含空白、斜杠、引号等一律拒绝。 */
const SAFE_TOKEN = /^[A-Za-z0-9._~-]+$/

function escapeHtmlAttribute(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

/**
 * 把「完整链接 / 站内路径 / 裸 token」规范化为公开页地址，并强制 embed=1。
 * 返回 null 表示输入不是可识别的公开知识库链接。
 */
export function resolvePublicKbPageUrl(input: string, origin: string): string | null {
  const trimmed = input.trim()
  if (!trimmed) return null
  // 协议相对地址（//evil.example/...）会解析到第三方源，明确拒绝。
  if (trimmed.startsWith('//')) return null
  try {
    let url: URL
    if (/^https?:\/\//i.test(trimmed)) {
      url = new URL(trimmed)
    } else if (trimmed.startsWith('/')) {
      url = new URL(trimmed, origin)
    } else {
      if (!SAFE_TOKEN.test(trimmed)) return null
      url = new URL(`${PUBLIC_KB_PATH}${encodeURIComponent(trimmed)}`, origin)
    }
    // 带 userinfo 的链接可能伪装来源，且公开页不需要。
    if (url.username || url.password) return null
    if (!url.pathname.startsWith(PUBLIC_KB_PATH)) return null
    const rawToken = url.pathname.slice(PUBLIC_KB_PATH.length)
    // token 是单段路径：空串、尾斜杠或额外路径段都不是公开页。
    if (!rawToken || rawToken.includes('/')) return null
    let token: string
    try {
      token = decodeURIComponent(rawToken)
    } catch {
      return null
    }
    // 完整链接同样要过 token 字符集：`%2F`、空格等编码变体一律拒绝，
    // 避免生成注定 404 或结构异常的片段。
    if (!SAFE_TOKEN.test(token)) return null
    url.pathname = `${PUBLIC_KB_PATH}${encodeURIComponent(token)}`
    url.hash = ''
    url.searchParams.set('embed', '1')
    return url.toString()
  } catch {
    return null
  }
}

/** 解析后可嵌入页面的源；输入非法时返回 null（用于跨源提示）。 */
export function resolvePublicKbPageOrigin(input: string, origin: string): string | null {
  const pageUrl = resolvePublicKbPageUrl(input, origin)
  if (!pageUrl) return null
  try {
    return new URL(pageUrl).origin
  } catch {
    return null
  }
}

/**
 * 生成 iframe 片段：宽度 100%、高度 600、无边框、懒加载。
 * title 进入 HTML 属性前必须转义，避免管理员填写的标题破坏片段结构。
 */
export function buildPublicKbEmbedSnippet(
  input: string,
  title: string,
  origin: string = typeof window === 'undefined' ? '' : window.location.origin,
): string {
  const pageUrl = resolvePublicKbPageUrl(input, origin)
  if (!pageUrl) return ''
  const src = escapeHtmlAttribute(pageUrl)
  const frameTitle = escapeHtmlAttribute(title.trim() || 'DocMind')
  return (
    `<iframe src="${src}" title="${frameTitle}" width="100%" height="600" ` +
    'frameborder="0" style="border:0" loading="lazy"></iframe>'
  )
}
