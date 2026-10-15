/**
 * B32/B33 契约回归：来源弹层读点。
 *
 * 真值（backend/app/services/chat_service.py:67-78 build_sources_list）：
 * 来源对象键为 {filename, relevanceScore, content, documentId, hasKeyword,
 * hasVector, rewriteHits, freshFactor} —— 预览文本在 `content`、相关度在
 * `relevanceScore`。旧模板读 `source.snippet || source.text`（恒 undefined →
 * 弹层恒「无预览」）与 `source.score`（相关度永不显示）。
 *
 * 变异点：读点回退 snippet/text/score → 本文件红。
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

// n-popover 直通替身：同时渲染 trigger 与 default 插槽，让弹层内容进入 DOM
// （naive 真实 Popover 悬停才挂载内容到 teleport，且带 200ms 延迟）
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
        sourcesLabel: '来源',
        sourceDoc: '文档',
        unknownDoc: '未知文档',
        noPreview: '无预览',
        // 与 locales/zh.ts:299 真实口径一致
        relevanceScore: '相关度: {score}%',
        useful: '有用',
        notUseful: '没用',
        regenerate: '重新生成',
        backToBottom: '回到底部',
      },
    },
  },
})

// 真实来源形状（build_sources_list 逐键对应）；KnowledgeSource 类型旧声明含
// fileId/chunkIndex 等真实响应没有的键，此处按真实响应造 fixture 再桥接类型
const realSource = {
  filename: 'architecture.md',
  relevanceScore: 0.87,
  content: '架构文档正文片段：FastAPI + Vue3',
  documentId: 1,
  hasKeyword: true,
  hasVector: true,
  rewriteHits: 1,
  freshFactor: 1.0,
}

const message = {
  id: 1,
  content: '回答正文',
  messageType: 'assistant',
  conversationId: 1,
  sources: [realSource],
} as unknown as ChatMessage

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

describe('ChatMessages · 来源弹层读点（B32/B33）', () => {
  it('预览读真实键 content、相关度读 relevanceScore（旧键 snippet/text/score 恒缺失）', () => {
    const wrapper = mountChat([message])
    const text = wrapper.text()

    expect(text).toContain('架构文档正文片段：FastAPI + Vue3')
    expect(text).toContain('相关度: 87%')
    expect(text).not.toContain('无预览')
  })

  it('无 content 时才显示「无预览」（如实回退，不谎报）', () => {
    const emptySourceMsg = {
      id: 2,
      content: '回答正文2',
      messageType: 'assistant',
      conversationId: 1,
      sources: [{ filename: 'empty.md', relevanceScore: 0.5, documentId: 2 }],
    } as unknown as ChatMessage
    const wrapper = mountChat([emptySourceMsg])
    expect(wrapper.text()).toContain('无预览')
    expect(wrapper.text()).toContain('相关度: 50%')
  })
})
