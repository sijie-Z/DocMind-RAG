/**
 * issue #289 G1：Agent 页 ThinkingStream 的思考类型标签此前恒为「💭 思考中」
 * —— 视图 getLastThinkingType() 过滤 msg.events 里的 thinking 事件，而 store
 * 的 thinking case 只写 thinkingText、从不 push 到 events，过滤恒空（幽灵引用）。
 * 修复：thinking 事件落进 msg.events，标签随最后一个 thinking 的 thinking_type
 * 变化（planning/evaluation/correction 可区分）。
 *
 * 变异点：删掉 stores/agent.ts thinking case 里的 msg.events.push(event)
 * → 「规划中」用例红（回退为默认「思考中」）。
 */
import { describe, it, expect, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { NMessageProvider, NSpin, NButton, NTooltip, NIcon } from 'naive-ui'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import zh from '@/locales/zh'

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

// F4：AgentView 用 t('hitl.*') 渲染取消标识——视图测试统一装 zh 实例
const i18n = createI18n({ legacy: false, locale: 'zh', fallbackLocale: 'zh', messages: { zh } })

const Host = defineComponent({
  render: () => h(NMessageProvider, null, { default: () => h(AgentView) }),
})

// 注意：不 stub ThinkingStream——本文件就是要断言它的类型标签渲染。
const stubs = {
  PlanTree: true,
  ToolCallCard: true,
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

async function setupView() {
  const pinia = createPinia()
  setActivePinia(pinia)
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
  const wrapper = mount(Host, {
    global: {
      plugins: [pinia, i18n],
      components: { NSpin, NButton, NTooltip, NIcon },
      stubs,
    },
  })
  await flushPromises()
  return { wrapper, store }
}

describe('AgentView · ThinkingStream 思考类型标签（issue #289 G1）', () => {
  it('thinking_type=planning → 标签「规划中」（而非默认「思考中」）', async () => {
    const { wrapper, store } = await setupView()
    store.processEvent({ type: 'thinking', content: '先规划任务', thinking_type: 'planning' } as any)
    await nextTick()

    const label = wrapper.find('.thinking-label')
    expect(label.exists()).toBe(true)
    expect(label.text()).toContain('规划中')
  })

  it('标签随最后一个 thinking 事件变化（planning → correction）', async () => {
    const { wrapper, store } = await setupView()
    store.processEvent({ type: 'thinking', content: '先规划任务', thinking_type: 'planning' } as any)
    await nextTick()
    expect(wrapper.find('.thinking-label').text()).toContain('规划中')

    store.processEvent({ type: 'thinking', content: '修正一下', thinking_type: 'correction' } as any)
    await nextTick()
    expect(wrapper.find('.thinking-label').text()).toContain('纠正中')
  })
})
