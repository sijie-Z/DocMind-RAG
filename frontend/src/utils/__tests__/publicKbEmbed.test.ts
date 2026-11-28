/**
 * 公开知识库 iframe 片段生成（批次十八）判别用例。
 *
 * 变异点：
 * - 不强制 embed=1 → 链接用例红；
 * - 接受任意路径/非法 token → 非法输入用例红；
 * - 标题不转义 → 转义用例红；
 * - 缺少 width/height/frameborder/loading → 片段属性用例红。
 */
import { describe, it, expect } from 'vitest'
import { buildPublicKbEmbedSnippet, resolvePublicKbPageUrl } from '../publicKbEmbed'

const ORIGIN = 'https://docmind.example.com'

describe('resolvePublicKbPageUrl', () => {
  it('裸 token → 当前源的公开页并强制 embed=1', () => {
    expect(resolvePublicKbPageUrl('tok-abc_123', ORIGIN)).toBe(
      `${ORIGIN}/public/kb/tok-abc_123?embed=1`,
    )
  })

  it('站内路径补全源，保留 token', () => {
    expect(resolvePublicKbPageUrl('/public/kb/tok-1', ORIGIN)).toBe(
      `${ORIGIN}/public/kb/tok-1?embed=1`,
    )
  })

  it('完整链接保留来源、覆盖 embed 值并去掉 hash', () => {
    expect(
      resolvePublicKbPageUrl('https://docs.example.org/public/kb/tok-2?embed=0&lang=en#top', ORIGIN),
    ).toBe('https://docs.example.org/public/kb/tok-2?embed=1&lang=en')
  })

  it.each([
    ['空串', ''],
    ['非公开路径', 'https://docs.example.org/dashboard'],
    ['额外路径段', 'https://docs.example.org/public/kb/tok/extra'],
    ['尾斜杠空 token', 'https://docs.example.org/public/kb/'],
    ['带空白的裸 token', 'tok abc'],
    ['HTML 注入尝试', '"><script>alert(1)</script>'],
    ['非法 URL', 'http://[::1'],
    ['协议相对地址', '//evil.example/public/kb/tok-1'],
    ['带 userinfo 的链接', 'https://user:pass@docs.example.org/public/kb/tok-1'],
    ['编码斜杠 token', 'https://docs.example.org/public/kb/tok%2Fextra'],
    ['编码空白 token', 'https://docs.example.org/public/kb/tok%20x'],
  ])('拒绝非法输入：%s', (_label, input) => {
    expect(resolvePublicKbPageUrl(input, ORIGIN)).toBeNull()
  })
})

describe('buildPublicKbEmbedSnippet', () => {
  it('生成 100% 宽、600 高、无边框、lazy 的 iframe 片段', () => {
    const snippet = buildPublicKbEmbedSnippet(
      `${ORIGIN}/public/kb/tok-1`,
      'Public policy KB',
      ORIGIN,
    )
    expect(snippet).toContain(`src="${ORIGIN}/public/kb/tok-1?embed=1"`)
    expect(snippet).toContain('title="Public policy KB"')
    expect(snippet).toContain('width="100%"')
    expect(snippet).toContain('height="600"')
    expect(snippet).toContain('frameborder="0"')
    expect(snippet).toContain('style="border:0"')
    expect(snippet).toContain('loading="lazy"')
    expect(snippet.startsWith('<iframe ')).toBe(true)
    expect(snippet.endsWith('</iframe>')).toBe(true)
  })

  it('标题与 URL 中的特殊字符经过 HTML 属性转义', () => {
    const snippet = buildPublicKbEmbedSnippet(
      `${ORIGIN}/public/kb/tok-1?lang=en`,
      'A "quoted" <b>& title',
      ORIGIN,
    )
    expect(snippet).toContain('title="A &quot;quoted&quot; &lt;b&gt;&amp; title"')
    expect(snippet).toContain('lang=en&amp;embed=1')
    expect(snippet).not.toContain('<b>')
  })

  it('非法输入返回空串（不渲染复制按钮/片段）', () => {
    expect(buildPublicKbEmbedSnippet('', 'KB', ORIGIN)).toBe('')
    expect(buildPublicKbEmbedSnippet('https://docs.example.org/login', 'KB', ORIGIN)).toBe('')
  })

  it('空标题回退到 DocMind，保持可访问性 title', () => {
    const snippet = buildPublicKbEmbedSnippet('tok-1', '   ', ORIGIN)
    expect(snippet).toContain('title="DocMind"')
  })
})
