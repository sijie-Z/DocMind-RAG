<template>
  <div class="markdown-body prose dark:prose-invert max-w-none text-sm leading-relaxed" v-html="renderedContent"></div>
</template>

<script setup lang="ts">
/**
 * Markdown 渲染组件
 * 支持：Markdown 语法、代码高亮、数学公式 (KaTeX)、代码块一键复制
 * 小白说明：这个组件就像是一个“翻译官”，把 AI 返回的带有特殊标记的文字（比如 **加粗**、```代码块```）
 * 转换成我们在网页上看到的漂亮样式。
 */
import { computed, onMounted, nextTick } from 'vue'
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'
import katex from 'katex'
import mdKatex from 'markdown-it-katex'
import 'highlight.js/styles/github-dark.css' // 默认使用 github-dark 风格
import 'katex/dist/katex.min.css'

interface Props {
  content: string
}

const props = defineProps<Props>()

// 初始化 markdown-it
// 小白说明：这里配置了“翻译官”的规则，比如遇到代码要用 highlight.js 来染色
const md: MarkdownIt = new MarkdownIt({
  html: true,
  linkify: true,
  typographer: true,
  highlight: (str: string, lang: string): string => {
    if (lang && hljs.getLanguage(lang)) {
      try {
        return `<pre class="hljs"><code>${
          hljs.highlight(str, { language: lang, ignoreIllegals: true }).value
        }</code></pre>`
      } catch (__) {}
    }
    return `<pre class="hljs"><code>${md.utils.escapeHtml(str)}</code></pre>`
  }
})

// 使用 KaTeX 插件支持数学公式
md.use(mdKatex)

// 渲染内容
const renderedContent = computed(() => {
  if (!props.content) return ''
  return md.render(props.content)
})

// 为代码块添加复制按钮的逻辑
const addCopyButtons = () => {
  const blocks = document.querySelectorAll('pre.hljs')
  blocks.forEach((block) => {
    // 如果已经有按钮了就不再添加
    if (block.querySelector('.copy-btn')) return

    const button = document.createElement('button')
    button.className = 'copy-btn'
    button.textContent = '复制'
    button.onclick = () => {
      const code = block.querySelector('code')?.innerText || ''
      navigator.clipboard.writeText(code).then(() => {
        button.textContent = '已复制!'
        setTimeout(() => {
          button.textContent = '复制'
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

// 当内容更新时，重新添加复制按钮
import { watch } from 'vue'
watch(() => props.content, () => {
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
  @apply rounded-xl my-4 p-4 bg-gray-900 overflow-x-auto relative group;
}

.markdown-body code {
  @apply font-mono text-xs;
}

/* 复制按钮样式 */
.copy-btn {
  @apply absolute top-2 right-2 px-2 py-1 text-[10px] bg-white/10 hover:bg-white/20 
         text-gray-400 rounded border border-white/10 opacity-0 group-hover:opacity-100 
         transition-all duration-200 cursor-pointer;
}

/* KaTeX 修正 */
.katex-display {
  @apply my-4 overflow-x-auto overflow-y-hidden;
}

/* 列表样式 */
.markdown-body ul {
  @apply list-disc pl-5 my-2;
}

.markdown-body ol {
  @apply list-decimal pl-5 my-2;
}

.markdown-body li {
  @apply my-1;
}

/* 引用样式 */
.markdown-body blockquote {
  @apply border-l-4 border-gray-300 dark:border-gray-600 pl-4 my-4 italic text-gray-600 dark:text-gray-400;
}

/* 表格样式 */
.markdown-body table {
  @apply w-full border-collapse my-4;
}

.markdown-body th, .markdown-body td {
  @apply border border-gray-200 dark:border-gray-700 px-3 py-2 text-left;
}

.markdown-body th {
  @apply bg-gray-50 dark:bg-gray-800/50 font-bold;
}
</style>
