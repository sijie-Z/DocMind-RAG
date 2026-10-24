/**
 * issue #508 · 静态覆盖度契约：`set(EventType) - set(Chat handlers)` 必须等于
 * 一张**显式豁免表**，每一项都写明理由。
 *
 * 背景（issue #508）：深度分析（Agent/PER）在 Chat 内既缺审批卡也缺恢复入口，
 * 只补前端事件白名单是假修复；方案 B 把深度分析整体迁到 /agent 工作台，Chat
 * 因此对 Agent 事件零订阅。本测试防止两类回归：
 *  1) 有人重新在 Chat 注册半套 Agent handler（diff 变小 → 红）；
 *  2) 后端新增事件类型（经 types/agent.ts 同步）后，在 Chat 侧无声消失
 *     （diff 出现未豁免键 → 红）。尤其 approval / plan_approval / run_cancelled
 *     必须在豁免表里有明确去向（/agent），不允许「谁都没处理」。
 *
 * 变异性：删/改任一豁免键 → 第一用例红；把任意 Agent 事件加回 useChatSend 的
 * `sseService.on` → 第一用例红；新增 AGENT_EVENT_TYPES 条目但不登记豁免 → 红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent, h, ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'

import { AGENT_EVENT_TYPES } from '@/types/agent'

const { handlers, ssePost, messageSpies, onEventMock } = vi.hoisted(() => ({
  handlers: new Map<string, (data: unknown) => void>(),
  ssePost: vi.fn().mockResolvedValue(true),
  messageSpies: { error: vi.fn(), info: vi.fn(), success: vi.fn(), warning: vi.fn() },
  onEventMock: vi.fn(),
}))

vi.mock('@/utils/sseService', () => ({
  sseService: {
    on: vi.fn((event: string, handler: (data: unknown) => void) => {
      handlers.set(event, handler)
    }),
    off: vi.fn((event: string) => {
      handlers.delete(event)
    }),
    onEvent: onEventMock,
    post: ssePost,
    abort: vi.fn(),
  },
}))

vi.mock('@/utils/message', () => ({ useDedupedMessage: () => messageSpies }))

import { useChatSend } from '../composables/useChatSend'

const i18n = createI18n({
  legacy: false,
  locale: 'zh',
  messages: {
    zh: {
      chat: {
        analyzeFiles: '分析文件', sseError: 'SSE错误', sseWarning: 'SSE警告',
        sendFailed: '发送失败', networkError: '网络错误', waitForUpload: '等待上传',
        waitForParsing: '等待解析', removeFailedAttachments: '移除失败附件',
        attachmentsNotReady: '附件未就绪', connecting: '连接中', retrying: '重试中',
      },
    },
  },
})

/**
 * 豁免表：Chat **有意不消费**的 Agent 事件 → 实际消费方与理由。
 * 新增 Agent 事件类型时必须在此登记；审批类事件不得删除（见第三用例）。
 */
