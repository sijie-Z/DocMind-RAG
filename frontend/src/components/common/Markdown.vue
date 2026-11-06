<template>
  <div
    ref="rootEl"
    class="markdown-body prose dark:prose-invert max-w-none text-sm leading-relaxed"
    :class="{ 'whitespace-pre-wrap': streaming }"
    v-html="renderedContent"
    @click="handleCitationClick"
    @keydown="handleCitationKeydown"
  ></div>
</template>

<script setup lang="ts">
import { computed, onMounted, nextTick, watch, ref } from 'vue'
import {
  renderMarkdownFull,
  renderMarkdownStreaming,
  renderMarkdownWithCitations,
} from '@/utils/markdownRenderer'
import 'highlight.js/styles/github-dark.css'
import 'katex/dist/katex.min.css'
import i18n from '@/locales'

interface Props {
  content: string
  /** 流式生成中：中间态走纯文本轻量渲染，终态再一次性做完整 Markdown+DOMPurify（E3） */
  streaming?: boolean
  /** 聊天回答：把正文里的 [n] 渲染为可点击引用并在点击时 emit citation */
  citations?: boolean
}

const props = withDefaults(defineProps<Props>(), { streaming: false, citations: false })

const emit = defineEmits<{
  citation: [index: number]
}>()

const citationIndexFromEvent = (target: EventTarget | null): number | null => {
  const element = (target as HTMLElement | null)?.closest?.('[data-citation-index]')
  if (!element || !rootEl.value?.contains(element)) return null
  const index = Number((element as HTMLElement).dataset.citationIndex)
  return Number.isInteger(index) && index > 0 ? index : null
}

const handleCitationClick = (event: MouseEvent) => {
  const index = citationIndexFromEvent(event.target)
  if (index !== null) emit('citation', index)
}

const handleCitationKeydown = (event: KeyboardEvent) => {
  if (event.key !== 'Enter' && event.key !== ' ') return
  const index = citationIndexFromEvent(event.target)
  if (index === null) return
  event.preventDefault()
  emit('citation', index)
}

// 组件根节点：代码块复制按钮的定位范围（替代原全文档 querySelectorAll 扫描）
const rootEl = ref<HTMLElement | null>(null)

const contentCache = new Map<string, string>()
const MAX_CACHE_SIZE = 200

const renderedContent = computed(() => {
  if (!props.content) return ''
  // E3：流式中间态轻量渲染（纯文本）——原实现每个 chunk 对全量累积内容跑一次
  // Markdown+hljs+DOMPurify，累计 O(n²)；完整渲染改到终态一次（streaming=false）。
  if (props.streaming) return renderMarkdownStreaming(props.content)
  const cacheKey = `${props.citations ? 'cite:' : ''}${props.content}`
  const cached = contentCache.get(cacheKey)
  if (cached) return cached

  const sanitized = props.citations
    ? renderMarkdownWithCitations(props.content, (index) =>
        i18n.global.t('chat.viewSource', { n: index })
      )
    : renderMarkdownFull(props.content)

  if (contentCache.size >= MAX_CACHE_SIZE) {
    const firstKey = contentCache.keys().next().value
    if (firstKey !== undefined) contentCache.delete(firstKey)
  }
  contentCache.set(cacheKey, sanitized)
  return sanitized
})

// 为代码块添加复制按钮的逻辑
// （体验第四波：按钮文案改走全局 i18n 单例——动态创建的 DOM 无法注入 useI18n；
//   按钮在内容变化时重建，语言切换后在下次内容更新时取到新文案）
const addCopyButtons = () => {
  // E3：只在组件根节点内定位代码块（原 document.querySelectorAll 全文档扫描：
  // 聊天页消息数越多、每次内容变化扫描成本越高）
  const blocks = rootEl.value?.querySelectorAll('pre.hljs')
  if (!blocks) return
  blocks.forEach((block) => {
    // 如果已经有按钮了就不再添加
    if (block.querySelector('.copy-btn')) return

    const button = document.createElement('button')
    button.className = 'copy-btn'
    button.setAttribute('data-state', 'idle')
    button.textContent = i18n.global.t('common.copy')
    button.onclick = () => {
      const code = block.querySelector('code')?.innerText || ''
      navigator.clipboard.writeText(code).then(() => {
        button.setAttribute('data-state', 'copied')
        button.textContent = i18n.global.t('common.copied')
        setTimeout(() => {
          button.setAttribute('data-state', 'idle')
          button.textContent = i18n.global.t('common.copy')
        }, 2000)
      })
    }
    block.appendChild(button)
    // 确保 pre 标签是相对定位，方便按钮定位
    ;(block as HTMLElement).style.position = 'relative'
  })
}

