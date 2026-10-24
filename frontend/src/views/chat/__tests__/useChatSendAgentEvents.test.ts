/**
 * issue #508（方案 B）：Chat 不再承载深度分析（Agent/PER）。
 *
 * 本文件原为 issue #289 的「聊天页 Agent 7 型事件消费者」正例；方案 B 后
 * 语义反转——深度分析强制走 /agent 工作台，Chat 对 Agent 事件**零订阅**，
 * 以免出现同一 run 在两个入口 UI 不同构的「半套状态」（审批/计划审批/取消
 * 事件在 Chat 既无卡片也无恢复入口）。
 *
 * 钉住：
 *  1) Agent-only 事件类型在 Chat 发送链路里没有任何 handler；
 *  2) 普通 RAG 需要的 handler（chunk/message/error/warning/debug + 客户端
 *     retry）全部保留——RAG SSE 行为不回归；
 *  3) 发送请求体不再携带 useAgent（旧客户端形态由后端守卫显式拒绝）。
 *
 * 全量「EventType - Chat handlers」差异的显式豁免表见 chatEventCoverage.test.ts。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent, h, ref, type Ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'

const { handlers, messageSpies, ssePost } = vi.hoisted(() => ({
  handlers: new Map<string, (data: unknown) => void>(),
  messageSpies: { error: vi.fn(), info: vi.fn(), success: vi.fn(), warning: vi.fn() },
  ssePost: vi.fn().mockResolvedValue(true),
}))

vi.mock('@/utils/sseService', () => ({
  sseService: {
    on: vi.fn((event: string, handler: (data: unknown) => void) => {
      handlers.set(event, handler)
    }),
    off: vi.fn((event: string) => {
      handlers.delete(event)
    }),
    post: ssePost,
    abort: vi.fn(),
  },
}))

vi.mock('@/utils/message', () => ({
  useDedupedMessage: () => messageSpies,
}))

import { useChatSend } from '../composables/useChatSend'
import type { ChatMessage, AttachedFile } from '@/types/chat'

const i18n = createI18n({
  legacy: false,
  locale: 'zh',
  messages: {
    zh: {
      chat: {
        analyzeFiles: '分析文件',
        sseError: 'SSE错误',
        sendFailed: '发送失败',
        networkError: '网络错误',
        waitForUpload: '等待上传',
        waitForParsing: '等待解析',
        removeFailedAttachments: '移除失败附件',
        attachmentsNotReady: '附件未就绪',
        connecting: '连接中',
        retrying: '服务繁忙，正在自动重试（第 {attempt}/{maxRetries} 次）...',
      },
    },
  },
})

let messages!: Ref<ChatMessage[]>
let send!: () => Promise<void>

const Host = defineComponent({
  setup() {
    messages = ref<ChatMessage[]>([])
    const attachedFiles = ref<AttachedFile[]>([])
    const attachedFileIds = ref<string[]>([])
    const isLoading = ref(false)
    const isRetrieving = ref(false)
    const sseStatus = ref('disconnected')
    const useSSE = ref(true)
    const debugEnabled = ref(false)
    const debugData = ref(null)
    const { handleSSESend } = useChatSend(
      messages, attachedFiles, attachedFileIds,
      () => {}, async () => {},
      isLoading, isRetrieving, sseStatus, useSSE, ref(true), debugEnabled, debugData,
    )
    send = () => handleSSESend('测试问题')
    return () => h('div')
  },
})

async function setup() {
  handlers.clear()
  messageSpies.info.mockClear()
  messageSpies.error.mockClear()
  ssePost.mockClear()
  setActivePinia(createPinia())
  mount(Host, { global: { plugins: [i18n] } })
  await send()
}

describe('useChatSend · 深度分析迁至 /agent 后的 Chat 事件面（issue #508）', () => {
  beforeEach(async () => {
    await setup()
  })

  it('Agent-only 事件在 Chat 中没有任何 handler（半套状态已移除）', () => {
    for (const type of [
      'thinking', 'tool_call', 'tool_result', 'tool_error',
      'plan_start', 'plan_step', 'plan_complete', 'reflection',
      'run_report', 'execution_context', 'retry_hint',
      'approval_required', 'plan_approval_required', 'run_cancelled',
      'steer_applied', 'command_start', 'command_result', 'command_error',
      'hook_error', 'execution_step_result', 'done',
    ]) {
      expect(handlers.has(type), `'${type}' 不应在 Chat 注册 handler`).toBe(false)
    }
  })

  it('普通 RAG 聊天的 handler 全部保留（事件面不回归）', () => {
    for (const type of ['chunk', 'message', 'error', 'warning', 'debug', 'retry']) {
      expect(handlers.get(type), `'${type}' handler`).toBeTypeOf('function')
    }
  })

  it('发送请求体不再携带 useAgent（深度分析改由 /agent 承担）', () => {
    expect(ssePost).toHaveBeenCalledTimes(1)
    const [path, payload] = ssePost.mock.calls[0]
    expect(path).toBe('stream')
    expect('useAgent' in (payload as Record<string, unknown>)).toBe(false)
  })

  it("G4：客户端 'retry' 退避事件 → 提示自动重试进度（行为不回归）", () => {
    handlers.get('retry')!({ attempt: 1, maxRetries: 3, waitTime: 1000 })
    expect(messageSpies.info).toHaveBeenCalledTimes(1)
    expect(messageSpies.info.mock.calls[0][0]).toContain('1/3')
  })

  it('RAG 事件仍正常落账（chunk → message 终态）', () => {
    handlers.get('chunk')!({ type: 'chunk', content: '第一段' })
    handlers.get('message')!({ type: 'message', content: '第一段（终态）' })
    const last = messages.value[messages.value.length - 1]
    expect(last.messageType).toBe('assistant')
    expect(last.content).toBe('第一段（终态）')
    // Agent 事件台账字段永不被写入
    expect(last.events).toBeUndefined()
    expect(last.runReport).toBeUndefined()
    expect(last.planSteps).toBeUndefined()
  })
})
