/**
 * P1 修复回归（UI 验收 round2 待办 #1）：聊天页 assistant 消息双渲染。
 *
 * 原结构「Markdown v-if="assistant" / 光标 span v-if / 纯文本 div v-else」中，
 * v-else 被光标 span 劫持：loading 结束后 span 的 v-if 为 false → v-else 为 true
 * → Markdown 与纯文本同时渲染（肉眼可见第二遍带 ** 记号）。
 * 修复后：assistant → Markdown（含流式光标），否则 → 纯文本，严格互斥。
 *
 * 变异点：还原「光标 span 插在 Markdown 与 v-else 之间」的劫持结构 →
 * 「完成态恰渲染一份」用例红（内容出现次数 1→2、.whitespace-pre-wrap 出现）。
 */
import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createI18n } from 'vue-i18n'
import ChatMessages from '../components/ChatMessages.vue'
import type { ChatMessage } from '@/types/chat'

// E5 起虚拟滚动为 ChatMessages 局部引入（不再经 main.ts 全局注册），测试侧
// 用 vi.mock 把模块替换为直通 stub（按 items 逐项渲染 default slot，语义同旧 stub）。
vi.mock('vue-virtual-scroller', async () => {
  const stubs = await import('@/test-utils/virtualScroller')
  return { DynamicScroller: stubs.DynamicScrollerStub, DynamicScrollerItem: stubs.DynamicScrollerItemStub }
})

// Markdown stub：把 content 原样渲染为文本（真实实现走 v-html，文本内容一致）——
// 「同一内容在 DOM 文本里出现几次」是判别双渲染的核心断言口径。
const MarkdownStub = defineComponent({
  name: 'Markdown',
  props: { content: { type: String, default: '' } },
  setup(props) {
    return () => h('div', { class: 'markdown-stub' }, props.content)
  },
})

const i18n = createI18n({
  legacy: false,
  locale: 'zh',
  messages: {
    zh: {
      chat: {
        sourcesLabel: '来源',
        sourceDoc: '文档',
        unknownDoc: '未知文档',
        noPreview: '无预览',
        relevanceScore: '相关度 {score}',
        useful: '有用',
        notUseful: '没用',
        regenerate: '重新生成',
        backToBottom: '回到底部',
        welcome: '欢迎',
        welcomeDesc: '描述',
      },
    },
  },
})

function mountChat(messages: ChatMessage[], isLoading = false) {
  return mount(ChatMessages, {
    props: { messages, isLoading, showBackToBottom: false, userAvatar: '', suggestions: [] },
    global: {
      plugins: [i18n],
      stubs: {
        Markdown: MarkdownStub,
        'n-icon': true,
        'n-avatar': true,
        'n-button': true,
        'n-tooltip': true,
        'n-tag': true,
        'n-popover': true,
      },
    },
  })
}

const MARK = '唯一标记ALPHA'
const countOf = (text: string, needle: string) => text.split(needle).length - 1

const assistantMsg = (content: string): ChatMessage => ({
  id: 1, content, messageType: 'assistant', conversationId: 1,
})
const userMsg = (content: string): ChatMessage => ({
  id: 2, content, messageType: 'user', conversationId: 1,
})

describe('ChatMessages · 消息体单份渲染（P1 双渲染修复）', () => {
  it('assistant 完成态：Markdown 恰渲染一份，无纯文本副本、无光标', () => {
    const wrapper = mountChat([assistantMsg(`${MARK} **加粗**答案`)])
    // 双渲染时同一内容在 DOM 文本里出现 2 次（Markdown + 纯文本副本）
    expect(countOf(wrapper.text(), MARK)).toBe(1)
    expect(wrapper.find('.whitespace-pre-wrap').exists()).toBe(false)
    expect(wrapper.findAll('.markdown-stub')).toHaveLength(1)
    expect(wrapper.find('.animate-blink').exists()).toBe(false)
  })

  it('assistant 流式（loading+最后一条）：Markdown + 光标并存，仍恰一份', () => {
    const wrapper = mountChat([assistantMsg(`${MARK} 流式中`)], true)
    expect(wrapper.find('.markdown-stub').exists()).toBe(true)
    expect(wrapper.find('.markdown-stub').text()).toContain(MARK)
    expect(wrapper.find('.animate-blink').exists()).toBe(true)
    expect(countOf(wrapper.text(), MARK)).toBe(1)
    expect(wrapper.find('.whitespace-pre-wrap').exists()).toBe(false)
  })

  it('user 消息：恰一份纯文本、不经 Markdown、无光标（含 isLoading 时）', () => {
    const wrapper = mountChat([userMsg(`${MARK} 用户提问`)], true)
    expect(wrapper.find('.whitespace-pre-wrap').exists()).toBe(true)
    expect(countOf(wrapper.text(), MARK)).toBe(1)
    expect(wrapper.findAll('.markdown-stub')).toHaveLength(0)
    expect(wrapper.find('.animate-blink').exists()).toBe(false)
  })
})
