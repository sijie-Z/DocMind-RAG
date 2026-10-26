import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useAgentStore } from '../agent'

function seedAssistant() {
  const store = useAgentStore()
  store.addMessage({
    role: 'assistant',
    content: 'authoritative-',
    events: [],
    loading: true,
    currentTool: '',
    thinkingText: 'existing-thinking',
    planSteps: [],
    planId: '',
    progress: 0,
  })
  store.processEvent({
    type: 'plan_step',
    plan_id: 'p1',
    plan_step_id: 's1',
    content: '检索步骤',
    tool_hint: 'search_knowledge_base',
  } as any)
  return store
}

describe('execution_step_result · 阶段1诊断事件归约', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('更新时间线和 PlanStep，但不写 assistant 正文或 thinkingText', () => {
    const store = seedAssistant()
    const beforeContent = store.messages[0].content
    const beforeThinking = store.messages[0].thinkingText
    store.processEvent({
      type: 'execution_step_result',
      content: JSON.stringify({
        step_id: 's1',
        status: 'failed',
        result: '局部结果，不是最终回答',
        error: { code: 'timeout', message: '检索超时' },
        future_unknown_field: { arbitrary: ['shape'] },
      }),
    } as any)

    const msg = store.messages[0]
    expect(msg.events).toHaveLength(1)
    expect(msg.events[0].type).toBe('execution_step_result')
    expect(msg.content).toBe(beforeContent)
    expect(msg.thinkingText).toBe(beforeThinking)
    expect(store.planSteps[0].status).toBe('failed')
    expect(store.planSteps[0].result).toBe('局部结果，不是最终回答')
    expect(store.planSteps[0].error_context).toBe('检索超时')
    expect(msg.planSteps[0].result).toBe('局部结果，不是最终回答')
  })

  it('兼容 ExecutorAgent 旧 JSON 形状、对象 content 与字符串 error', () => {
    const store = seedAssistant()
    store.processEvent({
      type: 'execution_step_result',
      content: {
        step_id: 's1',
        description: '旧执行器步骤',
        status: 'completed',
        result: '旧局部结果',
        error: null,
        latency_ms: 12,
        tool_hint: 'search_knowledge_base',
      },
    } as any)

    expect(store.planSteps[0].status).toBe('completed')
    expect(store.planSteps[0].result).toBe('旧局部结果')
    expect(store.planSteps[0].error_context).toBeUndefined()
    expect(store.messages[0].content).toBe('authoritative-')
  })

  it('错误对象为旧字段时也归约 error_context；坏 JSON 不抛错', () => {
    const store = seedAssistant()
    expect(() => store.processEvent({
      type: 'execution_step_result',
      content: '{not-json',
    } as any)).not.toThrow()
    expect(store.messages[0].events).toHaveLength(1)
    expect(store.planSteps[0].status).toBe('pending')

    store.processEvent({
      type: 'execution_step_result',
      content: JSON.stringify({ step_id: 's1', status: 'failed', error: '字符串错误' }),
    } as any)
    expect(store.planSteps[0].error_context).toBe('字符串错误')
  })

  it('旧 chunk 与 done 行为保持兼容', () => {
    const store = seedAssistant()
    store.processEvent({ type: 'chunk', content: 'final-answer' } as any)
    store.processEvent({ type: 'done', content: '', saved: false } as any)

    expect(store.messages[0].content).toBe('authoritative-final-answer')
    expect(store.messages[0].thinkingText).toBe('existing-thinking')
    expect(store.messages[0].loading).toBe(false)
    expect(store.messages[0].messageId).toBeUndefined()
  })
})
