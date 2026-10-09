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

/**
 * issue #289 残余（G1/G3/G6/G7）：store 侧归约的钉桩。
 *
 * 变异点：删 stores/agent.ts thinking case 的 msg.events.push(event) → G1 用例红；
 * 删 applyPlanStepUpdate 调用或 utils/agentPlan.ts 中 retry_attempt/plan_step_status
 * 分支 → 对应用例红。
 */
describe('useAgentStore · #289 残余事件归约（thinking 落账 / 计划步骤）', () => {
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

  function seedPlanStep(store: ReturnType<typeof useAgentStore>, id = 's1') {
    store.processEvent({
      type: 'plan_step',
      plan_id: 'p1',
      plan_step_id: id,
      content: '搜索资料',
      tool_hint: 'search',
    } as any)
  }

  it('G1：thinking 事件落进 msg.events（视图思考类型标签的数据源）', () => {
    const store = seedAssistantMessage()
    store.processEvent({ type: 'thinking', content: '先规划任务', thinking_type: 'planning' } as any)

    const msg = store.messages[store.messages.length - 1]
    expect(msg.thinkingText).toContain('先规划任务')
    expect(msg.events).toHaveLength(1)
    expect(msg.events[0].type).toBe('thinking')
    expect(msg.events[0].thinking_type).toBe('planning')
  })

  it('G7：plan_step 的语义字段落账到 PlanStep（PlanTree 徽标数据源）', () => {
    const store = seedAssistantMessage()
    store.processEvent({
      type: 'plan_step',
      plan_id: 'p1',
      plan_step_id: 's1',
      content: '删除旧数据',
      tool_hint: 'delete_docs',
      parallel_group: 2,
      risk_level: 'high',
      retry_strategy: 'skip',
      fallback_tool: 'web_search',
    } as any)

    const step = store.planSteps[0]
    expect(step.parallel_group).toBe(2)
    expect(step.risk_level).toBe('high')
    expect(step.retry_strategy).toBe('skip')
    expect(step.fallback_tool).toBe('web_search')
  })

  it('G3：tool_error 的 retry_attempt 抬升 step.retry_count（PlanTree 重试徽标数据源）', () => {
    const store = seedAssistantMessage()
    seedPlanStep(store)
    store.processEvent({
      type: 'tool_error', plan_step_id: 's1', content: '失败', retry_attempt: 2,
    } as any)

    expect(store.planSteps[0].retry_count).toBe(2)
    expect(store.planSteps[0].status).toBe('failed')
  })

  it('G3：thinking/retry_hint 的 retry_attempt 同样归约（重试轮的真实进度）', () => {
    const store = seedAssistantMessage()
    seedPlanStep(store)
    store.processEvent({
      type: 'thinking', plan_step_id: 's1', content: '重试步骤', thinking_type: 'correction', retry_attempt: 1,
    } as any)
    expect(store.planSteps[0].retry_count).toBe(1)

    store.processEvent({
      type: 'retry_hint', plan_step_id: 's1', content: 'retry_action=reset_transport', retry_attempt: 3,
    } as any)
    expect(store.planSteps[0].retry_count).toBe(3)
  })

  it('G6：plan_step_status 为状态终态权威值（thinking evaluation「步骤完成」）', () => {
    const store = seedAssistantMessage()
    seedPlanStep(store)
    store.processEvent({
      type: 'thinking',
      plan_step_id: 's1',
      content: '✅ 步骤完成: 搜索资料',
      thinking_type: 'evaluation',
      plan_step_status: 'completed',
    } as any)

    expect(store.planSteps[0].status).toBe('completed')
  })

  it('G3 负例：无 retry_attempt 的事件不改变 retry_count', () => {
    const store = seedAssistantMessage()
    seedPlanStep(store)
    store.processEvent({ type: 'tool_result', plan_step_id: 's1', content: '完成' } as any)
    expect(store.planSteps[0].retry_count).toBe(0)
    expect(store.planSteps[0].status).toBe('completed')
  })
})
