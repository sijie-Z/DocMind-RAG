import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useAgentStore } from '../agent'

/**
 * issue #242 收尾：run_report / execution_context 两个事件此前在
 * sseService 白名单层被静默丢弃（后端产、前端扔），UI 永远看不到
 * Run Report 与 Tokens——本文件钉住"事件必须落账到最后一条回答上"。
 *
 * 变异点：删 stores/agent.ts 里对应的 case（或从 sseService 白名单移除类型）
 * → 对应用例红。
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

  it('未知事件类型不抛错、不影响既有消息', () => {
    const store = seedAssistantMessage()
    const before = { ...store.messages[store.messages.length - 1] }
    store.processEvent({ type: 'retry_hint', content: 'x' } as any)
    const msg = store.messages[store.messages.length - 1]
    expect(msg.runReport).toBeUndefined()
    expect(msg.executionContext).toBeUndefined()
    expect(msg.content).toBe(before.content)
  })
})
