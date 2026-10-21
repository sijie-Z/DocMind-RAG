/**
 * ISS-3：后端 warning 事件（落库失败可见 / Agent 侧降级提示，载荷 type+content）
 * 此前被 sseService 白名单静默丢弃、聊天层零订阅 → 聊天页无任何提示。修复后：
 * useChatSend 在流开始时订阅 'warning'，经 useDedupedMessage().warning 展示
 * content；content 缺失时回退 i18n 兜底（chat.sseWarning）。
 *
 * 变异点：删除注册块里的 on('warning', onWarning) → 首用例红；删除 message.warning
 * 调用或改坏 || t('chat.sseWarning') 兜底 → 双用例红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent, h, ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'

const { messageSpies, postMock, handlers } = vi.hoisted(() => ({
  messageSpies: { error: vi.fn(), info: vi.fn(), success: vi.fn(), warning: vi.fn() },
  postMock: vi.fn(),
  handlers: new Map<string, (data: { type?: string; content?: string }) => void>(),
}))

vi.mock('@/utils/sseService', () => ({
  sseService: {
    on: vi.fn((event: string, handler: (data: { type?: string; content?: string }) => void) => {
      handlers.set(event, handler)
    }),
    off: vi.fn(),
    post: postMock,
    abort: vi.fn(),
  },
}))

vi.mock('@/utils/message', () => ({
  useDedupedMessage: () => messageSpies,
}))

import { useChatSend } from '../composables/useChatSend'

const i18n = createI18n({
  legacy: false,
  locale: 'zh',
  messages: {
    zh: {
      chat: {
        analyzeFiles: '分析文件',
        sseError: 'SSE错误',
        sseWarning: 'SSE警告', // 测试用占位值；真实四语文案见 locales/{zh,en,ja,fr}.ts
        sendFailed: '发送失败',
        networkError: '网络错误',
        waitForUpload: '等待上传',
        waitForParsing: '等待解析',
        removeFailedAttachments: '移除失败附件',
        attachmentsNotReady: '附件未就绪',
        connecting: '连接中',
        retrying: '重试中',
      },
    },
  },
})

let send!: () => Promise<void>

const Host = defineComponent({
  setup() {
    const messages = ref([])
    const attachedFiles = ref([])
    const attachedFileIds = ref<string[]>([])
    const isLoading = ref(false)
    const isRetrieving = ref(false)
    const sseStatus = ref('connected')
    const useSSE = ref(true)
    const useAgent = ref(false)
    const useStream = ref(true)
    const { handleSSESend } = useChatSend(
      messages, attachedFiles, attachedFileIds,
      () => {}, async () => {},
      isLoading, isRetrieving, sseStatus, useSSE, useAgent, useStream,
    )
    send = () => handleSSESend('测试问题')
    return () => h('div')
  },
})

beforeEach(() => {
  handlers.clear()
  messageSpies.warning.mockClear()
  messageSpies.error.mockClear()
  setActivePinia(createPinia())
  mount(Host, { global: { plugins: [i18n] } })
})

describe('useChatSend · warning 事件 → toast（ISS-3）', () => {
  it('流开始时订阅 warning；事件抵达时以 content 弹 warning toast', async () => {
    postMock.mockResolvedValueOnce(true)
    await send()

    expect(handlers.has('warning')).toBe(true) // 修复前：无订阅 → 红
    handlers.get('warning')!({ type: 'warning', content: '回答已生成，但会话记录保存失败，刷新后本轮对话可能不可见' })

    expect(messageSpies.warning).toHaveBeenCalledWith('回答已生成，但会话记录保存失败，刷新后本轮对话可能不可见')
    expect(messageSpies.error).not.toHaveBeenCalled() // warning 不是 error，不得误报
  })

  it('content 缺失时回退到 i18n 兜底文案（chat.sseWarning）', async () => {
    postMock.mockResolvedValueOnce(true)
    await send()

    handlers.get('warning')!({ type: 'warning' })

    expect(messageSpies.warning).toHaveBeenCalledWith('SSE警告')
  })
})
