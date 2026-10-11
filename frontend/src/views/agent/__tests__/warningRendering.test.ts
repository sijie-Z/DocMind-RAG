/**
 * warning 渲染分支判别用例（UI 验收 round2 · B29 / issue #301 批 D #22 展示面）。
 *
 * 后端在「回答已生成、但会话落库失败」时发射 {type:'warning', content}
 * （agent.py:207-212 / :341-347），store 已收（stores/agent.ts warning case），
 * 但 Agent 视图此前无渲染分支 —— 唯一失败信号不可见（用户刷新后问答消失
 * 仍无提示）。本文件钉住视图渲染分支及与 error 框的视觉区分。
 *
 * 变异点：
 * - 删 v-else-if="evt.type === 'warning'" 分支块 → 用例 (a)(c)(d) 红；
 * - 分支改为无条件 v-else → 用例 (b) 红（把 tool_result 误渲染成告警）。
 */
import { describe, it, expect, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import { NMessageProvider, NSpin, NButton, NTooltip, NIcon } from 'naive-ui'
import { createPinia, setActivePinia } from 'pinia'

import AgentView from '../index.vue'
import { useAgentStore } from '@/stores/agent'
import zh from '@/locales/zh'

vi.mock('@/api/agent', () => ({
  agentApi: {
    listTools: vi.fn().mockResolvedValue({ data: { success: true, data: [], total: 0 } }),
    listSessions: vi.fn().mockResolvedValue({ data: { success: true, data: { sessions: [], total: 0, page: 1, page_size: 20 } } }),
    getConfig: vi.fn().mockResolvedValue({ data: { success: true, data: {} } }),
    chat: vi.fn(),
    getSession: vi.fn().mockResolvedValue({ data: { success: true, data: null } }),
    deleteSession: vi.fn().mockResolvedValue({}),
    createSession: vi.fn().mockResolvedValue({ data: { success: true, data: null } }),
    updateConfig: vi.fn().mockResolvedValue({}),
    submitFeedback: vi.fn().mockResolvedValue({}),
  },
}))

const Host = defineComponent({
  render: () => h(NMessageProvider, null, { default: () => h(AgentView) }),
})

const stubs = {
  PlanTree: true,
  ThinkingStream: true,
  ToolCallCard: true,
  ReflectionBanner: true,
  AgentConfigPanel: true,
  SessionSelector: true,
  AgentInput: true,
  Markdown: true,
  // replay 支线并入后的新子组件（装配对齐：n-popover 未注册时子组件会被直接渲染）
  RecentTaskList: true,
  ReplayTimeline: true,
  NSpin: true,
  NButton: true,
  NTooltip: true,
  NIcon: true,
}

// replay 支线给 AgentView 引入 useI18n()——mount 必须装 zh i18n 实例
const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

function seedStore(overrides: Record<string, unknown> = {}) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useAgentStore()
  store.addMessage({
    role: 'assistant',
    content: '',
    events: [],
    loading: false,
    currentTool: '',
    thinkingText: '',
    planSteps: [],
    planId: '',
    progress: 0,
    ...overrides,
  } as any)
  return { pinia, store }
}

async function mountView(pinia: ReturnType<typeof createPinia>) {
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

const WARNING_CONTENT = '回答已生成，但会话记录保存失败，刷新后本轮对话可能不可见'

describe('AgentView · warning 渲染分支（B29 落库失败提示）', () => {
  it('有 warning 事件 → 告警行出屏（含「提示」徽标与失败原因）', async () => {
    const { pinia } = seedStore({ events: [{ type: 'warning', content: WARNING_CONTENT }] })
    const wrapper = await mountView(pinia)

    const box = wrapper.find('.run-warning-standalone')
    expect(box.exists()).toBe(true)
    expect(box.text()).toContain('提示')
    expect(box.text()).toContain(WARNING_CONTENT)
  })

  it('无 warning 事件 → 不渲染告警行（分支可关，不是恒显）', async () => {
    const { pinia } = seedStore({
      events: [
        { type: 'tool_result', content: '工具结果', tool_call_id: 'c1' },
        { type: 'retry_hint', content: '正在重试' },
      ],
    })
    const wrapper = await mountView(pinia)
    expect(wrapper.find('.run-warning-standalone').exists()).toBe(false)
  })

  it('store 全链路：processEvent(warning) 落 events 并立即出屏', async () => {
    const { pinia, store } = seedStore()
    const wrapper = await mountView(pinia)

    store.processEvent({ type: 'warning', content: WARNING_CONTENT } as any)
    await nextTick()

    expect((store.messages[0] as any).events).toHaveLength(1)
    expect(wrapper.find('.run-warning-standalone').text()).toContain(WARNING_CONTENT)
  })

  it('warning 与 error 并存 → 各自独立渲染（amber 告警 ≠ 红色运行失败）', async () => {
    const { pinia } = seedStore({
      events: [
        { type: 'warning', content: WARNING_CONTENT },
        { type: 'error', content: 'Agent 执行出错' },
      ],
    })
    const wrapper = await mountView(pinia)

    expect(wrapper.findAll('.run-warning-standalone')).toHaveLength(1)
    expect(wrapper.findAll('.run-error-standalone')).toHaveLength(1)
    expect(wrapper.find('.run-warning-badge').text()).toBe('提示')
    expect(wrapper.find('.run-error-badge').text()).toBe('运行失败')
  })
})
