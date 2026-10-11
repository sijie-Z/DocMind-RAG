/**
 * issue #250 第 6 条收尾（review_mx250 P2①）：retry_hint 事件此前只到 store
 * 层（落进 msg.events + store 用例钉住），但 Agent 视图对事件类型没有任何渲染
 * 分支 —— UI 仍然看不到。#253 对 run_report 的处置是「放行**并展示**」，本次
 * 把「展示」的一半补齐，本文件是它的判别用例。
 *
 * 变异点：删 index.vue 里 `v-else-if="evt.type === 'retry_hint'"` 分支
 * → 「有事件」用例红；分支改成无条件渲染 → 「无事件」用例红。
 */
import { describe, it, expect, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { NMessageProvider, NSpin, NButton, NTooltip, NIcon } from 'naive-ui'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@/locales'

import AgentView from '../index.vue'
import { useAgentStore } from '@/stores/agent'

// 视图 onMounted 会拉初始数据；测试里全部打桩为空。
vi.mock('@/api/agent', () => ({
  agentApi: {
    listTools: vi.fn().mockResolvedValue({ data: { success: true, data: [] } }),
    listSessions: vi.fn().mockResolvedValue({ data: { success: true, data: { sessions: [] } } }),
    getConfig: vi.fn().mockResolvedValue({ data: { success: true, data: {} } }),
    chat: vi.fn(),
    getSession: vi.fn().mockResolvedValue({ data: { success: true, data: {} } }),
    deleteSession: vi.fn().mockResolvedValue({}),
    createSession: vi.fn().mockResolvedValue({ data: { success: true, data: null } }),
    updateConfig: vi.fn().mockResolvedValue({}),
    submitFeedback: vi.fn().mockResolvedValue({}),
  },
}))

// 视图在 setup 里调 useDedupedMessage()（naive-ui useMessage 需要 Provider）
const Host = defineComponent({
  render: () => h(NMessageProvider, null, { default: () => h(AgentView) }),
})

const stubs = {
  // 显式导入的子组件（脚本里 import 的，按名 stub 掉，避免深挂载）
  PlanTree: true,
  ThinkingStream: true,
  ToolCallCard: true,
  ReflectionBanner: true,
  AgentConfigPanel: true,
  SessionSelector: true,
  AgentInput: true,
  Markdown: true,
  // 模板里用到但未本地注册的 naive-ui 组件：注册 + stub，避免解析告警
  NSpin: true,
  NButton: true,
  NTooltip: true,
  NIcon: true,
}

function seedAssistantMessage(events: unknown[] = []) {
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
  return store
}

async function setupView(events: unknown[] = []) {
  const pinia = createPinia()
  setActivePinia(pinia)
  seedAssistantMessage(events)
  const wrapper = mount(Host, {
    global: {
      plugins: [pinia, i18n],
      components: { NSpin, NButton, NTooltip, NIcon },
      stubs,
    },
  })
  await flushPromises()
  return wrapper
}

describe('AgentView · retry_hint 渲染分支（#250 第 6 条）', () => {
  it('有 retry_hint 事件 → 渲染轻量提示行（reset_transport → 中文动作）', async () => {
    const wrapper = await setupView([
      {
        type: 'retry_hint',
        content: 'retry_action=reset_transport',
        plan_step_id: 's1',
        retry_attempt: 1,
      },
    ])

    const hint = wrapper.find('.retry-hint')
    expect(hint.exists()).toBe(true)
    expect(hint.text()).toContain('重建连接后重试')
  })

  it('未知 retry_action → 回退展示原始 content（不吞信息）', async () => {
    const wrapper = await setupView([
      { type: 'retry_hint', content: 'retry_action=some_future_action' },
    ])

    const hint = wrapper.find('.retry-hint')
    expect(hint.exists()).toBe(true)
    expect(hint.text()).toContain('retry_action=some_future_action')
  })

  it('无 retry_hint 事件 → 不渲染提示行（分支可关，不是恒显）', async () => {
    const wrapper = await setupView([
      { type: 'tool_error', content: '出错了' },
    ])

    expect(wrapper.find('.retry-hint').exists()).toBe(false)
  })
})
