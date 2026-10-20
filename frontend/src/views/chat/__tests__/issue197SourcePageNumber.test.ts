/**
 * issue #197 前端判别测试：来源卡片展示 PDF 页码（pageNumber）。
 *
 * 真值（backend/app/services/chat_service.py build_sources_list）：
 *   #197 起来源对象新增 `pageNumber`——PDF chunk 的页码（来自解析期页偏移
 *   → document_chunks.page_number → ES 顶层 page_number → 检索透传）；
 *   非 PDF / 旧索引为 null。
 *
 * 修前：来源契约没有页码键，前端任何「第 N 页」展示都无从产出；本文件在
 * 修前基线上因 `source-page` 节点不存在而红。
 *
 * 变异点（红→绿判别）：
 *   - ChatMessages 不渲染 source.pageNumber / 键名读成 chunkIndex 等 → 第 1 个用例红；
 *   - pageNumber 为 null 时仍渲染页码（伪造）→ 第 2 个用例红（防伪造 guard，
 *     基线上无页码节点时同样绿）。
 * 四语词条的存在性由 locales/__tests__/keySnapshot.test.ts（zh 全量键面）与
 * completeness.test.ts（en 硬闸 / ja·fr 棘轮）锁定；本文件用自带 messages，
 * 不承担 locale 键存在性判别。
 */
import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createI18n } from 'vue-i18n'
import ChatMessages from '../components/ChatMessages.vue'
import type { ChatMessage } from '@/types/chat'

vi.mock('vue-virtual-scroller', async () => {
  const stubs = await import('@/test-utils/virtualScroller')
  return {
    DynamicScroller: stubs.DynamicScrollerStub,
    DynamicScrollerItem: stubs.DynamicScrollerItemStub,
  }
})

const MarkdownStub = defineComponent({
  name: 'Markdown',
  props: { content: { type: String, default: '' } },
  setup(props) {
    return () => h('div', props.content)
  },
})

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
        rankPosition: '排序 #{rank}',
        rerankScore: '重排分: {score}',
        sourcePage: '第 {page} 页',
        useful: '有用',
        notUseful: '没用',
        regenerate: '重新生成',
        backToBottom: '回到底部',
      },
    },
  },
})

function mountChat(messages: ChatMessage[]) {
  return mount(ChatMessages, {
    props: {
      messages,
      isLoading: false,
      showBackToBottom: false,
      userAvatar: '',
      suggestions: [],
    },
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

function messageWithSource(source: Record<string, unknown>): ChatMessage {
  return {
    id: 1,
    content: '回答正文',
    messageType: 'assistant',
    conversationId: 1,
    sources: [source],
  } as unknown as ChatMessage
}

describe('issue #197 · 来源卡片页码', () => {
  it('PDF 来源携带 pageNumber 时渲染「第 N 页」', () => {
    const wrapper = mountChat([
      messageWithSource({
        filename: 'policy.pdf',
        rank: 1,
        content: '正文片段',
        documentId: 'doc01',
        pageNumber: 3,
      }),
    ])
    const page = wrapper.find('[data-testid="source-page"]')
    expect(page.exists(), '来源弹层必须渲染页码节点（修前无此节点）').toBe(true)
    expect(page.text()).toContain('第 3 页')
    expect(wrapper.text()).not.toContain('chat.sourcePage')
  })

  it('非 PDF 来源 pageNumber 为 null 时不渲染页码（不伪造）', () => {
    const wrapper = mountChat([
      messageWithSource({
        filename: 'notes.txt',
        rank: 1,
        content: '正文片段',
        documentId: 'doc02',
        pageNumber: null,
      }),
    ])
    expect(wrapper.find('[data-testid="source-page"]').exists()).toBe(false)
  })
})
