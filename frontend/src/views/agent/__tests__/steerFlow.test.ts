/**
 * A：运行中转向——视图接线 + 时间线渲染 + 四语键完整性。
 *
 * 与 hitlApproval.test.ts 同款驱动：mock api 模块 + 真 AgentInput 挂载视图。
 *
 * 变异点：
 * - handleSteer 不调 agentApi.steerTask（走本地假动作）→ 提交用例红；
 * - 成功后不落"已发送指导"时间线条目 → 渲染用例红；
 * - steer_applied 事件无渲染分支 → 生效用例红；
 * - 失败（429）也落条目（把拒绝当受理）→ 失败用例红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { NMessageProvider, NDialogProvider, NSpin, NButton, NTooltip, NIcon } from 'naive-ui'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import zh from '@/locales/zh'
import en from '@/locales/en'
import ja from '@/locales/ja'
import fr from '@/locales/fr'

import AgentView from '../index.vue'
import { useAgentStore } from '@/stores/agent'

const apiMocks = vi.hoisted(() => ({
  listTools: vi.fn().mockResolvedValue({ data: [] }),
  listSessions: vi.fn().mockResolvedValue({ data: { sessions: [] } }),
  getConfig: vi.fn().mockResolvedValue({ data: {} }),
  chat: vi.fn(),
  getSession: vi.fn().mockResolvedValue({ data: {} }),
  deleteSession: vi.fn().mockResolvedValue({}),
  createSession: vi.fn().mockResolvedValue({ data: null }),
  updateConfig: vi.fn().mockResolvedValue({}),
  submitFeedback: vi.fn().mockResolvedValue({}),
  getTask: vi.fn().mockResolvedValue({ data: { success: true, data: { state: 'completed' } } }),
  cancelTask: vi.fn().mockResolvedValue({ data: { state: 'cancel_requested' } }),
  decideApproval: vi.fn().mockResolvedValue({ abort: () => {} }),
  steerTask: vi.fn(),
}))

vi.mock('@/api/agent', () => ({ agentApi: apiMocks }))

const i18n = createI18n({ legacy: false, locale: 'zh', fallbackLocale: 'zh', messages: { zh } })

const Host = defineComponent({
  render: () => h(NMessageProvider, null, { default: () => h(NDialogProvider, null, { default: () => h(AgentView) }) }),
})

const stubs = {
  PlanTree: true,
  ThinkingStream: true,
  ToolCallCard: true,
  ReflectionBanner: true,
  RunReportBlock: true,
  AgentConfigPanel: true,
  SessionSelector: true,
  Markdown: true,
  NSpin: true,
  NButton: true,
  NTooltip: true,
  NIcon: true,
  // AgentInput / ApprovalCard 不 stub——转向交互在真组件上点。
}

async function mountView(pinia: ReturnType<typeof createPinia>) {
  const wrapper = mount(Host, {
    global: { plugins: [pinia, i18n], components: { NSpin, NButton, NTooltip, NIcon }, stubs },
  })
  await flushPromises()
  return wrapper
}

function setupStore() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useAgentStore()
  return { pinia, store }
}

function addAssistant(store: ReturnType<typeof useAgentStore>, loading = false) {
  store.addMessage({
    role: 'assistant', content: '', events: [], loading, currentTool: '',
    thinkingText: '', planSteps: [], planId: '', progress: 0,
  } as any)
}

describe('AgentView · 运行中转向', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
    document.body.innerHTML = ''
    apiMocks.getTask.mockResolvedValue({ data: { success: true, data: { state: 'running' } } })
    apiMocks.steerTask.mockResolvedValue({
      data: { success: true, data: { task_id: 't', state: 'queued', pending: 1, message: '指导已入队；将在下一个步边界生效' } },
    })
  })

  it('running 中发送指导：POST steer + 时间线"已发送指导"条目', async () => {
    const { pinia, store } = setupStore()
    store.setTaskId('task-steer')
    store.setLoading(true)
    addAssistant(store, true)

    const wrapper = await mountView(pinia)
    await wrapper.find('.agent-textarea').setValue('重点关注风险章节')
    await wrapper.find('[data-testid="steer-btn"]').trigger('click')
    await flushPromises()

    expect(apiMocks.steerTask).toHaveBeenCalledTimes(1)
    expect(apiMocks.steerTask).toHaveBeenCalledWith('task-steer', '重点关注风险章节')
    expect(wrapper.find('.steer-badge').text()).toBe('已发送指导')
    expect(wrapper.find('.steer-text').text()).toContain('重点关注风险章节')
    wrapper.unmount()
  })

  it('waiting_approval：输入区切换为转向模式（发送指导替换发送）', async () => {
    apiMocks.getTask.mockResolvedValue({
      data: {
        success: true,
        data: {
          state: 'waiting_approval',
          approval: { tool_name: 'execute_python', risk_level: 'high', reason: 'r' },
        },
      },
    })
    const { pinia, store } = setupStore()
    store.setTaskId('task-wait')
    addAssistant(store, false)

    const wrapper = await mountView(pinia)
    expect(wrapper.find('.send-btn').exists()).toBe(false)
    expect(wrapper.find('[data-testid="steer-btn"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('steer_applied 事件渲染"指导已生效"条目（与"已发送"区分）', async () => {
    const { pinia, store } = setupStore()
    store.setTaskState('running')
    addAssistant(store, true)
    store.processEvent({ type: 'steer_applied', content: '重点关注风险章节', task_id: 't' } as any)

    const wrapper = await mountView(pinia)
    const applied = wrapper.find('.steer-badge-applied')
    expect(applied.exists()).toBe(true)
    expect(applied.text()).toBe('指导已生效')
    expect(wrapper.find('.steer-standalone.steer-applied').text()).toContain('重点关注风险章节')
    wrapper.unmount()
  })

  it('429（缓冲满）：不落"已发送"条目 + 诚实提示（不当受理）', async () => {
    apiMocks.steerTask.mockRejectedValue({ response: { status: 429, data: { message: '缓冲已满' } } })
    const { pinia, store } = setupStore()
    store.setTaskId('task-full')
    store.setLoading(true)
    addAssistant(store, true)

    const wrapper = await mountView(pinia)
    await wrapper.find('.agent-textarea').setValue('再来一条')
    await wrapper.find('[data-testid="steer-btn"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('.steer-badge').exists()).toBe(false)
    await flushPromises()
    expect(document.body.textContent).toContain('转向缓冲已满')
    wrapper.unmount()
  })

  it('四语键齐备：hitl.steer*（本特性新增键）', () => {
    const locales: Array<[string, Record<string, any>]> = [
      ['zh', zh], ['en', en], ['ja', ja], ['fr', fr],
    ]
    const keys = ['steerSend', 'steerHint', 'steerSent', 'steerApplied', 'steerFull', 'steerFailed']
    for (const [code, msg] of locales) {
      const hitl = (msg as any).hitl
      expect(hitl, `${code} 缺 hitl 段`).toBeTruthy()
      for (const k of keys) {
        expect(hitl[k], `${code} 缺 hitl.${k}`).toBeTruthy()
      }
    }
  })
})
