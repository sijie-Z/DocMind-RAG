/**
 * nginx 嵌入边界静态断言（批次十八）。
 *
 * 只允许 `/public/kb/` 被跨源 iframe 嵌入；管理/登录等其它 SPA 路由必须
 * 继续发送 X-Frame-Options SAMEORIGIN。nginx 的 add_header 不跨层继承合并：
 * location 一旦声明 add_header，server 级安全头全部失效——本用例同时锁住
 * 「公开 location 重复安全头」「其它 location 不得误放开」「server 级兜底
 * 不被删」「demo 两套 snippet 不漂移」。
 *
 * 变异点：
 * - 删除公开 location 的 CSP frame-ancestors → 红；
 * - 公开 location 保留/继承 X-Frame-Options → 红；
 * - 管理/登录路径误加 frame-ancestors（或去掉 SAMEORIGIN）→ 红；
 * - 用 try_files 回退（内部重定向丢 embed 头）→ 红；
 * - 删除 server 级兜底安全头 / demo 通用 snippet include → 红；
 * - demo 部署漏拷 embed 安全头 snippet → 红。
 */
import { describe, it, expect } from 'vitest'
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

/**
 * vitest 的 cwd 通常是 frontend/（npm script），但也允许从仓库根运行。
 * 不用 import.meta.url：vitest 的模块 runner 可能给出非 file: scheme。
 */
function findFrontendRoot(): string {
  const cwd = process.cwd()
  if (existsSync(resolve(cwd, 'nginx.conf'))) return cwd
  const nested = resolve(cwd, 'frontend')
  if (existsSync(resolve(nested, 'nginx.conf'))) return nested
  throw new Error('无法定位 frontend/nginx.conf')
}

const FRONTEND_ROOT = findFrontendRoot()
const REPO_ROOT = resolve(FRONTEND_ROOT, '..')

const PROD_NGINX = resolve(FRONTEND_ROOT, 'nginx.conf')
const DEMO_NGINX = resolve(REPO_ROOT, 'deploy/demo/nginx.demo.conf')
const DEMO_HEADERS = resolve(REPO_ROOT, 'deploy/demo/security_headers.conf')
const DEMO_EMBED_HEADERS = resolve(REPO_ROOT, 'deploy/demo/security_headers_embed.conf')
const DEMO_DOCKERFILE = resolve(REPO_ROOT, 'deploy/demo/Dockerfile.frontend')

const COMMON_SECURITY_HEADERS = [
  'X-Content-Type-Options "nosniff" always;',
  'X-XSS-Protection "1; mode=block" always;',
  'Referrer-Policy "strict-origin-when-cross-origin" always;',
  'Permissions-Policy "camera=(), microphone=(), geolocation=()" always;',
]
const XFO_SAMEORIGIN = 'X-Frame-Options "SAMEORIGIN" always;'
const FRAME_ANCESTORS = 'Content-Security-Policy "frame-ancestors *" always;'
const ROBOTS_NOINDEX = 'X-Robots-Tag "noindex, nofollow" always;'
const DEMO_HEADERS_INCLUDE = 'include /etc/nginx/snippets/security_headers.conf;'

function read(path: string): string {
  return readFileSync(path, 'utf8')
}

