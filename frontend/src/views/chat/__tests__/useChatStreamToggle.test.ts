/**
 * W2-T7 项 4：对话「流式响应」开关真接线（第三轮终检 §2.4 假开关）。
 *
 * 现状（修前）：useStream 仅在 ChatInput 的 UI 上绑定（useChatConnection 定义），
 * 发送链路（useChatSend/WS 渲染）零消费——关掉开关回答照旧逐字上屏。
 * 修复：useStream 透传给渲染路径；关闭时 chunk 只入缓冲不逐字落屏，
 * 待 message 终态一次性落屏（= 非流式展示）；开启时为原流式行为（不回归）。
 *
 * 变异点：onChunk 忽略 useStream 直接渲染 → 「关闭态不逐字落屏」用例精确红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent, h, ref, type Ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'

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
import type { ChatMessage } from '@/types/chat'

const i18n = createI18n({
  legacy: false,
  locale: 'zh',
  messages: {
    zh: {
      chat: {
        analyzeFiles: '分析文件', sseError: 'SSE错误', sendFailed: '发送失败', networkError: '网络错误',
        waitForUpload: '等待上传', waitForParsing: '等待解析', removeFailedAttachments: '移除失败附件',
        attachmentsNotReady: '附件未就绪', connecting: '连接中', retrying: '重试中',
      },
    },
  },
})

let messages!: Ref<ChatMessage[]>
let send!: () => Promise<void>
let useStreamRef!: Ref<boolean>

const Host = defineComponent({
  setup() {
    messages = ref<ChatMessage[]>([])
    const attachedFiles = ref([])
    const attachedFileIds = ref<string[]>([])
    const isLoading = ref(false)
    const isRetrieving = ref(false)
    const sseStatus = ref('disconnected')
    const useSSE = ref(true)
    const useAgent = ref(false)
    useStreamRef = ref(true)
    const { handleSSESend } = useChatSend(
      messages, attachedFiles, attachedFileIds,
      () => {}, async () => {},
      isLoading, isRetrieving, sseStatus, useSSE, useAgent,
      useStreamRef,
    )
    send = () => handleSSESend('测试问题')
    return () => h('div')
  },
})

const lastContent = () => messages.value[messages.value.length - 1]?.content

beforeEach(() => {
  messageSpies.error.mockClear()
  for (const k of Object.keys(sseHandlers)) delete sseHandlers[k]
  setActivePinia(createPinia())
  mount(Host, { global: { plugins: [i18n] } })
})

describe('W2-T7 · 流式响应开关真接线', () => {
  it('关闭流式：chunk 不逐字落屏（缓冲），message 终态一次性落屏', async () => {
    useStreamRef.value = false
    await send()

    sseHandlers.chunk({ content: '第一段', messageId: 'm1' })
    // 修复前：chunk 直接渲染 → 此处已是 '第一段'（精确红点）
    expect(lastContent()).toBe('')

    sseHandlers.chunk({ content: '第二段', messageId: 'm1' })
    expect(lastContent()).toBe('')

    sseHandlers.message({ content: '第一段第二段', messageId: 'm1' })
    expect(lastContent()).toBe('第一段第二段')
  })

  it('开启流式（默认）：chunk 逐字落屏，行为不回归', async () => {
    useStreamRef.value = true
    await send()

    sseHandlers.chunk({ content: '第一段', messageId: 'm1' })
    expect(lastContent()).toBe('第一段')

    sseHandlers.chunk({ content: '第二段', messageId: 'm1' })
    expect(lastContent()).toBe('第一段第二段')
  })
})
