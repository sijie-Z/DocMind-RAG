/**
 * E3（性能批）：聊天流式渲染批量化判别测试（先红后绿）。
 *
 * 现状（修前）：SSE 每个 chunk 直接写 lastMsg.content → Markdown.vue 的 computed
 * 每个 chunk 对「全量累积内容」执行一次 markdown-it 全量解析 + hljs 高亮 +
 * DOMPurify 消毒 —— 200 chunk 就是 200 次全量渲染，累计 O(n²)；侧栏列表
 * fetchConversations 也被放在 chunk 路径上（新会话首个 chunk 即拉列表）。
 *
 * 修复后：chunk 只累积，内容按帧（rAF，兜底 33ms）节流合并落屏；流式中间态
 * 走纯文本轻量渲染，终态才做一次完整 Markdown+DOMPurify；侧栏刷新移到
 * 流完成（message 终态）与切页路径。
 *
 * 度量（本文件 console.info 输出，PR 基线引用）：
 *   chunk 数 / 流式期间全量渲染次数 / 终态全量渲染次数 / 端到端耗时。
 *
 * 变异点（回退判别）：
 * - onChunk 恢复逐 chunk 写 content → 流式期间全量渲染次数 0 的断言红（回到 200）；
 * - Markdown 中间态恢复全量渲染 → 同上红 + 「中间态不出 h1」断言红；
 * - fetchConversations 放回 chunk 路径 → 侧栏刷新断言红；
 * - 去掉终态一次性落屏 → 最终内容一致性断言红。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { defineComponent, h, ref, nextTick, type Ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import DOMPurify from 'dompurify'

const { messageSpies, sseHandlers } = vi.hoisted(() => ({
  messageSpies: { error: vi.fn(), info: vi.fn(), success: vi.fn(), warning: vi.fn() },
  sseHandlers: {} as Record<string, (data: unknown) => void>,
}))

vi.mock('@/utils/sseService', () => ({
  sseService: {
    on: vi.fn((ev: string, fn: (data: unknown) => void) => { sseHandlers[ev] = fn }),
    off: vi.fn(),
    post: vi.fn().mockResolvedValue(true),
    abort: vi.fn(),
  },
}))

vi.mock('@/utils/message', () => ({
  useDedupedMessage: () => messageSpies,
}))

import { useChatSend } from '../composables/useChatSend'
import ChatMessages from '../components/ChatMessages.vue'
import type { ChatMessage } from '@/types/chat'

// DynamicScroller 为全局注册（main.ts app.use(VueVirtualScroller)），测试侧用
// 直通 stub 复现「按 items 逐项渲染 default slot」（与 chatMessageSingleRender 同款）。
const DynamicScroller = defineComponent({
  props: { items: { type: Array, default: () => [] } },
  setup(props, { slots }) {
    return () =>
      h('div', (props.items as unknown[]).map((item, index) => slots.default?.({ item, index, active: true })))
  },
})
const DynamicScrollerItem = defineComponent({
  setup(_, { slots }) {
    return () => h('div', slots.default?.())
  },
})

const i18n = createI18n({
  legacy: false,
  locale: 'zh',
  messages: {
    zh: {
      chat: {
        sourcesLabel: '来源', sourceDoc: '文档', unknownDoc: '未知文档', noPreview: '无预览',
        relevanceScore: '相关度 {score}', useful: '有用', notUseful: '没用',
        regenerate: '重新生成', backToBottom: '回到底部', welcome: '欢迎', welcomeDesc: '描述',
        demoData: '演示数据',
        analyzeFiles: '分析文件', sseError: 'SSE错误', sendFailed: '发送失败', networkError: '网络错误',
        waitForUpload: '等待上传', waitForParsing: '等待解析', removeFailedAttachments: '移除失败附件',
        attachmentsNotReady: '附件未就绪', connecting: '连接中', retrying: '重试中',
      },
    },
  },
})

let messages!: Ref<ChatMessage[]>
let isLoading!: Ref<boolean>
let fetchSpy!: ReturnType<typeof vi.fn>
let send!: () => Promise<void>
let wrapper!: VueWrapper

const Host = defineComponent({
  setup() {
    messages = ref<ChatMessage[]>([])
    isLoading = ref(false)
    fetchSpy = vi.fn(async () => {})
    const attachedFiles = ref([])
    const attachedFileIds = ref<string[]>([])
    const isRetrieving = ref(false)
    const sseStatus = ref('disconnected')
    const useSSE = ref(true)
    const useStream = ref(true)
    const { handleSSESend } = useChatSend(
      messages, attachedFiles, attachedFileIds,
      () => {}, fetchSpy as unknown as () => Promise<void>,
      isLoading, isRetrieving, sseStatus, useSSE, useStream,
    )
    send = () => handleSSESend('测试问题')
    return () => h(ChatMessages, {
      messages: messages.value,
      isLoading: isLoading.value,
      showBackToBottom: false,
      userAvatar: '',
      suggestions: [],
    })
  },
})

const wait = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))
const lastAssistant = (): ChatMessage => messages.value[messages.value.length - 1]
/** 等一帧节流落屏（rAF 在 jsdom 下按 ~16ms 走；无 rAF 时实现回退 33ms） */
const waitForFlush = () => wait(100)

