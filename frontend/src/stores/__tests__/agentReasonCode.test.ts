/**
 * store 对 approval_required 的 reason_code/reason_params 透传（i18n 契约）。
 *
 * 变异点：删 store 里 pendingApproval 的 reason_code/reason_params 透传 → 红。
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useAgentStore } from '@/stores/agent'
import type { AgentEvent } from '@/types/agent'

describe('agent store · approval_required 理由码透传', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('事件带 reason_code/params → pendingApproval 透传给卡片', () => {
    const store = useAgentStore()
    store.processEvent({
      type: 'approval_required',
      content: '中文兜底',
      tool_name: 'execute_python',
      tool_args: { code: 'print(1)' },
      tool_call_id: 'c1',
      risk_level: 'high',
      reason_code: 'high_risk_tool',
      reason_params: { tool: 'execute_python', risk: 'high' },
    } as AgentEvent)
    const pa = store.pendingApproval
    expect(pa?.reason_code).toBe('high_risk_tool')
    expect(pa?.reason_params).toEqual({ tool: 'execute_python', risk: 'high' })
    expect(pa?.reason).toBe('中文兜底')
  })

  it('事件不带 reason_code（旧后端事件）→ 字段 undefined，卡片走回退路径', () => {
    const store = useAgentStore()
    store.processEvent({
      type: 'approval_required',
      content: '中文兜底',
      tool_name: 'execute_python',
      risk_level: 'high',
    } as AgentEvent)
    const pa = store.pendingApproval
    expect(pa?.reason_code).toBeUndefined()
    expect(pa?.reason).toBe('中文兜底')
  })
})
