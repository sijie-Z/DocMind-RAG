import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useAgentStore } from '../agent'

/**
 * issue #242 收尾：run_report / execution_context 两个事件此前**在 store 的
 * switch 里没有 case**（Agent 视图的解析器无白名单，事件其实直达 store），
 * 于是 Run Report 与 Tokens 在 UI 里永远不见——本文件钉住这两个事件的落账。
 *
 * 边界（评审核实）：本测试只覆盖 store 层；`utils/sseService.ts` 的两处白名单
 * 放行属于 Chat 视图路径的同类防线（当前无消费者、无解析级测试覆盖——已知缺口）。
 *
 * 变异点：删 stores/agent.ts 里对应的 case → 对应用例红（已实测）。
 */
describe('useAgentStore · SSE 可见性事件落账', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  function seedAssistantMessage() {
    const store = useAgentStore()
    store.addMessage({
      role: 'assistant',
      content: '',
      events: [],
      loading: true,
      currentTool: '',
      thinkingText: '',
      planSteps: [],
      planId: '',
      progress: 0,
    })
    return store
  }

  it('run_report 写入最后一条 assistant 消息（UI 的运行报告来源）', () => {
    const store = seedAssistantMessage()
    store.processEvent({ type: 'run_report', content: 'Tokens:   14176\n步骤: 2/2' } as any)
    const msg = store.messages[store.messages.length - 1]
    expect(msg.runReport).toBe('Tokens:   14176\n步骤: 2/2')
  })

  it('execution_context（对象内容）落账 total_tokens（UI 的 Tokens 徽标来源）', () => {
    const store = seedAssistantMessage()
    store.processEvent({
      type: 'execution_context',
      content: { total_tokens: 14176, steps_completed: 2 },
    } as any)
    const msg = store.messages[store.messages.length - 1]
    expect(msg.executionContext?.total_tokens).toBe(14176)
  })

  it('retry_hint 落进事件列表（#250 第 6 条：后端发、前端此前无 case）', () => {
    const store = seedAssistantMessage()
    store.processEvent({
      type: 'retry_hint',
      content: 'retry_action=reset_transport',
      plan_step_id: 's1',
      retry_attempt: 1,
    } as any)
    const msg = store.messages[store.messages.length - 1]
    expect(msg.events.length).toBe(1)
    expect(msg.events[0].type).toBe('retry_hint')
    expect(msg.events[0].content).toBe('retry_action=reset_transport')
  })

  it('未知事件类型不抛错、不影响既有消息', () => {
    const store = seedAssistantMessage()
    const before = { ...store.messages[store.messages.length - 1] }
    store.processEvent({ type: 'totally_unknown_event', content: 'x' } as any)
    const msg = store.messages[store.messages.length - 1]
    expect(msg.runReport).toBeUndefined()
    expect(msg.executionContext).toBeUndefined()
    expect(msg.content).toBe(before.content)
  })
})