let sanitizeSpy: ReturnType<typeof vi.spyOn>

beforeEach(() => {
  for (const k of Object.keys(sseHandlers)) delete sseHandlers[k]
  messageSpies.error.mockClear()
  setActivePinia(createPinia())
  sanitizeSpy = vi.spyOn(DOMPurify, 'sanitize')
  wrapper = mount(Host, {
    global: {
      plugins: [i18n],
      components: { DynamicScroller, DynamicScrollerItem },
      stubs: {
        PlanTree: true, ReflectionBanner: true, RetryHintLine: true, RunReportBlock: true,
        'n-icon': true, 'n-avatar': true, 'n-button': true, 'n-tooltip': true,
        'n-tag': true, 'n-popover': true,
      },
    },
  })
})

afterEach(() => {
  wrapper.unmount()
  vi.restoreAllMocks()
})

describe('E3 · 聊天流式渲染批量化（性能）', () => {
  it('mock 200 chunk：流式期间 0 次全量 Markdown 渲染，终态恰 1 次；内容最终一致', async () => {
    await send()
    expect(messages.value).toHaveLength(2) // user + assistant 占位

    const CHUNKS = 200
    const pieces: string[] = []
    const t0 = performance.now()
    for (let i = 0; i < CHUNKS; i++) {
      const piece = i === 0 ? '**加粗**开篇 ' : `第${i + 1}片 `
      pieces.push(piece)
      sseHandlers.chunk({ content: piece, messageId: 'm1', conversationId: 42 })
      // 每 chunk 一个渲染临界区（真实流式里 chunk 分属不同宏任务）
      await nextTick()
    }
    const elapsedStream = performance.now() - t0
    const fullRendersDuringStream = sanitizeSpy.mock.calls.length

    // 等一帧：节流器应把累积内容以轻量方式落屏一次，且不触发全量渲染
    await waitForFlush()
    const streamedContent = lastAssistant().content
    const fullRendersAfterFlush = sanitizeSpy.mock.calls.length

    const fullText = pieces.join('')
    const t1 = performance.now()
    sseHandlers.message({ content: fullText, messageId: 'm1', conversationId: 42 })
    await nextTick()
    const elapsedTotal = performance.now() - t0
    const fullRendersAtCompletion = sanitizeSpy.mock.calls.length - fullRendersAfterFlush

    // 度量基线（PR 正文引用）
    console.info(
      `[E3 perf] chunks=${CHUNKS} fullRendersDuringStream=${fullRendersDuringStream} ` +
      `fullRendersAfterFlush=${fullRendersAfterFlush} fullRendersAtCompletion=${fullRendersAtCompletion} ` +
      `elapsedStreamMs=${elapsedStream.toFixed(1)} elapsedTotalMs=${elapsedTotal.toFixed(1)} ` +
      `streamedChars=${streamedContent.length} finalizeMs=${(performance.now() - t1).toFixed(1)}`,
    )

    // 核心判别：流式期间不得对每个 chunk 做全量 Markdown+DOMPurify（修前=200）
    expect(fullRendersDuringStream).toBe(0)
    // 轻量落屏不触发 DOMPurify；终态恰一次完整渲染
    expect(fullRendersAfterFlush).toBe(0)
    expect(fullRendersAtCompletion).toBe(1)
    // 流式中间态内容已完整累积（最终一致性）
    expect(streamedContent).toBe(fullText)
    expect(lastAssistant().content).toBe(fullText)
    expect(isLoading.value).toBe(false)
    // 终态升级为完整 Markdown（加粗成真 <strong>）
    expect(wrapper.find('.markdown-body strong').exists()).toBe(true)
  })

  it('流式中间态轻量渲染（原文可见、不出 h1），终态才解析为完整 Markdown', async () => {
    await send()
    sseHandlers.chunk({ content: '# 标题', messageId: 'm1', conversationId: 1 })
    await waitForFlush()

    // 修前：chunk 直接全量渲染 → h1 立即出现（精确红点）
    expect(wrapper.find('.markdown-body h1').exists()).toBe(false)
    expect(wrapper.text()).toContain('# 标题')

    sseHandlers.message({ content: '# 标题', messageId: 'm1', conversationId: 1 })
    await nextTick()
    expect(wrapper.find('.markdown-body h1').exists()).toBe(true)
  })

  it('侧栏会话刷新不在 chunk 路径上（流完成才刷新，修前首个 chunk 即拉列表）', async () => {
    await send()
    sseHandlers.chunk({ content: 'x', messageId: 'm1', conversationId: 7 })
    sseHandlers.chunk({ content: 'y', messageId: 'm1', conversationId: 7 })
    expect(fetchSpy).not.toHaveBeenCalled() // 修前：chunk 内 fetchConversations → 红

    sseHandlers.message({ content: 'xy', messageId: 'm1', conversationId: 7 })
    expect(fetchSpy).toHaveBeenCalledTimes(1)
  })
})