/** 去掉 nginx 注释后再做指令断言：注释里出现指令名（如「不用 X-Frame-Options」）不算配置。 */
function stripComments(text: string): string {
  return text
    .split(/\r?\n/)
    .map((line) => line.replace(/#.*$/, ''))
    .join('\n')
}

/** 从 `{` 位置按花括号配对提取整块（含嵌套 if）。 */
function extractBlockAt(config: string, openBrace: number): string {
  let depth = 0
  for (let i = openBrace; i < config.length; i += 1) {
    if (config[i] === '{') depth += 1
    else if (config[i] === '}') {
      depth -= 1
      if (depth === 0) return config.slice(openBrace, i + 1)
    }
  }
  throw new Error('nginx 配置花括号不平衡')
}

/** 从 `location <header> {` 起点提取 location 块。 */
function extractLocation(config: string, header: string): string {
  const marker = `location ${header} {`
  const start = config.indexOf(marker)
  if (start < 0) throw new Error(`nginx 配置缺少 ${marker}`)
  return extractBlockAt(config, start + marker.length - 1)
}

/** 列出全部 location 块（header 为 `location` 后的匹配式）。 */
function listLocationBlocks(
  config: string,
): Array<{ header: string; body: string; start: number; end: number }> {
  const blocks: Array<{ header: string; body: string; start: number; end: number }> = []
  const pattern = /(^|\n)\s*location\s+([^\n{]+)\{/g
  let match: RegExpExecArray | null
  while ((match = pattern.exec(config)) !== null) {
    const open = config.indexOf('{', match.index)
    const body = extractBlockAt(config, open)
    blocks.push({ header: match[2].trim(), body, start: open, end: open + body.length })
  }
  return blocks
}

/** 指令是否出现在 server 作用域（任意 location 块之外）。 */
function hasServerScopeDirective(config: string, directive: string): boolean {
  const ranges = listLocationBlocks(config).map(({ start, end }) => [start, end])
  let from = 0
  for (;;) {
    const index = config.indexOf(directive, from)
    if (index < 0) return false
    if (!ranges.some(([start, end]) => index >= start && index <= end)) return true
    from = index + directive.length
  }
}

function expectEmbedPublicLocation(block: string): void {
  // 允许嵌入的唯一开关：CSP frame-ancestors *；同一 location 不得再发 XFO。
  expect(block).toMatch(FRAME_ANCESTORS)
  expect(block).not.toMatch(/X-Frame-Options/i)
  // 能力 URL 不得被搜索引擎收录。
  expect(block).toContain(ROBOTS_NOINDEX)
  // nginx add_header 不继承：公开路径必须自行重复全部安全头。
  for (const header of COMMON_SECURITY_HEADERS) {
    expect(block).toContain(header)
  }
  // SPA 回退必须在当前 location 内完成；try_files 的 URI 回退会内部
  // 重定向到 location /，导致上面这些 embed 头丢失。
  expect(block).toMatch(/rewrite \^ \/index\.html break;/)
  expect(block).not.toMatch(/try_files/)
  expect(block).not.toMatch(/unsafe-inline/)
  // 服务端入口标记：前端路由守卫用它识别「可被任意来源框住」的文档。
  expect(block).toContain(
    'sub_filter \'<head>\' \'<head><meta name="docmind-public-kb-entry" content="1">\';',
  )
  expect(block).toContain('sub_filter_once on;')
}

describe('production nginx (frontend/nginx.conf)', () => {
  const config = stripComments(read(PROD_NGINX))

  it('server 级兜底安全头仍在（覆盖未声明 add_header 的 API/WS location）', () => {
    expect(hasServerScopeDirective(config, XFO_SAMEORIGIN)).toBe(true)
    for (const header of COMMON_SECURITY_HEADERS) {
      expect(hasServerScopeDirective(config, header), header).toBe(true)
    }
  })

  it('仅 /public/kb/ 允许跨源嵌入（CSP frame-ancestors *）', () => {
    const publicBlock = extractLocation(config, '/public/kb/')
    expectEmbedPublicLocation(publicBlock)
    // 全文件只允许这一处 frame-ancestors；任何其它路径放开都会使计数 >1。
    expect(config.match(/frame-ancestors/g)?.length ?? 0).toBe(1)
    expect(config).not.toMatch(/X-Frame-Options "ALLOWALL"/i)
    expect(config).not.toMatch(/unsafe-inline/)
  })

  it('SPA/管理/登录（location /）显式保持 SAMEORIGIN 与全部安全头', () => {
    const spaBlock = extractLocation(config, '/')
    expect(spaBlock).toContain(XFO_SAMEORIGIN)
    for (const header of COMMON_SECURITY_HEADERS) {
      expect(spaBlock).toContain(header)
    }
    expect(spaBlock).not.toMatch(/frame-ancestors/)
  })

  it('静态资源 location 不因 add_header 丢安全头', () => {
    const assetsBlock = extractLocation(config, '/assets/')
    expect(assetsBlock).toContain(XFO_SAMEORIGIN)
    for (const header of COMMON_SECURITY_HEADERS) {
      expect(assetsBlock).toContain(header)
    }
  })

  it('API 前缀不放开嵌入（继承 server 级 SAMEORIGIN）', () => {
    const apiBlock = extractLocation(config, '/api/')
    expect(apiBlock).not.toMatch(/frame-ancestors|X-Frame-Options/i)
    expect(apiBlock).not.toMatch(/\badd_header\b/)
  })

  it('结构不变量：任何自行声明 add_header 的非公开 location 都必须重复安全头', () => {
    for (const { header, body } of listLocationBlocks(config)) {
      if (!/\badd_header\b/.test(body)) continue
      if (header.startsWith('/public/kb/')) continue
      // 既有 /healthz 是纯文本健康检查（只有 Content-Type），不承载页面。
      if (header === '= /healthz') continue
      expect(body, `${header} 丢失 ${XFO_SAMEORIGIN}`).toContain(XFO_SAMEORIGIN)
      for (const common of COMMON_SECURITY_HEADERS) {
        expect(body, `${header} 丢失 ${common}`).toContain(common)
      }
    }
  })
})

describe('demo nginx (deploy/demo)', () => {
  const config = stripComments(read(DEMO_NGINX))
  const headers = stripComments(read(DEMO_HEADERS))
  const embedHeaders = stripComments(read(DEMO_EMBED_HEADERS))
  const dockerfile = stripComments(read(DEMO_DOCKERFILE))

  function addHeaderLines(text: string): string[] {
    return text
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter((line) => line.startsWith('add_header'))
      .sort()
  }

  it('server 级通用安全头 include 仍在（覆盖 API/WS 等未单独声明的 location）', () => {
    expect(hasServerScopeDirective(config, DEMO_HEADERS_INCLUDE)).toBe(true)
  })

  it('公开 location 使用专用 embed 安全头（含 CSP、不含 XFO）', () => {
    const publicBlock = extractLocation(config, '/public/kb/')
    expect(publicBlock).toContain('include /etc/nginx/snippets/security_headers_embed.conf;')
    expect(publicBlock).not.toContain(DEMO_HEADERS_INCLUDE)
    expect(publicBlock).not.toMatch(/X-Frame-Options/i)
    expectEmbedPublicLocation(`${publicBlock}\n${embedHeaders}`)
  })

  it('embed 安全头 snippet 只允许嵌入且保留其余安全头', () => {
    expect(embedHeaders).toMatch(FRAME_ANCESTORS)
    expect(embedHeaders).toContain(ROBOTS_NOINDEX)
    expect(embedHeaders).not.toMatch(/X-Frame-Options/i)
    for (const header of COMMON_SECURITY_HEADERS) {
      expect(embedHeaders).toContain(header)
    }
    expect(embedHeaders).toContain(
      'Strict-Transport-Security "max-age=31536000; includeSubDomains" always;',
    )
    expect(embedHeaders).not.toMatch(/unsafe-inline/)
  })

  it('通用与 embed 安全头不漂移：embed = 通用 − XFO + CSP + noindex', () => {
    const general = addHeaderLines(headers)
    const embed = addHeaderLines(embedHeaders)
    const generalWithoutXfo = general.filter(
      (line) => !line.startsWith('add_header X-Frame-Options'),
    )
    const embedWithoutFramePolicy = embed.filter(
      (line) =>
        !line.startsWith('add_header Content-Security-Policy') &&
        !line.startsWith('add_header X-Robots-Tag'),
    )
    expect(embedWithoutFramePolicy).toEqual(generalWithoutXfo)
    expect(
      embed.filter((line) => line.startsWith('add_header Content-Security-Policy')),
    ).toEqual([`add_header ${FRAME_ANCESTORS}`])
    expect(embed.filter((line) => line.startsWith('add_header X-Robots-Tag'))).toEqual([
      `add_header ${ROBOTS_NOINDEX}`,
    ])
  })

  it('管理/登录（location /）仍通过通用安全头发送 SAMEORIGIN', () => {
    expect(headers).toContain(XFO_SAMEORIGIN)
    const spaBlock = extractLocation(config, '/')
    expect(spaBlock).toContain(DEMO_HEADERS_INCLUDE)
    expect(spaBlock).not.toMatch(/frame-ancestors/)
    expect(extractLocation(config, '/assets/')).toContain(DEMO_HEADERS_INCLUDE)
  })

  it('结构不变量：任何自行声明 add_header 的非公开 location 都引用通用安全头', () => {
    for (const { header, body } of listLocationBlocks(config)) {
      if (!/\badd_header\b/.test(body)) continue
      if (header.startsWith('/public/kb/')) continue
      expect(body, `${header} 缺少 ${DEMO_HEADERS_INCLUDE}`).toContain(DEMO_HEADERS_INCLUDE)
    }
  })

  it('demo 配置正文不含任何 frame-ancestors（只允许出现在 embed snippet）', () => {
    expect(config).not.toMatch(/frame-ancestors/)
    expect(embedHeaders.match(/frame-ancestors/g)?.length ?? 0).toBe(1)
  })

  it('demo 镜像会把 embed 安全头 snippet 拷入容器', () => {
    expect(dockerfile).toContain(
      'COPY deploy/demo/security_headers_embed.conf /etc/nginx/snippets/security_headers_embed.conf',
    )
  })
})
