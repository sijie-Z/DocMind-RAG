/**
 * AgentView · 回放消费面接线（入口 / 时间线 dock / 深链 / 任务记录）。
 *
 * 与 hitlApproval.test.ts 同款驱动：mock api 模块 + 真 AgentInput/ReplayTimeline
 * 挂载视图；本文件额外装真 vue-router（深链 ?id= 是通知跳转的落点约定：
 * 通知 target_route="Agent" + target_id → router.push({name:'Agent', query:{id}})
 * → 本页读取 query.id 直达回放）。
 *
 * 变异点：
 * - 不读 route.query.id（深链不打开回放）→ 深链用例红；
 * - 完成运行后不亮入口 / 不回读 has_replay → 入口用例红；
 * - 404 不落诚实空态（编造空轨迹）→ 空态用例红。
 *
 * 契约纪律（SEAM-3 教训）：mock 形状 = 真实拦截器输出（**完整 AxiosResponse**，
 * 信封在 res.data = {success, data}，消费者读 res.data.data）——mock 跟着错误
 * 假设走会放跑"入口永远不亮"的运行时 bug。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { NMessageProvider, NDialogProvider, NSpin, NButton, NTooltip, NIcon, NPopover } from 'naive-ui'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import i18n from '@/locales'

import AgentView from '../index.vue'
import AgentInput from '@/components/agent/AgentInput.vue'
import ReplayTimeline from '@/components/agent/ReplayTimeline.vue'

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
  listTasks: vi.fn().mockResolvedValue({ data: { success: true, data: { tasks: [], total: 0, page: 1, page_size: 10 } } }),
  getReplay: vi.fn(),
}))

vi.mock('@/api/agent', () => ({ agentApi: apiMocks }))

const Host = defineComponent({
  render: () => h(NMessageProvider, null, { default: () => h(NDialogProvider, null, { default: () => h(AgentView) }) }),
})

const TIMELINE = {
  task_id: 'abc123def456',
  query: '分析文档',
  goal: '产出结论',
  plan_summary: '检索 → 汇总',
  steps_completed: 1,
  steps: [
    {
      step_id: 's1', description: '检索资料', tool_used: 'search_knowledge_base',
      status: 'completed', result_summary: '找到 3 条', error: null,
      duration_ms: 125.5, tokens_used: 42,
    },
  ],
  findings: [],
  decisions: [],
  failures: [],
  duration_ms: 1234.5,
  total_tokens: 42,
  progress: 1,
  start_time: 0,
}

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
  // 注意：AgentInput / ReplayTimeline 不 stub（交互要在真组件上做）
}

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'Home', component: { template: '<div />' } },
      { path: '/agent', name: 'Agent', component: { template: '<div />' } },
    ],
  })
}

async function mountView(pinia: ReturnType<typeof createPinia>, router: ReturnType<typeof makeRouter>) {
  const wrapper = mount(Host, {
    global: {
      plugins: [pinia, i18n, router],
      components: { NSpin, NButton, NTooltip, NIcon, NPopover },
      stubs,
    },
  })
  await flushPromises()
  return wrapper
}

async function setupAt(route: string) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = makeRouter()
  await router.push(route)
  await router.isReady()
  const wrapper = await mountView(pinia, router)
  return { pinia, router, wrapper }
}

describe('AgentView · 回放消费面', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
    apiMocks.getTask.mockResolvedValue({ data: { success: true, data: { state: 'completed' } } })
    apiMocks.listTasks.mockResolvedValue({ data: { success: true, data: { tasks: [], total: 0, page: 1, page_size: 10 } } })
  })

  it('深链 ?id= → 挂载即打开回放时间线（通知点击的落点）', async () => {
    apiMocks.getReplay.mockResolvedValue({ data: { success: true, data: TIMELINE } })
    const { router, wrapper } = await setupAt('/agent?id=deep-1')

    expect(apiMocks.getReplay).toHaveBeenCalledWith('deep-1')
    const panel = wrapper.findComponent(ReplayTimeline)
    expect(panel.exists(), '深链必须直接打开回放面板').toBe(true)
    expect(panel.text()).toContain('检索资料')
    expect(router.currentRoute.value.query.id).toBe('deep-1')
    wrapper.unmount()
  })

  it('深链指向无数据任务 → 诚实空态（不编造轨迹）', async () => {
    apiMocks.getReplay.mockRejectedValueOnce({ response: { status: 404 } })
    const { wrapper } = await setupAt('/agent?id=gone-1')

    const panel = wrapper.findComponent(ReplayTimeline)
    expect(panel.exists()).toBe(true)
    expect(panel.text()).toContain('该任务没有可回放的数据')
    expect(panel.findAll('.replay-step')).toHaveLength(0)
    wrapper.unmount()
  })

  it('页面已挂载时深链切换任务必须响应（F2 判别：同路由二次导航）', async () => {
    // 变异点：深链只在 onMounted 处理（无 route watch）→ 二次导航不切换 → 本用例红
    apiMocks.getReplay.mockImplementation(async (taskId: string) => ({
      data: {
        success: true,
        data: {
          ...TIMELINE,
          task_id: taskId,
          steps: [{ ...TIMELINE.steps[0], description: `任务-${taskId}` }],
        },
      },
    }))
    const { router, wrapper } = await setupAt('/agent?id=first-1')
    expect(wrapper.findComponent(ReplayTimeline).text()).toContain('任务-first-1')

    // 同路由仅 query 变化（组件不重挂）：等价于页面已打开时再点另一条通知
    await router.push('/agent?id=second-2')
    await flushPromises()

    expect(apiMocks.getReplay).toHaveBeenCalledWith('second-2')
    expect(wrapper.findComponent(ReplayTimeline).text()).toContain('任务-second-2')
    expect(router.currentRoute.value.query.id).toBe('second-2')
    wrapper.unmount()
  })

  it('完成运行亮「查看回放」入口；点击打开时间线并写入 URL（可刷新直达；SEAM-3 判别：信封 mock 下入口必须真亮）', async () => {
    apiMocks.chat.mockImplementation(async (_q: string, onEvent: (e: any) => void) => {
      onEvent({ type: 'thinking', content: '可用工具: 3 个', task_id: 'run-1' })
      onEvent({ type: 'chunk', content: '最终答案' })
      onEvent({ type: 'done', content: '' })
      return { abort: () => {} }
    })
    apiMocks.getTask.mockResolvedValue({ data: { success: true, data: { state: 'completed', has_replay: true } } })
    apiMocks.getReplay.mockResolvedValue({ data: { success: true, data: TIMELINE } })
    const { router, wrapper } = await setupAt('/agent')

    wrapper.findComponent(AgentInput).vm.$emit('send', '分析文档')
    await flushPromises()
    await flushPromises()

    const entry = wrapper.find('.replay-entry-btn')
    expect(entry.exists(), '有回放的完成运行必须亮入口').toBe(true)
    await entry.trigger('click')
    await flushPromises()

    expect(apiMocks.getReplay).toHaveBeenCalledWith('run-1')
    expect(wrapper.findComponent(ReplayTimeline).exists()).toBe(true)
    expect(router.currentRoute.value.query.id).toBe('run-1')
    wrapper.unmount()
  })

  it('无回放的运行（has_replay=false）不亮入口（不虚标、不空跳）', async () => {
    apiMocks.chat.mockImplementation(async (_q: string, onEvent: (e: any) => void) => {
      onEvent({ type: 'thinking', content: '可用工具: 3 个', task_id: 'run-2' })
      onEvent({ type: 'done', content: '' })
      return { abort: () => {} }
    })
    apiMocks.getTask.mockResolvedValue({ data: { success: true, data: { state: 'failed', has_replay: false } } })
    const { wrapper } = await setupAt('/agent')

    wrapper.findComponent(AgentInput).vm.$emit('send', '分析文档')
    await flushPromises()
    await flushPromises()

    expect(apiMocks.getTask).toHaveBeenCalledWith('run-2')
    expect(wrapper.find('.replay-entry-btn').exists()).toBe(false)
    wrapper.unmount()
  })

  it('关闭回放面板并清掉 URL 深链', async () => {
    apiMocks.getReplay.mockResolvedValue({ data: { success: true, data: TIMELINE } })
    const { router, wrapper } = await setupAt('/agent?id=deep-2')

    expect(wrapper.findComponent(ReplayTimeline).exists()).toBe(true)
    await wrapper.find('.replay-close').trigger('click')
    await flushPromises()

    expect(wrapper.findComponent(ReplayTimeline).exists()).toBe(false)
    expect(router.currentRoute.value.query.id).toBeUndefined()
    wrapper.unmount()
  })

  it('任务记录按钮打开时拉取任务列表（has_replay 消费面）', async () => {
    const { wrapper } = await setupAt('/agent')

    const taskBtn = wrapper
      .findAll('button.header-btn')
      .find((b) => b.text().includes('任务记录'))
    expect(taskBtn, '头部必须有任务记录入口').toBeTruthy()
    await taskBtn!.trigger('click')
    await flushPromises()

    expect(apiMocks.listTasks).toHaveBeenCalled()
    wrapper.unmount()
  })
})
