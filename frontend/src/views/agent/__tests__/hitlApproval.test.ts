/**
 * HITL 视图接线测试（#HITL：审批卡 / 服务端停止 / 刷新回到现场）。
 *
 * 与 errorRendering.test.ts 同款驱动：mock api 模块 + 真 ApprovalCard/AgentInput
 * 挂载视图，断言交互确实走到了服务端接口。
 *
 * 变异点：
 * - handleStop 退回纯客户端 abort（不发 cancelTask）→ 停止用例红；
 * - 视图不渲染 ApprovalCard dock → 审批卡用例红；
 * - onMounted 不做 restorePendingApproval → 刷新回到现场用例红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { NMessageProvider, NSpin, NButton, NTooltip, NIcon } from 'naive-ui'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import zh from '@/locales/zh'

import AgentView from '../index.vue'
import ApprovalCard from '@/components/agent/ApprovalCard.vue'
import { useAgentStore } from '@/stores/agent'

const apiMocks = vi.hoisted(() => ({
  listTools: vi.fn().mockResolvedValue({ data: { success: true, data: [] } }),
  listSessions: vi.fn().mockResolvedValue({ data: { success: true, data: { sessions: [] } } }),
  getConfig: vi.fn().mockResolvedValue({ data: { success: true, data: {} } }),
  chat: vi.fn(),
  getSession: vi.fn().mockResolvedValue({ data: { success: true, data: {} } }),
  deleteSession: vi.fn().mockResolvedValue({}),
  createSession: vi.fn().mockResolvedValue({ data: { success: true, data: null } }),
  updateConfig: vi.fn().mockResolvedValue({}),
  submitFeedback: vi.fn().mockResolvedValue({}),
  getTask: vi.fn().mockResolvedValue({ data: { success: true, data: { state: 'completed' } } }),
  cancelTask: vi.fn().mockResolvedValue({ data: { state: 'cancel_requested' } }),
  decideApproval: vi.fn().mockResolvedValue({ abort: () => {} }),
}))

vi.mock('@/api/agent', () => ({ agentApi: apiMocks }))

// F4：AgentView 用 t('hitl.*') 渲染取消标识——视图测试统一装 zh 实例
const i18n = createI18n({ legacy: false, locale: 'zh', fallbackLocale: 'zh', messages: { zh } })

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
  Markdown: true,
  NSpin: true,
  NButton: true,
  NTooltip: true,
  NIcon: true,
  // 注意：AgentInput / ApprovalCard **不 stub**——停止与审批交互要在真组件上点。
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

function setupStore() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useAgentStore()
  return { pinia, store }
}

describe('AgentView · HITL 接线', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
    apiMocks.getTask.mockResolvedValue({ data: { success: true, data: { state: 'completed' } } })
    apiMocks.cancelTask.mockResolvedValue({ data: { state: 'cancel_requested' } })
    apiMocks.decideApproval.mockResolvedValue({ abort: () => {} })
  })

  it('停止按钮请求服务端取消（POST cancel 语义）', async () => {
    // 挂载恢复路径要先拿到"未终态"的任务行，否则会清掉陈旧指针（见下面用例）；
    // mock 形状=真实拦截器输出（完整 AxiosResponse，信封在 res.data）
    apiMocks.getTask.mockResolvedValue({ data: { success: true, data: { state: 'running' } } })
    const { pinia, store } = setupStore()
    store.setTaskId('task-1')
    store.setLoading(true)
    store.addMessage({
      role: 'assistant', content: '', events: [], loading: true, currentTool: '',
      thinkingText: '', planSteps: [], planId: '', progress: 0,
    } as any)

    const wrapper = await mountView(pinia)
    const stopBtn = wrapper.find('.stop-btn')
    expect(stopBtn.exists()).toBe(true)
    await stopBtn.trigger('click')
    await flushPromises()

    expect(apiMocks.cancelTask).toHaveBeenCalledWith('task-1')
    wrapper.unmount()
  })

  it('pendingApproval 时渲染审批卡；批准走 decideApproval 并携带改指令', async () => {
    apiMocks.getTask.mockResolvedValue({
      data: {
        success: true,
        data: {
          state: 'waiting_approval',
          approval: {
            tool_name: 'execute_python', tool_args: { code: 'print(2)' },
            risk_level: 'high', reason: '高风险工具执行前需人工审批',
          },
        },
      },
    })
    const { pinia, store } = setupStore()
    store.setTaskId('task-2')
    store.setTaskState('waiting_approval')
    store.setPendingApproval({
      tool_name: 'execute_python',
      tool_args: { code: 'print(2)' },
      risk_level: 'high',
      reason: '高风险工具执行前需人工审批',
    })

    const wrapper = await mountView(pinia)
    const card = wrapper.findComponent(ApprovalCard)
    expect(card.exists()).toBe(true)
    expect(card.text()).toContain('execute_python')

    card.vm.$emit('decide', { decision: 'approve', modifiedInstruction: '只读查询' })
    await flushPromises()

    expect(apiMocks.decideApproval).toHaveBeenCalledTimes(1)
    const [calledTask, calledInput] = apiMocks.decideApproval.mock.calls[0]
    expect(calledTask).toBe('task-2')
    expect(calledInput).toMatchObject({ decision: 'approve', modified_instruction: '只读查询' })
    wrapper.unmount()
  })

  it('刷新/重连回到审批现场：持久化 task id + GET task → 重建审批卡', async () => {
    localStorage.setItem('docmind.agent.lastTaskId', 't-restore')
    apiMocks.getTask.mockResolvedValue({
      data: {
        success: true,
        data: {
          state: 'waiting_approval',
          approval: {
            tool_name: 'execute_sql',
            tool_args: { query: 'select 1' },
            risk_level: 'high',
            reason: '高风险工具执行前需人工审批',
          },
        },
      },
    })
    const { pinia, store } = setupStore()

    const wrapper = await mountView(pinia)

    expect(apiMocks.getTask).toHaveBeenCalledWith('t-restore')
    expect(store.taskState).toBe('waiting_approval')
    expect(store.pendingApproval?.tool_name).toBe('execute_sql')
    expect(wrapper.findComponent(ApprovalCard).exists()).toBe(true)
    wrapper.unmount()
  })

  it('陈旧任务指针（已终态）不重建卡片并清掉持久化', async () => {
    localStorage.setItem('docmind.agent.lastTaskId', 't-stale')
    apiMocks.getTask.mockResolvedValue({ data: { success: true, data: { state: 'completed' } } })
    const { pinia, store } = setupStore()

    const wrapper = await mountView(pinia)

    expect(store.pendingApproval).toBeNull()
    expect(localStorage.getItem('docmind.agent.lastTaskId')).toBeNull()
    wrapper.unmount()
  })

  it('F4：run_cancelled 事件有可见"已取消"徽标 + 思考流切换停止态', async () => {
    const { pinia, store } = setupStore()
    store.setTaskState('cancelled')
    store.addMessage({
      role: 'assistant',
      content: '',
      events: [{ type: 'run_cancelled', content: '任务已被用户取消', task_id: 't-c' } as any],
      loading: false,
      currentTool: '',
      thinkingText: '执行步骤: 一',
      planSteps: [],
      planId: '',
      progress: 0,
    } as any)

    const wrapper = await mountView(pinia)

    const badge = wrapper.find('.run-cancelled-badge')
    expect(badge.exists()).toBe(true)
    expect(badge.text()).toBe('已取消')
    expect(wrapper.find('.run-cancelled-text').text()).toContain('任务已被用户取消')
    // 思考流收到停止态（组件的终态标签渲染由 thinkingStopped.test.ts 单独钉）
    expect(wrapper.findComponent({ name: 'ThinkingStream' }).props('stopped')).toBe(true)
    // 与"运行失败"的红色徽标体系区分（取消是正常收敛，不是错误）
    expect(wrapper.find('.run-error-badge').exists()).toBe(false)
    // 未取消时不得出现（不是恒显）
    const clean = setupStore()
    clean.store.addMessage({
      role: 'assistant', content: 'a', events: [], loading: false, currentTool: '',
      thinkingText: '', planSteps: [], planId: '', progress: 0,
    } as any)
    const w2 = await mountView(clean.pinia)
    expect(w2.find('.run-cancelled-badge').exists()).toBe(false)
    wrapper.unmount()
    w2.unmount()
  })
})
