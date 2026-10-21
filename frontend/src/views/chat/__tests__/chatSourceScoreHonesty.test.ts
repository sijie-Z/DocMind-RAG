/**
 * issue #185 契约：来源分数展示必须诚实、可证伪。
 *
 * 修前：`ChatMessages.vue` 把 `relevanceScore`（后端当时是批内 min-max 相对分，
 * 榜首恒 1.0）按 `×100` 渲染成「相关度 100%」——同一批文本无论绝对质量如何，
 * 榜首都声称 100%，无法证伪。
 *
 * 修后契约（backend `build_sources_list`）：
 * - `relevanceScore` 是重排器绝对分（local CE logit / zhipu relevance_score），
 *   仅当来源带 `rank` 标记（新契约数据）时显示为「重排分: X.XX」，绝不 ×100；
 * - 无绝对分（null / 旧消息无 rank）时只显示「排序 #N」；
 * - `rankScore` 只是相对排序分，不得作为相关度展示。
 *
 * 变异点：恢复 `relevanceScore * 100` 展示、或把 rankScore 当 relevance、
 * 或在无绝对分时隐藏序位 → 对应用例红。
 */
import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createI18n } from 'vue-i18n'
import ChatMessages from '../components/ChatMessages.vue'
import type { ChatMessage } from '@/types/chat'

vi.mock('vue-virtual-scroller', async () => {
  const stubs = await import('@/test-utils/virtualScroller')
  return { DynamicScroller: stubs.DynamicScrollerStub, DynamicScrollerItem: stubs.DynamicScrollerItemStub }
})

const MarkdownStub = defineComponent({
  name: 'Markdown',
  props: { content: { type: String, default: '' } },
  setup(props) {
    return () => h('div', props.content)
  },
})

// 弹层直通替身：让来源弹层内容进入 DOM 供断言
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
        rankPosition: '排序 #{rank}',
        rerankScore: '重排分: {score}',
        useful: '有用',
        notUseful: '没用',
        regenerate: '重新生成',
        backToBottom: '回到底部',
      },
    },
  },
})

function mountChat(sources: Record<string, unknown>[]) {
  const message = {
    id: 1,
    content: '回答正文',
    messageType: 'assistant',
    conversationId: 1,
    sources,
  } as unknown as ChatMessage
  return mount(ChatMessages, {
    props: { messages: [message], isLoading: false, showBackToBottom: false, userAvatar: '', suggestions: [] },
    global: {
      plugins: [i18n],
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

describe('ChatMessages · 来源分数诚实口径（issue #185）', () => {
  it('重排绝对分展示为「重排分」，绝不换算成百分比相关度', () => {
    const wrapper = mountChat([
      { filename: 'a.md', relevanceScore: -1.25, rankScore: 1.0, rank: 1, content: '正文' },
    ])
    const text = wrapper.text()

    expect(text).toContain('重排分: -1.25')
    expect(text).not.toContain('%')
    expect(text).not.toContain('相关度')
  })

  it('无绝对分（降级/未重排）时只展示排序序位，不得显示 100%', () => {
    const wrapper = mountChat([
      { filename: 'b.md', relevanceScore: null, rankScore: 1.0, rank: 2, content: '正文' },
    ])
    const text = wrapper.text()

    expect(text).toContain('排序 #2')
    expect(text).not.toContain('%')
    expect(text).not.toContain('相关度')
  })

  it('旧消息（无 rank 标记、relevanceScore 是历史 rank 分）按序位回退，不冒充重排分', () => {
    const wrapper = mountChat([
      { filename: 'c.md', relevanceScore: 0.87, content: '正文' },
    ])
    const text = wrapper.text()

    expect(text).toContain('排序 #1')
    expect(text).not.toContain('87')
    expect(text).not.toContain('%')
  })
})
