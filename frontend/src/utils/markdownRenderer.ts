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

/**
 * 流式中间态轻量渲染：纯文本（HTML 转义，配合 whitespace-pre-wrap 保留换行）。
 * 不做 Markdown 解析 / hljs / DOMPurify —— 输出无标签、无属性，注入面为零。
 */
export function renderMarkdownStreaming(content: string): string {
  return md.utils.escapeHtml(content)
}