const CHAT_AGENT_EVENT_EXEMPTIONS: Record<string, string> = {
  // ── 计划/工具/推理：仅 /agent 工作台的时间线与计划树渲染 ──
  thinking: 'Agent 推理流只在 /agent 时间线渲染；Chat 无深度分析模式',
  plan_start: '计划树只在 /agent 工作台渲染（Chat 无计划 UI）',
  plan_step: '计划树只在 /agent 工作台渲染（Chat 无计划 UI）',
  plan_complete: '计划树只在 /agent 工作台渲染（Chat 无计划 UI）',
  tool_call: '工具时间线只在 /agent 工作台渲染；Chat 不再伪造工具 stamp',
  tool_result: '工具时间线只在 /agent 工作台渲染；Chat 不再伪造工具 stamp',
  tool_error: '工具时间线只在 /agent 工作台渲染；Chat 不再伪造工具 stamp',
  reflection: '反思结果只在 /agent 工作台事件台账展示',
  run_report: '运行报告只在 /agent 工作台展示',
  execution_context: '运行上下文徽标只在 /agent 工作台展示',
  execution_step_result: '后端已发射；/agent 暂未渲染（既有缺口，另行跟进）；Chat 已无 Agent 模式',
  retry_hint: '重试提示只在 /agent 工作台事件台账展示',
  done: 'Agent 终态事件；Chat 的 RAG 终态是 message（非 done）',
  // ── HITL / 控制面：Chat 没有审批卡与恢复入口，统一由 /agent 承担 ──
  approval_required: '审批卡只在 /agent 工作台提供（Chat 无审批 UI，issue #508 方案 B）',
  plan_approval_required: '计划审批卡只在 /agent 工作台提供（Chat 无审批 UI）',
  run_cancelled: '取消收敛与恢复只在 /agent 工作台处理',
  steer_applied: '运行中转向只在 /agent 工作台支持',
  command_start: '用户命令只在 /agent 工作台执行与展示',
  command_result: '用户命令只在 /agent 工作台执行与展示',
  command_error: '用户命令只在 /agent 工作台执行与展示',
  hook_error: '后端已发射；/agent 暂未渲染（既有缺口，另行跟进）；Chat 已无 Agent 模式',
}

let registered: Set<string>

const Host = defineComponent({
  setup() {
    const messages = ref([])
    const attachedFiles = ref([])
    const attachedFileIds = ref<string[]>([])
    const isLoading = ref(false)
    const isRetrieving = ref(false)
    const sseStatus = ref('disconnected')
    const useSSE = ref(true)
    const { handleSSESend } = useChatSend(
      messages, attachedFiles, attachedFileIds,
      () => {}, async () => {},
      isLoading, isRetrieving, sseStatus, useSSE, ref(true),
    )
    void handleSSESend('覆盖度检查')
    return () => h('div')
  },
})

beforeEach(async () => {
  handlers.clear()
  ssePost.mockClear()
  onEventMock.mockClear()
  setActivePinia(createPinia())
  mount(Host, { global: { plugins: [i18n] } })
  await new Promise((resolve) => setTimeout(resolve, 0))
  registered = new Set(handlers.keys())
})

describe('issue #508 · EventType - Chat handlers 的显式豁免表', () => {
  it('剩余差异与豁免表逐项一致（无未登记的无声丢失）', () => {
    const missing = AGENT_EVENT_TYPES.filter((type) => !registered.has(type))
    expect([...missing].sort()).toEqual(Object.keys(CHAT_AGENT_EVENT_EXEMPTIONS).sort())
  })

  it('每条豁免都必须给出消费方与理由（不允许空泛「忽略」）', () => {
    for (const [type, reason] of Object.entries(CHAT_AGENT_EVENT_EXEMPTIONS)) {
      expect(reason.trim().length, `'${type}' 缺少豁免理由`).toBeGreaterThan(8)
    }
  })

  it('审批/计划审批/取消类事件必须在豁免表且有明确去向（不得无声丢失）', () => {
    for (const type of ['approval_required', 'plan_approval_required', 'run_cancelled']) {
      expect(AGENT_EVENT_TYPES as readonly string[]).toContain(type)
      expect(CHAT_AGENT_EVENT_EXEMPTIONS[type], `'${type}' 未登记豁免`).toBeTruthy()
      expect(CHAT_AGENT_EVENT_EXEMPTIONS[type]).toContain('/agent')
      expect(registered.has(type), `'${type}' 不应在 Chat 注册 handler`).toBe(false)
    }
  })

  it('Chat 只注册 RAG 事件（chunk/message/error/warning/debug/客户端 retry）', () => {
    expect([...registered].sort()).toEqual(
      ['chunk', 'debug', 'error', 'message', 'retry', 'warning'].sort(),
    )
  })

  it('不通过 onEvent 透传通道订阅任何 Agent 事件（#480 未知事件入口同样零订阅）', () => {
    expect(onEventMock).not.toHaveBeenCalled()
  })
})
