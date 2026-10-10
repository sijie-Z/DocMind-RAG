/**
 * HITL store 落账测试：approval_required / run_cancelled / done.task_state /
 * task_id 持久化。
 *
 * 变异点：
 * - 去掉 approval_required case → 审批卡数据源恒空 → 红；
 * - run_cancelled 不置 cancelled → "取消不是完成"用例红；
 * - done 无视 task_state → 暂停后流结束被当完成、卡片被清 → 红。
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useAgentStore } from '../agent'

describe('useAgentStore · HITL 事件落账', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
  })

  function seedAssistant() {
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
    } as any)
    return store
  }

  it('approval_required：任务态 waiting_approval + 审批卡数据 + task_id 持久化', () => {
    const store = seedAssistant()
    store.processEvent({
      type: 'approval_required',
      content: '高风险工具 execute_python 执行前需人工审批',
      tool_name: 'execute_python',
      tool_args: { code: 'print(1)' },
      tool_call_id: 'tc-1',
      risk_level: 'high',
      plan_step_id: 's1',
      task_id: 't-abc',
    } as any)

    expect(store.taskState).toBe('waiting_approval')
    expect(store.pendingApproval).toMatchObject({
      tool_name: 'execute_python',
      tool_args: { code: 'print(1)' },
      risk_level: 'high',
      tool_call_id: 'tc-1',
    })
    expect(store.taskId).toBe('t-abc')
    expect(localStorage.getItem('docmind.agent.lastTaskId')).toBe('t-abc')
    // 事件落进消息流（时间线可见"为什么停了"）
    const msg = store.messages[store.messages.length - 1]
    expect(msg.events.some((e) => e.type === 'approval_required')).toBe(true)
  })

  it('run_cancelled：诚实收敛到 cancelled（不是完成也不是错误）', () => {
    const store = seedAssistant()
    store.processEvent({ type: 'run_cancelled', content: '任务已被用户取消', task_id: 't-1' } as any)

    expect(store.taskState).toBe('cancelled')
    expect(store.pendingApproval).toBeNull()
    expect(store.isLoading).toBe(false)
    const msg = store.messages[store.messages.length - 1]
    expect(msg.loading).toBe(false)
    expect(msg.events.some((e) => e.type === 'run_cancelled')).toBe(true)
  })

  it('done(task_state=waiting_approval)：暂停后的流结束不得当成完成、卡片保留', () => {
    const store = seedAssistant()
    store.processEvent({
      type: 'approval_required',
      content: '审批理由',
      tool_name: 'execute_python',
      tool_args: {},
      risk_level: 'high',
      task_id: 't-1',
    } as any)
    store.processEvent({ type: 'done', content: '', task_state: 'waiting_approval' } as any)

    expect(store.taskState).toBe('waiting_approval')
    expect(store.pendingApproval).not.toBeNull()
    expect(store.isLoading).toBe(false)
  })

  it('done 正常完成：清卡片 + completed', () => {
    const store = seedAssistant()
    store.processEvent({
      type: 'approval_required',
      content: '审批理由',
      tool_name: 'execute_python',
      tool_args: {},
      risk_level: 'high',
      task_id: 't-1',
    } as any)
    store.processEvent({ type: 'done', content: '' } as any)

    expect(store.taskState).toBe('completed')
    expect(store.pendingApproval).toBeNull()
  })

  it('resetTask：新发送前复位任务现场与持久化指针', () => {
    const store = seedAssistant()
    store.setTaskId('t-old')
    store.setPendingApproval({ tool_name: 'x' } as any)
    store.resetTask()

    expect(store.taskId).toBeNull()
    expect(store.pendingApproval).toBeNull()
    expect(store.taskState).toBe('running')
    expect(localStorage.getItem('docmind.agent.lastTaskId')).toBeNull()
  })
})
