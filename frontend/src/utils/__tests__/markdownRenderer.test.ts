/**
 * E3（性能批）：Markdown 渲染器模块级单例 + 双渲染路径判别测试。
 *
 * 修前：Markdown.vue 每个组件实例 new MarkdownIt + use 插件（重复构建渲染器），
 * 且每个 chunk 对全量内容做完整 Markdown+DOMPurify。
 * 修后：渲染器模块级单例；流式中间态纯文本轻量渲染，终态完整渲染。
 *
 * 变异点（回退判别）：
 * - 恢复每实例创建渲染器 → 单例断言红；
 * - 流式路径改回完整渲染 → 「中间态不解析标签」断言红。
 */
import { describe, it, expect } from 'vitest'
import {
  getMarkdownRenderer,
  renderMarkdownFull,
  renderMarkdownStreaming,
  renderMarkdownWithCitations,
} from '../markdownRenderer'

describe('markdownRenderer · 模块级单例（E3）', () => {
  it('跨调用返回同一 MarkdownIt 实例（插件只注册一次）', () => {
    expect(getMarkdownRenderer()).toBe(getMarkdownRenderer())
  })

  it('流式中间态：HTML 转义为纯文本，不解析 Markdown 标签', () => {
    const out = renderMarkdownStreaming('# 标题\n<script>alert(1)</script>')
    expect(out).not.toContain('<h1')
    expect(out).not.toContain('<script')
    expect(out).toContain('&lt;script&gt;')
    expect(out).toContain('# 标题')
  })

  it('终态：完整 Markdown 渲染 + 消毒（标题成标签、脚本不落地）', () => {
    const out = renderMarkdownFull('# 标题\n\n<script>alert(1)</script>')
    expect(out).toContain('<h1>')
    expect(out).not.toContain('<script')
  })
})

describe('markdownRenderer · 聊天引用角标', () => {
  it('正文 [n] 变成可点击 sup，文本保持不变', () => {
    const out = renderMarkdownWithCitations('付款期限为30天[1]，质保期12个月[2]。')

    expect(out).toContain('class="chat-citation"')
    expect(out).toContain('data-citation-index="1"')
    expect(out).toContain('data-citation-index="2"')
    expect(out).toContain('[1]')
    expect(out).toContain('付款期限为30天')
    // 角标只包住标记本身，不吞正文。
    expect(out).toContain('付款期限为30天<sup')
  })

  it('代码块/行内代码/链接内的 [n] 原样保留', () => {
    const out = renderMarkdownWithCitations(
      '`[1]`\n\n```\n[2]\n```\n\n[链接](https://x.test/[3])\n\n正文[4]'
    )

    expect(out).toContain('data-citation-index="4"')
    expect(out).not.toContain('data-citation-index="1"')
    expect(out).not.toContain('data-citation-index="2"')
    expect(out).not.toContain('data-citation-index="3"')
  })

  it('非数字/超长方括号不转换', () => {
    const out = renderMarkdownWithCitations('备注[a]、[1234] 与 [1]')

    expect(out).toContain('data-citation-index="1"')
    expect(out).not.toContain('data-citation-index="1234"')
    expect(out).not.toContain('data-citation-index="a"')
  })

  it('HTML 仍被消毒，引用标记照常转换', () => {
    const out = renderMarkdownWithCitations('<img src=x onerror=alert(1)>[1]')

    expect(out).not.toContain('<img')
    expect(out).toContain('data-citation-index="1"')
  })
})
