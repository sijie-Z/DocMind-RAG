/**
 * issue #289 G2：Agent 页 ToolCallCard 的「重试 N」徽标此前绑定 tool_call 的
 * evt.retry_attempt —— 后端 tool_call 事件从不携带该字段（恒 undefined，徽标
 * 永不显示；幽灵引用）。修复：与 getToolResult 同款前瞻配对，从配对的
 * tool_error 事件的 retry_attempt 取数（后端真实发射点，executor.py）。
 *
 * 变异点：视图绑定改回 evt.retry_attempt → 「配对 tool_error 带重试次数」
 * 用例红（徽标不出现）。
 */
import { describe, it, expect, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { NMessageProvider, NSpin, NButton, NTooltip, NIcon } from 'naive-ui'
import { createPinia, setActivePinia } from 'pinia'

import AgentView from '../index.vue'
import { useAgentStore } from '@/stores/agent'

// 视图 onMounted 会拉初始数据；测试里全部打桩为空。
vi.mock('@/api/agent', () => ({
  agentApi: {
    listTools: vi.fn().mockResolvedValue({ data: [] }),
    listSessions: vi.fn().mockResolvedValue({ data: { sessions: [] } }),
    getConfig: vi.fn().mockResolvedValue({ data: {} }),
    chat: vi.fn(),
    getSession: vi.fn().mockResolvedValue({ data: {} }),
    deleteSession: vi.fn().mockResolvedValue({}),
    createSession: vi.fn().mockResolvedValue({ data: null }),
    updateConfig: vi.fn().mockResolvedValue({}),
    submitFeedback: vi.fn().mockResolvedValue({}),
  },
}))

const Host = defineComponent({
  render: () => h(NMessageProvider, null, { default: () => h(AgentView) }),
})

// 注意：不 stub ToolCallCard——本文件就是要断言它头部的重试徽标。
const stubs = {
  PlanTree: true,
  ThinkingStream: true,
  ReflectionBanner: true,
  RetryHintLine: true,
  RunReportBlock: true,
  AgentConfigPanel: true,
  SessionSelector: true,
  AgentInput: true,
  Markdown: true,
  NSpin: true,
  NButton: true,
  NTooltip: true,
  NIcon: true,
}

async function setupView(events: unknown[]) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useAgentStore()
  store.addMessage({
    role: 'assistant',
    content: '最终答案',
    events,
    loading: false,
    currentTool: '',
    thinkingText: '',
    planSteps: [],
    planId: '',
    progress: 0,
  } as any)
  const wrapper = mount(Host, {
    global: {
      plugins: [pinia],
      components: { NSpin, NButton, NTooltip, NIcon },
      stubs,
    },
  })
  await flushPromises()
  return wrapper
}

describe('AgentView · ToolCallCard 重试徽标（issue #289 G2）', () => {
  it('配对 tool_error 携带 retry_attempt → 徽标「重试 N」（此前恒不显示）', async () => {
    const wrapper = await setupView([
      { type: 'tool_call', tool_name: 'search', tool_call_id: 'c1' },
      { type: 'tool_error', tool_name: 'search', tool_call_id: 'c1', content: '超时', retry_attempt: 2 },
    ])

    const badge = wrapper.find('.tool-retry')
    expect(badge.exists()).toBe(true)
    expect(badge.text()).toContain('重试 2')
  })

  it('配对 tool_result（成功）→ 无重试徽标（负例）', async () => {
    const wrapper = await setupView([
      { type: 'tool_call', tool_name: 'search', tool_call_id: 'c2' },
      { type: 'tool_result', tool_name: 'search', tool_call_id: 'c2', content: 'ok' },
    ])

    expect(wrapper.find('.tool-retry').exists()).toBe(false)
  })

  it('tool_call 无配对事件 → 无重试徽标（负例）', async () => {
    const wrapper = await setupView([
      { type: 'tool_call', tool_name: 'search', tool_call_id: 'c3' },
    ])

    expect(wrapper.find('.tool-retry').exists()).toBe(false)
  })
})
