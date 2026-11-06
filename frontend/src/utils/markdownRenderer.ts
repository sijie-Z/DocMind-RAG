/**
 * Markdown 渲染单例（E3 性能批）。
 *
 * 原实现 Markdown.vue 每个组件实例都 `new MarkdownIt(...)` 并 `md.use(mdKatex)`
 * 注册一次插件（聊天页每条消息一个实例，重复构建渲染器）；侧栏/工作区多入口
 * 同样如此。这里收敛为模块级单例：插件只注册一次，所有 Markdown 实例共享。
 *
 * 同时提供两条渲染路径：
 * - renderMarkdownFull：完整 Markdown 解析 + hljs 高亮 + DOMPurify 消毒（终态）；
 * - renderMarkdownStreaming：仅 HTML 转义的纯文本轻量渲染（流式中间态）。
 *   原实现每个 chunk 都对「全量累积内容」跑一次完整渲染，累计 O(n²)；流式期间
 *   走轻量路径后，完整渲染只在终态发生一次。
 */
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js/lib/common'
import DOMPurify from 'dompurify'
import mdKatex from '@vscode/markdown-it-katex'

// markdown-it 15 起，默认导出是 `MarkdownItCallable`（一个 const，兼容不带 new 的旧写法），
// 它只有「值」没有「类型」含义 —— 因此不能再拿默认导入当类型标注。
// 这里去掉显式标注，让 TS 从 `new` 的构造签名推断出 MarkdownIt 实例类型。
const md = new MarkdownIt({
  html: false,
  linkify: true,
  typographer: true,
  highlight: (str: string, lang: string): string => {
    if (lang && hljs.getLanguage(lang)) {
      try {
        return `<pre class="hljs"><code>${
          hljs.highlight(str, { language: lang, ignoreIllegals: true }).value
        }</code></pre>`
      } catch { /* lang not available, fall through */ }
    }
    return `<pre class="hljs"><code>${md.utils.escapeHtml(str)}</code></pre>`
  }
})

// 插件只注册一次（模块级单例的全部实例共享）
md.use(mdKatex)

/** 模块级单例（测试用于断言跨组件/跨调用同引用）。 */
export function getMarkdownRenderer() {
  return md
}

/** 终态完整渲染：Markdown → DOMPurify 消毒。 */
export function renderMarkdownFull(content: string): string {
  const raw = md.render(content)
  return DOMPurify.sanitize(raw, {
    ADD_TAGS: ['math', 'semantics', 'mrow', 'mi', 'mo', 'mn', 'msup', 'mfrac'],
    ADD_ATTR: ['xmlns', 'mathvariant'],
  })
}

const CITATION_PATTERN = /\[(\d{1,3})\]/

/**
 * 聊天回答专用：在完整 Markdown 渲染后把正文里的 `[n]` 变成可点击引用。
 *
 * - 只处理文本节点；代码块/行内代码/链接/公式内的 `[n]` 原样保留；
 * - 先 DOMPurify 再走 DOM，避免把未消毒 HTML 放进临时容器；
 * - 输出再次消毒（role/tabindex/data-* 均为 DOMPurify 默认允许）。
 */
export function renderMarkdownWithCitations(content: string): string {
  const sanitizeOptions = {
    ADD_TAGS: ['math', 'semantics', 'mrow', 'mi', 'mo', 'mn', 'msup', 'mfrac'],
    ADD_ATTR: ['xmlns', 'mathvariant'],
  }
  const safe = DOMPurify.sanitize(md.render(content), sanitizeOptions)
  if (typeof document === 'undefined') return safe

  const container = document.createElement('div')
  container.innerHTML = safe
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const parent = (node as Text).parentElement
      if (!parent) return NodeFilter.FILTER_REJECT
      if (parent.closest('pre, code, a, .katex, .katex-display, .copy-btn')) {
        return NodeFilter.FILTER_REJECT
      }
      return CITATION_PATTERN.test((node as Text).nodeValue || '')
        ? NodeFilter.FILTER_ACCEPT
        : NodeFilter.FILTER_REJECT
    },
  })
  const textNodes: Text[] = []
  while (walker.nextNode()) textNodes.push(walker.currentNode as Text)

  for (const node of textNodes) {
    const parts = (node.nodeValue || '').split(/(\[\d{1,3}\])/)
    if (parts.length < 2) continue
    const fragment = document.createDocumentFragment()
    for (const part of parts) {
      const match = /^\[(\d{1,3})\]$/.exec(part)
      if (!match) {
        if (part) fragment.appendChild(document.createTextNode(part))
        continue
      }
      const citation = document.createElement('sup')
      citation.className = 'chat-citation'
      citation.dataset.citationIndex = match[1]
      citation.setAttribute('role', 'button')
      citation.setAttribute('tabindex', '0')
      citation.textContent = part
      fragment.appendChild(citation)
    }
    node.parentNode?.replaceChild(fragment, node)
  }
  return DOMPurify.sanitize(container.innerHTML, sanitizeOptions)
}

/**
 * 流式中间态轻量渲染：纯文本（HTML 转义，配合 whitespace-pre-wrap 保留换行）。
 * 不做 Markdown 解析 / hljs / DOMPurify —— 输出无标签、无属性，注入面为零。
 */
export function renderMarkdownStreaming(content: string): string {
  return md.utils.escapeHtml(content)
}
