/**
 * 重新生成（原地替换）前端契约。
 *
 * 修前：handleRegenerate 只删本地 assistant 再走普通发送——后端插入重复 user
 * 行、旧答案仍落库，重开会话出现 Q, A1, Q, A2。
 *
 * 修后：不追加 user 气泡；请求带 payload.regenerate；成功才移除旧答案气泡，
 * 失败恢复旧答案并移除新占位气泡。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
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

vi.mock('@/utils/message', () => ({ useDedupedMessage: () => messageSpies }))

import { sseService } from '@/utils/sseService'
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
        regenerateNeedsReload: '该回答尚未保存，请刷新会话后再重新生成',
      },
    },
  },
})

let messages!: Ref<ChatMessage[]>
let regenerate!: (msg: ChatMessage) => Promise<void>
let stop!: () => void

const Host = defineComponent({
  setup() {
    messages = ref<ChatMessage[]>([
      { id: 1, content: '本轮问题', messageType: 'user', conversationId: 7 },
      {
        id: 2,
        content: '旧答案',
        messageType: 'assistant',
        conversationId: 7,
        sources: [{ filename: 'a.md', relevanceScore: 1, content: '旧来源' }],
        isCached: true,
      },
    ])
    const attachedFiles = ref([])
    const attachedFileIds = ref<string[]>([])
    const isLoading = ref(false)
    const isRetrieving = ref(false)
    const sseStatus = ref('disconnected')
    const useSSE = ref(true)
    const useStream = ref(true)
    const { regenerateMessage, stopGeneration } = useChatSend(
      messages, attachedFiles, attachedFileIds,
      () => {}, async () => {},
      isLoading, isRetrieving, sseStatus, useSSE, useStream,
    )
    regenerate = regenerateMessage
    stop = stopGeneration
    return () => h('div')
  },
})

beforeEach(() => {
  messageSpies.error.mockClear()
  for (const k of Object.keys(sseHandlers)) delete sseHandlers[k]
  vi.mocked(sseService.post).mockClear()
  setActivePinia(createPinia())
  mount(Host, { global: { plugins: [i18n] } })
})

describe('重新生成 · 原地替换', () => {
  it('不追加 user 气泡；请求带 regenerate；成功后才移除旧答案', async () => {
    let resolvePost!: (value: boolean) => void
    vi.mocked(sseService.post).mockReturnValueOnce(
      new Promise<boolean>(resolve => { resolvePost = resolve }) as never
    )

    const pending = regenerate(messages.value[1])
    await flushPromises()

    const body = vi.mocked(sseService.post).mock.calls[0][1] as Record<string, unknown>
    expect(body.content).toBe('本轮问题')
    expect((body.payload as Record<string, unknown>).regenerate).toBe(true)
    expect(messages.value.filter(m => m.messageType === 'user')).toHaveLength(1)
    // 流式期间旧答案隐藏、新占位在最后
    expect(messages.value.find(m => m.id === 2)?.content).toBe('')
    expect(messages.value).toHaveLength(3)

    sseHandlers.chunk?.({ content: '新', conversationId: 7, messageId: 'm-a' })
    sseHandlers.message?.({ content: '新答案', conversationId: 7, messageId: 'm-a' })
    resolvePost(true)
    await pending
    await flushPromises()

    expect(messages.value.filter(m => m.messageType === 'user')).toHaveLength(1)
    expect(messages.value.some(m => m.id === 2)).toBe(false)
    const assistants = messages.value.filter(m => m.messageType === 'assistant')
    expect(assistants).toHaveLength(1)
    expect(assistants[0].content).toBe('新答案')
    // 采用服务端 id，点赞/点踩不再拿本地 sse-* id 打 404。
    expect(assistants[0].id).toBe('m-a')
  })

  it('失败：新占位移除、旧答案原样恢复、明确报错', async () => {
    let resolvePost!: (value: boolean) => void
    vi.mocked(sseService.post).mockReturnValueOnce(
      new Promise<boolean>(resolve => { resolvePost = resolve }) as never
    )

    const pending = regenerate(messages.value[1])
    await flushPromises()

    sseHandlers.error?.({ content: 'boom' })
    resolvePost(true)
    await pending
    await flushPromises()

    expect(messageSpies.error).toHaveBeenCalled()
    const old = messages.value.find(m => m.id === 2)
    expect(old?.content).toBe('旧答案')
    expect(old?.sources).toEqual([{ filename: 'a.md', relevanceScore: 1, content: '旧来源' }])
    expect(old?.isCached).toBe(true)
    expect(messages.value.filter(m => m.messageType === 'assistant')).toHaveLength(1)
    expect(messages.value).toHaveLength(2)
  })

  it('停止生成：恢复旧答案，移除被中止的占位气泡', async () => {
    let resolvePost!: (value: boolean) => void
    vi.mocked(sseService.post).mockReturnValueOnce(
      new Promise<boolean>(resolve => { resolvePost = resolve }) as never
    )

    const pending = regenerate(messages.value[1])
    await flushPromises()
    sseHandlers.chunk?.({ content: '半截', conversationId: 7, messageId: 'm-a' })
    stop()
    // 停止当下就必须恢复（不能靠之后的 EOF 分支兜底）。
    expect(messages.value.find(m => m.id === 2)?.content).toBe('旧答案')
    expect(messages.value).toHaveLength(2)
    resolvePost(true)
    await pending
    await flushPromises()

    expect(messages.value.find(m => m.id === 2)?.content).toBe('旧答案')
    expect(messages.value).toHaveLength(2)
    expect(messages.value.filter(m => m.messageType === 'assistant')).toHaveLength(1)
  })

  it('停止后旧流以 post=false 迟到：不得误报发送失败或清理新流', async () => {
    let resolvePost!: (value: boolean) => void
    vi.mocked(sseService.post).mockReturnValueOnce(
      new Promise<boolean>(resolve => { resolvePost = resolve }) as never
    )

    const pending = regenerate(messages.value[1])
    await flushPromises()
    stop()
    resolvePost(false)
    await pending
    await flushPromises()

    expect(messageSpies.error).not.toHaveBeenCalled()
    expect(messages.value.find(m => m.id === 2)?.content).toBe('旧答案')
    expect(messages.value).toHaveLength(2)
  })

  it('流干净结束但没有终态帧：恢复旧答案并结束加载态', async () => {
    let resolvePost!: (value: boolean) => void
    vi.mocked(sseService.post).mockReturnValueOnce(
      new Promise<boolean>(resolve => { resolvePost = resolve }) as never
    )

    const pending = regenerate(messages.value[1])
    await flushPromises()
    sseHandlers.chunk?.({ content: '半截', conversationId: 7, messageId: 'm-a' })
    resolvePost(true)
    await pending
    await flushPromises()

    expect(messages.value.find(m => m.id === 2)?.content).toBe('旧答案')
    expect(messages.value).toHaveLength(2)
  })

  it('未落库的 sse-* 占位：拒绝重生成并提示刷新（不重写更早一轮）', async () => {
    const placeholder: ChatMessage = {
      id: 'sse-99',
      content: '',
      messageType: 'assistant',
      conversationId: 7,
    }
    messages.value.push(placeholder)

    await regenerate(placeholder)

    expect(vi.mocked(sseService.post)).not.toHaveBeenCalled()
    expect(messageSpies.warning).toHaveBeenCalled()
    expect(messages.value).toHaveLength(3)
  })

  it('发送失败（post=false）：恢复旧答案，不留下空占位', async () => {
    vi.mocked(sseService.post).mockResolvedValueOnce(false)

    await regenerate(messages.value[1])
    await flushPromises()

    expect(messages.value.find(m => m.id === 2)?.content).toBe('旧答案')
    expect(messages.value).toHaveLength(2)
    expect(messageSpies.error).toHaveBeenCalled()
  })
})
