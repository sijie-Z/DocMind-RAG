/**
 * User Commands (#475) · Agent store 对 command_start/result/error 的落账判别。
 *
 * 契约（与 backend/app/agent/events.py / commands/execution.py 对齐）：
 * - command_start：事件进入消息时间线，消息标记为命令消息，运行中不清空；
 * - command_result：结构化结果（content + command_data）进入时间线，结束 loading；
 * - command_error：稳定错误文案 + error_type 进入时间线，结束 loading；
 * - 命令消息不吸收 chunk：后端为兼容旧 UI 会在 command_result 后再发一条
 *   chunk（同内容），若照常拼接会重复渲染结果 —— 命令消息必须忽略 chunk。
 *
 * 变异点：
 * - store 不处理 command_* → 落账/状态用例红；
 * - chunk 分支不区分命令消息 → 去重用例红（content 出现重复结果）；
 * - command_error 不落 error_type → 错误分类用例红。
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useAgentStore } from '../agent'

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
    startedAt: Date.now(),
  })
  return store
}

describe('useAgentStore · User Commands 事件落账', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('command_start 进入时间线并标记命令消息（loading 保持）', () => {
    const store = seedAssistantMessage()
    store.processEvent({
      type: 'command_start',
      content: '执行命令 /status',
      command_name: 'status',
    } as any)

    const msg = store.messages[store.messages.length - 1]
    expect(msg.isCommand).toBe(true)
    expect(msg.loading).toBe(true)
    expect(msg.events.map((e) => e.type)).toEqual(['command_start'])
    expect(msg.events[0].command_name).toBe('status')
  })

  it('command_result 落内容与结构化载荷，结束 loading（结果不写进 content）', () => {
    const store = seedAssistantMessage()
    store.processEvent({ type: 'command_start', content: '执行命令 /tools', command_name: 'tools' } as any)
    store.processEvent({
      type: 'command_result',
      content: '## 工具（3）',
      command_name: 'tools',
      command_data: { total: 3, tools: [{ name: 'web_search' }] },
    } as any)

    const msg = store.messages[store.messages.length - 1]
    const terminal = msg.events[msg.events.length - 1]
    expect(terminal.type).toBe('command_result')
    expect(terminal.content).toBe('## 工具（3）')
    expect(terminal.command_data?.total).toBe(3)
    expect(msg.content).toBe('')
    expect(msg.loading).toBe(false)
  })

  it('command_error 落稳定文案与 error_type，结束 loading', () => {
    const store = seedAssistantMessage()
    store.processEvent({ type: 'command_start', content: '执行命令 /nope', command_name: 'nope' } as any)
    store.processEvent({
      type: 'command_error',
      content: '未知命令：/nope',
      command_name: 'nope',
      error_type: 'unknown_command',
    } as any)

    const msg = store.messages[store.messages.length - 1]
    const terminal = msg.events[msg.events.length - 1]
    expect(terminal.type).toBe('command_error')
    expect(terminal.error_type).toBe('unknown_command')
    expect(terminal.content).toBe('未知命令：/nope')
    expect(msg.loading).toBe(false)
  })

  it('命令结果后的兼容 chunk 不重复拼接（命令消息忽略 chunk）', () => {
    const store = seedAssistantMessage()
    store.processEvent({ type: 'command_start', content: '执行命令 /status', command_name: 'status' } as any)
    store.processEvent({
      type: 'command_result',
      content: '## 会话状态\n- 消息数：3',
      command_name: 'status',
      command_data: { session: { message_count: 3 } },
    } as any)
    // backend/app/api/v1/endpoints/agent.py: 兼容旧 UI 的 chunk（同内容）
    store.processEvent({ type: 'chunk', content: '## 会话状态\n- 消息数：3' } as any)

    const msg = store.messages[store.messages.length - 1]
    expect(msg.content).toBe('')
    expect(msg.events.filter((e) => e.type === 'command_result')).toHaveLength(1)
  })

  it('普通（非命令）消息的 chunk 不受影响', () => {
    const store = seedAssistantMessage()
    store.processEvent({ type: 'chunk', content: '普通回答' } as any)
    const msg = store.messages[store.messages.length - 1]
    expect(msg.content).toBe('普通回答')
    expect(msg.isCommand).toBeFalsy()
  })

  it('done 事件结束时命令消息同样收敛 loading', () => {
    const store = seedAssistantMessage()
    store.processEvent({ type: 'command_start', content: '执行命令 /help', command_name: 'help' } as any)
    store.processEvent({ type: 'command_result', content: 'ok', command_name: 'help' } as any)
    store.processEvent({ type: 'done', content: '', saved: false } as any)
    const msg = store.messages[store.messages.length - 1]
    expect(msg.loading).toBe(false)
    expect(store.isLoading).toBe(false)
  })
})