onMounted(() => {
  addCopyButtons()
})

// 内容/渲染模式变化时重建复制按钮（v-html 重渲染会清掉动态按钮）
watch(renderedContent, () => {
  nextTick(() => {
    addCopyButtons()
  })
})
</script>

<style>
/* Markdown 样式微调 */
.markdown-body {
  word-break: break-word;
}

.markdown-body pre.hljs {
  border-radius: 12px;
  margin: 1rem 0;
  padding: 1rem;
  background: #1a1b26;
  overflow-x: auto;
  position: relative;
}

.markdown-body pre.hljs:hover .copy-btn {
  opacity: 1;
}

.markdown-body code {
  font-family: 'Cascadia Code', 'Fira Code', 'JetBrains Mono', monospace;
  font-size: 0.75rem;
}

/* 复制按钮样式 */
.copy-btn {
  position: absolute;
  top: 0.5rem;
  right: 0.5rem;
  padding: 0.25rem 0.5rem;
  font-size: 10px;
  background: rgba(255, 255, 255, 0.1);
  color: #9ca3af;
  border-radius: 4px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  opacity: 0;
  transition: all 0.2s;
  cursor: pointer;
}

.copy-btn:hover {
  background: rgba(255, 255, 255, 0.2);
}

.copy-btn[data-state="copied"] {
  color: #10b981;
}

/* 引用角标：聊天回答里的 [n]，点击/Enter 打开对应来源。
   垂直 padding 扩大命中区但不撑行高；相邻角标留 2px 间距。 */
.markdown-body .chat-citation {
  color: #1d4ed8;
  cursor: pointer;
  font-size: 0.72em;
  font-weight: 600;
  line-height: 1;
  padding: 0.35em 0.2em;
  margin: 0 2px;
  vertical-align: super;
  border-radius: 2px;
}
.markdown-body .chat-citation:hover {
  background: rgba(37, 99, 235, 0.12);
}
.markdown-body .chat-citation:focus-visible {
  outline: 2px solid #2563eb;
  outline-offset: 2px;
}
.dark .markdown-body .chat-citation {
  color: #60a5fa;
}
.dark .markdown-body .chat-citation:focus-visible {
  outline-color: #60a5fa;
}
@media (pointer: coarse) {
  .markdown-body .chat-citation {
    padding: 0.6em 0.3em;
  }
}

/* KaTeX 修正 */
.katex-display {
  margin: 1rem 0;
  overflow-x: auto;
  overflow-y: hidden;
}

/* 列表样式 */
.markdown-body ul {
  list-style: disc;
  padding-left: 1.25rem;
  margin: 0.5rem 0;
}

.markdown-body ol {
  list-style: decimal;
  padding-left: 1.25rem;
  margin: 0.5rem 0;
}

.markdown-body li {
  margin: 0.25rem 0;
}

/* 引用样式 */
.markdown-body blockquote {
  border-left: 4px solid #d1d5db;
  padding-left: 1rem;
  margin: 1rem 0;
  font-style: italic;
  color: #6b7280;
}

.dark .markdown-body blockquote {
  border-left-color: #4b5563;
  color: #9ca3af;
}

/* 表格样式 */
.markdown-body table {
  width: 100%;
  border-collapse: collapse;
  margin: 1rem 0;
}

.markdown-body th,
.markdown-body td {
  border: 1px solid #e5e7eb;
  padding: 0.5rem 0.75rem;
  text-align: left;
}

.dark .markdown-body th,
.dark .markdown-body td {
  border-color: #374151;
}

.markdown-body th {
  background: #f9fafb;
  font-weight: 700;
}

.dark .markdown-body th {
  background: rgba(31, 41, 55, 0.5);
}

/* 链接样式 */
.markdown-body a {
  color: #3b82f6;
  text-decoration: none;
}

.markdown-body a:hover {
  text-decoration: underline;
}

/* 标题样式 */
.markdown-body h1, .markdown-body h2, .markdown-body h3 {
  margin-top: 1.5rem;
  margin-bottom: 0.5rem;
  font-weight: 600;
}

.markdown-body h1 { font-size: 1.5rem; }
.markdown-body h2 { font-size: 1.25rem; }
.markdown-body h3 { font-size: 1.125rem; }

/* 分割线 */
.markdown-body hr {
  border: none;
  border-top: 1px solid #e5e7eb;
  margin: 1.5rem 0;
}

.dark .markdown-body hr {
  border-top-color: #374151;
}
</style>
