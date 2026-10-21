/**
 * B：计划审批——视图接线 + store 落账 + 四语键完整性。
 *
 * 变异点：
 * - pendingPlanApproval 不渲染卡片 → 卡片用例红；
 * - approve/reject/edit 不走 decidePlanApproval（或载荷错）→ 提交用例红；
 * - rejected JSON 终态分支不收敛现场（卡片残留）→ 终态用例红；
 * - store 的 done/waiting 语义破坏（暂停被当完成清卡）→ 现场保持用例红。
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
import PlanApprovalCard from '@/components/agent/PlanApprovalCard.vue'
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
  getTask: vi.fn().mockResolvedValue({ data: { success: true, data: { state: 'running' } } }),
  cancelTask: vi.fn().mockResolvedValue({ data: { state: 'cancel_requested' } }),
  decideApproval: vi.fn().mockResolvedValue({ abort: () => {} }),
  steerTask: vi.fn(),
  decidePlanApproval: vi.fn(),
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
  // AgentInput / ApprovalCard / PlanApprovalCard 不 stub——交互在真组件上点。
}

const PENDING = {
  plan_id: 'plan-1',
  goal: '分析财报风险',
  steps: [
    { id: 's1', description: '检索相关文档', dependencies: [], tool_hint: 'search_knowledge_base' },
    { id: 's2', description: '综合分析', dependencies: ['s1'], tool_hint: null },
  ],
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

describe('AgentView · 计划审批', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
    document.body.innerHTML = ''
    apiMocks.getTask.mockResolvedValue({ data: { success: true, data: { state: 'running' } } })
    apiMocks.decidePlanApproval.mockResolvedValue({ streamed: true, abort: () => {} })
  })

  it('pendingPlanApproval → 渲染计划卡 dock', async () => {
    const { pinia, store } = setupStore()
    store.setTaskId('task-plan')
    store.setTaskState('waiting_plan_approval')
    store.setPendingPlanApproval(PENDING as any)

    const wrapper = await mountView(pinia)
    const card = wrapper.findComponent(PlanApprovalCard)
    expect(card.exists()).toBe(true)
    expect(card.text()).toContain('分析财报风险')

    card.vm.$emit('decide', { decision: 'approve' })
    await flushPromises()
    expect(apiMocks.decidePlanApproval).toHaveBeenCalledTimes(1)
    const [tid, input] = apiMocks.decidePlanApproval.mock.calls[0]
    expect(tid).toBe('task-plan')
    expect(input).toMatchObject({ decision: 'approve' })
    wrapper.unmount()
  })

  it('编辑后批准 / 拒绝：载荷原样透传到 API', async () => {
    const { pinia, store } = setupStore()
    store.setTaskId('task-plan')
    store.setTaskState('waiting_plan_approval')
    store.setPendingPlanApproval(PENDING as any)

    const wrapper = await mountView(pinia)
    const card = wrapper.findComponent(PlanApprovalCard)

    const edited = [{ id: 's1', description: '编辑后的唯一步', dependencies: [], tool_hint: null }]
    card.vm.$emit('decide', { decision: 'edit', edited_steps: edited })
    await flushPromises()
    expect(apiMocks.decidePlanApproval.mock.calls[0][1]).toMatchObject({
      decision: 'edit',
      edited_steps: edited,
    })
    wrapper.unmount()

    // 第二次决策用全新挂载（首决后卡片被乐观清除、旧组件实例已卸载）
    const s2 = setupStore()
    s2.store.setTaskId('task-plan')
    s2.store.setTaskState('waiting_plan_approval')
    s2.store.setPendingPlanApproval({ ...PENDING, plan_id: 'plan-2' } as any)
    const w2 = await mountView(s2.pinia)
    w2.findComponent(PlanApprovalCard).vm.$emit('decide', { decision: 'reject', note: '不要这个思路' })
    await flushPromises()
    expect(apiMocks.decidePlanApproval.mock.calls[1][1]).toMatchObject({
      decision: 'reject',
      note: '不要这个思路',
    })
    w2.unmount()
  })

  it('reject#2（JSON 终态 rejected）：现场收敛（卡片清、状态 rejected、刷新权威）', async () => {
    apiMocks.decidePlanApproval.mockResolvedValue({
      streamed: false,
      state: 'rejected',
      rejections: 2,
      message: '计划已再次被拒绝，重规划机会已用尽，任务终止（rejected）',
    })
    // 第 1 次 getTask（挂载恢复现场）→ 仍在等待计划审批；
    // 第 2 次 getTask（终态后刷新）→ rejected。
    apiMocks.getTask
      .mockResolvedValueOnce({ data: { success: true, data: { state: 'waiting_plan_approval', plan_approval: PENDING } } })
      .mockResolvedValueOnce({ data: { success: true, data: { state: 'rejected', last_error: 'x' } } })
    const { pinia, store } = setupStore()
    store.setTaskId('task-plan')
    store.setTaskState('waiting_plan_approval')
    store.setPendingPlanApproval(PENDING as any)

    const wrapper = await mountView(pinia)
    wrapper.findComponent(PlanApprovalCard).vm.$emit('decide', { decision: 'reject', note: '还是不行' })
    await flushPromises()

    expect(store.pendingPlanApproval).toBeNull()
    expect(store.taskState).toBe('rejected')
    expect(apiMocks.getTask).toHaveBeenCalledWith('task-plan')
    wrapper.unmount()
  })

  it('422（非法编辑）：提示 + 刷新现场（卡片不静默消失）', async () => {
    const err = Object.assign(new Error('422'), { status: 422, detail: '步骤 s1 依赖不存在' })
    apiMocks.decidePlanApproval.mockRejectedValue(err)
    apiMocks.getTask.mockResolvedValue({
      data: { success: true, data: { state: 'waiting_plan_approval', plan_approval: PENDING } },
    })
    const { pinia, store } = setupStore()
    store.setTaskId('task-plan')
    store.setTaskState('waiting_plan_approval')
    store.setPendingPlanApproval(PENDING as any)

    const wrapper = await mountView(pinia)
    wrapper.findComponent(PlanApprovalCard).vm.$emit('decide', { decision: 'edit', edited_steps: [] })
    await flushPromises()

    expect(apiMocks.getTask).toHaveBeenCalledWith('task-plan')
    // 刷新后现场重建（任务仍 waiting_plan_approval）
    expect(store.pendingPlanApproval?.plan_id).toBe('plan-1')
    wrapper.unmount()
  })

  it('store：plan_approval_required 落账（含 PlanTree 回填）；done.waiting_plan_approval 保卡', () => {
    const { store } = setupStore()
    store.processEvent({
      type: 'plan_approval_required',
      content: '计划已生成（2 个步骤），等待审批',
      plan_id: 'plan-1',
      plan_goal: '目标',
      plan_steps: PENDING.steps,
      task_id: 't1',
    } as any)
    expect(store.taskState).toBe('waiting_plan_approval')
    expect(store.pendingPlanApproval?.steps.length).toBe(2)
    // PlanTree 回填（resume 流不回放 plan_step 事件）
    expect(store.planSteps.map((s) => s.id)).toEqual(['s1', 's2'])

    store.processEvent({ type: 'done', task_state: 'waiting_plan_approval' } as any)
    expect(store.pendingPlanApproval, '暂停不是完成，卡片保持').not.toBeNull()

    store.processEvent({ type: 'done' } as any)
    expect(store.pendingPlanApproval).toBeNull()
    expect(store.taskState).toBe('completed')
  })

  it('时间线渲染 plan_approval_required 条目（"计划待审批"徽标）', async () => {
    const { pinia, store } = setupStore()
    store.setTaskState('waiting_plan_approval')
    store.addMessage({
      role: 'assistant', content: '', loading: false, currentTool: '', thinkingText: '',
      planSteps: [], planId: '', progress: 0,
      events: [{
        type: 'plan_approval_required', content: '计划已生成（2 个步骤），等待审批',
        plan_id: 'p', task_id: 't',
      } as any],
    } as any)

    const wrapper = await mountView(pinia)
    expect(wrapper.find('.plan-standalone-badge').text()).toBe('计划待审批')
    expect(wrapper.find('.plan-standalone-text').text()).toContain('计划已生成')
    wrapper.unmount()
  })

  it('刷新恢复：GET task=waiting_plan_approval + plan_approval → 重建计划卡', async () => {
    localStorage.setItem('docmind.agent.lastTaskId', 't-restore-plan')
    apiMocks.getTask.mockResolvedValue({
      data: { success: true, data: { state: 'waiting_plan_approval', plan_approval: PENDING } },
    })
    const { pinia, store } = setupStore()

    const wrapper = await mountView(pinia)
    expect(store.taskState).toBe('waiting_plan_approval')
    expect(store.pendingPlanApproval?.goal).toBe('分析财报风险')
    expect(wrapper.findComponent(PlanApprovalCard).exists()).toBe(true)
    wrapper.unmount()
  })

  it('评审 MINOR-B：waiting_plan_approval 输入闸门——禁用 + 提示，发送/转向不触网', async () => {
    const { pinia, store } = setupStore()
    store.setTaskId('task-plan')
    store.setTaskState('waiting_plan_approval')
    store.setPendingPlanApproval(PENDING as any)
    apiMocks.getTask.mockResolvedValue({
      data: { success: true, data: { state: 'waiting_plan_approval', plan_approval: PENDING } },
    })

    const wrapper = await mountView(pinia)
    const ta = wrapper.find('.agent-textarea')
    expect(ta.attributes('disabled')).toBeDefined()
    expect(wrapper.find('.input-hint').text()).toContain('计划审批')
    expect(wrapper.find('.send-btn').attributes('disabled')).toBeDefined()

    // 尝试输入 + 回车：闸门拦下——不触发新聊天（会丢任务指针）也不触发转向
    await ta.setValue('试探输入')
    await ta.trigger('keydown.enter')
    await flushPromises()
    expect(apiMocks.chat).not.toHaveBeenCalled()
    expect(apiMocks.steerTask).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('四语键齐备：hitl.plan*（本特性新增键）', () => {
    const locales: Array<[string, Record<string, any>]> = [
      ['zh', zh], ['en', en], ['ja', ja], ['fr', fr],
    ]
    const keys = [
      'planTitle', 'planGoal', 'planSteps', 'planApprove', 'planEditSteps',
      'planSaveApprove', 'planCancelEdit', 'planReject', 'planRejectNotePlaceholder',
      'planRejectHint', 'planAddStep', 'planRemove', 'planDeps', 'planSubmitting',
      'planTimeline', 'planRejectedMessage', 'planEditInvalid', 'planFailed',
      'planPendingHint',
    ]
    for (const [code, msg] of locales) {
      const hitl = (msg as any).hitl
      expect(hitl, `${code} 缺 hitl 段`).toBeTruthy()
      for (const k of keys) {
        expect(hitl[k], `${code} 缺 hitl.${k}`).toBeTruthy()
      }
    }
  })
})
