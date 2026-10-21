/**
 * issue #289 主项：聊天页 Agent 模式 7 型事件"传输放行但零消费者"。
 *
 * 背景（recon_frontend_events.md 流 A2）：run_report / execution_context /
 * retry_hint / plan_start / plan_step / plan_complete / reflection 经
 * sseService 白名单放行后，useChatSend 从未注册 handler —— 链路止于传输层，
 * 同一 run 在聊天页与 Agent 页看到的 UI 不同构。本文件钉住 handler 的注册
 * 与落账（渲染侧见 chatAgentExtrasRendering.test.ts）。
 *
 * 另有 G4：客户端 'retry' 退避事件（429/503）此前只 off 未 on。
 *
 * 变异点：删任一 `sseService.on('xxx', ...)` 注册 → 对应用例红（fire 时
 * 注册断言先红）；删 handler 内落账 → 对应用例红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent, h, ref, type Ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'

const { handlers, messageSpies } = vi.hoisted(() => ({
  handlers: new Map<string, (data: unknown) => void>(),
  messageSpies: { error: vi.fn(), info: vi.fn(), success: vi.fn(), warning: vi.fn() },
}))

vi.mock('@/utils/sseService', () => ({
  sseService: {
    on: vi.fn((event: string, handler: (data: unknown) => void) => {
      handlers.set(event, handler)
    }),
    off: vi.fn((event: string) => {
      handlers.delete(event)
    }),
    post: vi.fn().mockResolvedValue(true),
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
    const useAgent = ref(true)
    const debugEnabled = ref(false)
    const debugData = ref(null)
    const { handleSSESend } = useChatSend(
      messages, attachedFiles, attachedFileIds,
      () => {}, async () => {},
      isLoading, isRetrieving, sseStatus, useSSE, useAgent, ref(true), debugEnabled, debugData,
    )
    send = () => handleSSESend('测试问题')
    return () => h('div')
  },
})

async function setup() {
  handlers.clear()
  messageSpies.info.mockClear()
  messageSpies.error.mockClear()
  setActivePinia(createPinia())
  mount(Host, { global: { plugins: [i18n] } })
  await send()
}

function lastAssistant(): ChatMessage {
  const msg = messages.value[messages.value.length - 1]
  expect(msg.messageType).toBe('assistant')
  return msg
}

function fire(event: string, data: unknown) {
  const handler = handlers.get(event)
  expect(handler, `handler for '${event}' should be registered`).toBeTypeOf('function')
  handler!(data)
}

describe('useChatSend · Agent 模式残余事件（issue #289 主项）', () => {
  beforeEach(async () => {
    await setup()
  })

  it('7 型事件 + 客户端 retry 的 handler 全部注册（此前白名单放行、零消费者）', () => {
    for (const type of [
      'run_report', 'execution_context', 'retry_hint',
      'plan_start', 'plan_step', 'plan_complete', 'reflection',
      'retry',
    ]) {
      expect(handlers.get(type), `'${type}' handler`).toBeTypeOf('function')
    }
  })

  it('run_report → 落进 msg.runReport（折叠块数据源）', () => {
    expect(lastAssistant().runReport).toBeUndefined()
    fire('run_report', { type: 'run_report', content: 'Tokens:   14176\n步骤: 2/2' })
    expect(lastAssistant().runReport).toBe('Tokens:   14176\n步骤: 2/2')
  })

  it('execution_context → 落进 msg.executionContext（Tokens 徽标数据源）', () => {
    fire('execution_context', { type: 'execution_context', content: { total_tokens: 14176 } })
    expect(lastAssistant().executionContext?.total_tokens).toBe(14176)
  })

  it('retry_hint → 落进 msg.events（渲染层 RetryHintLine 数据源）', () => {
    fire('retry_hint', { type: 'retry_hint', content: 'retry_action=reset_transport' })
    const events = lastAssistant().events || []
    expect(events.some((e) => e.type === 'retry_hint' && e.content === 'retry_action=reset_transport')).toBe(true)
  })

  it('plan_start/plan_step/plan_complete → planSteps 重建 + 事件台账', () => {
    fire('plan_start', { type: 'plan_start', planId: 'p1', content: '开始规划' })
    fire('plan_step', { type: 'plan_step', planId: 'p1', planStepId: 's1', content: '搜索资料', toolHint: 'search' })
    fire('plan_step', { type: 'plan_step', planId: 'p1', planStepId: 's2', content: '整理结果' })
    fire('plan_complete', { type: 'plan_complete', planId: 'p1', content: '计划完成' })

    const msg = lastAssistant()
    expect(msg.planSteps).toHaveLength(2)
    expect(msg.planSteps![0]).toMatchObject({
      id: 's1', description: '搜索资料', tool_hint: 'search', status: 'pending', retry_count: 0,
    })
    const types = (msg.events || []).map((e) => e.type)
    expect(types).toEqual(['plan_start', 'plan_step', 'plan_step', 'plan_complete'])
  })

  it('tool_call / tool_result 推进计划步骤状态与进度（与 Agent 页 store 同款归约）', () => {
    fire('plan_start', { type: 'plan_start', planId: 'p1', content: '' })
    fire('plan_step', { type: 'plan_step', planId: 'p1', planStepId: 's1', content: '搜索资料' })
    fire('tool_call', { type: 'tool_call', toolName: 'search', planStepId: 's1' })
    expect(lastAssistant().planSteps![0].status).toBe('running')

    fire('tool_result', { type: 'tool_result', toolName: 'search', planStepId: 's1', planProgress: 0.5 })
    expect(lastAssistant().planSteps![0].status).toBe('completed')
    expect(lastAssistant().planProgress).toBe(0.5)
  })

  it('reflection → 落进 msg.events（带 reflection_result）', () => {
    fire('reflection', { type: 'reflection', content: '结果需要重试', result: 'retry' })
    const events = lastAssistant().events || []
    const refl = events.find((e) => e.type === 'reflection')
    expect(refl).toBeTruthy()
    expect(refl!.content).toBe('结果需要重试')
    expect(refl!.reflection_result).toBe('retry')
  })

  it("G4：客户端 'retry' 退避事件 → 提示自动重试进度（此前只 off 未 on、退避期无任何提示）", () => {
    fire('retry', { attempt: 1, maxRetries: 3, waitTime: 1000 })
    expect(messageSpies.info).toHaveBeenCalledTimes(1)
    expect(messageSpies.info.mock.calls[0][0]).toContain('1/3')
  })

  it('负例：未触发事件前 7 型字段为空（handler 不是恒写）', () => {
    const msg = lastAssistant()
    expect(msg.runReport).toBeUndefined()
    expect(msg.executionContext).toBeUndefined()
    expect(msg.planSteps).toBeUndefined()
    expect(msg.events).toBeUndefined()
  })
})
