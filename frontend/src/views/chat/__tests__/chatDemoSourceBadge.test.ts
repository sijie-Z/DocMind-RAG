/**
 * T4 契约回归（第二轮 §7.2④）：demo 假分数「演示数据」角标。
 *
 * 背景：示例对话（/demo/seed 落库）的 sources 是写死的假检索结果，
 * relevance_score 0.79~0.95 与真实检索无关。后端已逐条标注 `demo: true`
 * （test_demo_sources_labeled.py 钉住），前端读点是本文件：
 * 有 demo 来源 → 渲染「演示数据」角标；无 → 不渲染（正常结果不得被误标）。
 * 相关度数字保留（只标注、不隐藏）。
 *
 * 变异点：去掉角标渲染 / 去掉 hasDemoSources 判定（恒渲染或恒不渲染）→ 本文件红。
 */
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createI18n } from 'vue-i18n'
import ChatMessages from '../components/ChatMessages.vue'
import type { ChatMessage } from '@/types/chat'

const DynamicScroller = defineComponent({
  props: { items: { type: Array, default: () => [] } },
  setup(props, { slots }) {
    return () =>
      h('div', props.items.map((item, index) => slots.default?.({ item, index, active: true })))
  },
})

const DynamicScrollerItem = defineComponent({
  setup(_, { slots }) {
    return () => h('div', slots.default?.())
  },
})

const MarkdownStub = defineComponent({
  name: 'Markdown',
  props: { content: { type: String, default: '' } },
  setup(props) {
    return () => h('div', props.content)
  },
})

// n-popover 直通替身（同 chatSourcePopover.test.ts）：让弹层内容进入 DOM，
// 以便断言相关度数字仍在渲染（数字保留 = 只标注、不隐藏）
const NPopoverPassthrough = defineComponent({
  name: 'Popover',
  setup(_, { slots }) {
    return () => h('div', { class: 'popover-passthrough' }, [slots.trigger?.(), slots.default?.()])
  },
})

const i18n = createI18n({
  legacy: false,
  locale: 'zh',
  messages: {
    zh: {
      chat: {
        sourcesLabel: '来源引用：',
        sourceDoc: '文档',
        unknownDoc: '未知文档',
        noPreview: '无预览',
        relevanceScore: '相关度: {score}%',
        demoData: '演示数据',
        useful: '有用',
        notUseful: '没用',
        regenerate: '重新生成',
        backToBottom: '回到底部',
      },
    },
  },
})

function makeMessage(sources: Record<string, unknown>[], id = 1) {
  return {
    id,
    content: '回答正文',
    messageType: 'assistant',
    conversationId: 1,
    sources,
  } as unknown as ChatMessage
}

function mountChat(messages: ChatMessage[]) {
  return mount(ChatMessages, {
    props: { messages, isLoading: false, showBackToBottom: false, userAvatar: '', suggestions: [] },
    global: {
      plugins: [i18n],
      components: { DynamicScroller, DynamicScrollerItem },
      stubs: {
        Markdown: MarkdownStub,
        Popover: NPopoverPassthrough,
        'n-icon': true,
        'n-avatar': true,
        'n-button': true,
        'n-tooltip': true,
        'n-tag': true,
      },
    },
  })
}

describe('ChatMessages · demo 来源「演示数据」角标（T4 §7.2④）', () => {
  it('source 带 demo:true → 渲染角标；相关度数字保留', () => {
    const wrapper = mountChat([
      makeMessage([
        { filename: 'rag_technology.txt', relevanceScore: 0.92, demo: true },
        { filename: 'rag_technology.txt', relevanceScore: 0.85, demo: true },
      ]),
    ])

    const badge = wrapper.find('[data-testid="demo-source-badge"]')
    expect(badge.exists()).toBe(true)
    expect(badge.text()).toBe('演示数据')
    // 数字保留（体验价值）：角标只是标注，不遮蔽来源区与相关度
    expect(wrapper.text()).toContain('来源引用：')
    expect(wrapper.text()).toContain('相关度: 92%')
  })

  it('source 不带 demo（真实检索结果）→ 不渲染角标（不得误标）', () => {
    const wrapper = mountChat([
      makeMessage([{ filename: 'real.pdf', relevanceScore: 0.9 }]),
    ])

    expect(wrapper.find('[data-testid="demo-source-badge"]').exists()).toBe(false)
  })

  it('混合来源：只要有一条 demo → 渲染角标（标注整体含演示数据）', () => {
    const wrapper = mountChat([
      makeMessage([
        { filename: 'real.pdf', relevanceScore: 0.9 },
        { filename: 'demo.txt', relevanceScore: 0.9, demo: true },
      ]),
    ])

    expect(wrapper.find('[data-testid="demo-source-badge"]').exists()).toBe(true)
  })
})
