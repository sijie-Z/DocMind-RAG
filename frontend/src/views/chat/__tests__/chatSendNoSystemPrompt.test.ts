/**
 * B3 · 聊天 activeSystemPrompt 死通道清理（任务书「四、B 批」B3）。
 *
 * 现状（修前）：useChatSend 从 localStorage 读 activeSystemPrompt 并塞进
 * SSE 请求 payload.systemPrompt；后端 chat_stream_endpoint / WS 处理器只消费
 * payload.strictMode / privacyMode / enableThinking，systemPrompt 从未被读取
 * ——死通道，应用提示词对回答零影响却让用户误以为已生效。
 *
 * 修复：前端不再发送 payload.systemPrompt（localStorage 的提示词徽标属另一
 * 功能，不在本用例断言范围内）。
 *
 * 变异点：把 systemPrompt 重新塞回 payload → 本用例红。
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
      },
    },
  },
})

let send!: () => Promise<void>

const Host = defineComponent({
  setup() {
    const messages = ref<ChatMessage[]>([])
    const attachedFiles = ref([])
    const attachedFileIds = ref<string[]>([])
    const isLoading = ref(false)
    const isRetrieving = ref(false)
    const sseStatus = ref('disconnected')
    const useSSE = ref(true)
    const useAgent = ref(false)
    const useStream = ref(true)
    const { handleSSESend } = useChatSend(
      messages, attachedFiles, attachedFileIds,
      () => {}, async () => {},
      isLoading, isRetrieving, sseStatus, useSSE, useAgent,
      useStream,
    )
    send = () => handleSSESend('测试问题')
    return () => h('div')
  },
})

beforeEach(() => {
  messageSpies.error.mockClear()
  for (const k of Object.keys(sseHandlers)) delete sseHandlers[k]
  localStorage.clear()
  vi.mocked(sseService.post).mockClear()
  setActivePinia(createPinia())
  mount(Host, { global: { plugins: [i18n] } })
})

describe('B3 · 聊天不再发送 payload.systemPrompt（activeSystemPrompt 死通道）', () => {
  it('localStorage 有 activeSystemPrompt 时，SSE 请求体也不得再带 systemPrompt 字段', async () => {
    localStorage.setItem('activeSystemPrompt', '请以严谨风格回答')

    await send()

    const postMock = vi.mocked(sseService.post)
    expect(postMock).toHaveBeenCalledTimes(1)
    const body = postMock.mock.calls[0][1] as Record<string, unknown>

    // 仍然保留既有开关字段（行为不回归）
    expect(body).toHaveProperty('payload')
    const payload = body.payload as Record<string, unknown>
    expect(payload).toHaveProperty('strictMode')
    expect(payload).toHaveProperty('privacyMode')

    // 修前此处为 '请以严谨风格回答'（死字段）；修后必须不存在该键
    expect(payload, `payload 不得再含 systemPrompt: ${JSON.stringify(payload)}`)
      .not.toHaveProperty('systemPrompt')
  })
})
