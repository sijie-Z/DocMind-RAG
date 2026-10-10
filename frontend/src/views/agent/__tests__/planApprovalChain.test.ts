/**
 * MAJOR（评审回炉）：计划审批**真 api→view 链**——只 mock 传输层。
 *
 * 链路：AgentView（真）→ agentApi（真，含 fetch 直发与响应形态分支）
 *      → 传输层替身（request 模块 = axios 通道；global fetch = SSE/JSON 通道）。
 * F2 先例（hitlRestoreReconnect）只 mock request；本文件对 SSE/JSON 端点
 * 再补 fetch 替身——decidePlanApproval 的请求体与响应解析不再被 api 层替身
 * 掩盖。三决策（approve / edit / reject）各一条，从卡片交互一路断言到
 * 真实请求体与流消费后的现场收敛。
 *
 * 变异点：视图改传错 decision / 丢 edited_steps / 不消费 SSE → 对应用例红。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { NMessageProvider, NSpin, NButton, NTooltip, NIcon } from 'naive-ui'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'

import zh from '@/locales/zh'
import AgentView from '../index.vue'
import PlanApprovalCard from '@/components/agent/PlanApprovalCard.vue'
import { useAgentStore } from '@/stores/agent'

// 只 mock request 模块（axios 通道）；agentApi / 视图 / store 全走真实代码。
const requestMocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
}))

vi.mock('@/utils/request', () => ({ default: requestMocks }))

const i18n = createI18n({ legacy: false, locale: 'zh', fallbackLocale: 'zh', messages: { zh } })

const Host = defineComponent({
  render: () => h(NMessageProvider, null, { default: () => h(AgentView) }),
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
}

const PENDING = {
  plan_id: 'plan-1',
  goal: '分析财报风险',
  steps: [
    { id: 's1', description: '检索相关文档', dependencies: [], tool_hint: 'search_knowledge_base' },
    { id: 's2', description: '综合分析', dependencies: ['s1'], tool_hint: null },
  ],
}

function _envelope(data: unknown) {
  return { data: { success: true, data } }
}

function stubResponse(bodyText: string, contentType: string, status = 200) {
  const encoder = new TextEncoder()
  let consumed = false
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: {
      get: (k: string) => (k.toLowerCase() === 'content-type' ? contentType : null),
    },
    json: async () => JSON.parse(bodyText),
    body: {
      getReader: () => ({
        read: async () => {
          if (consumed) return { done: true, value: undefined }
          consumed = true
          return { done: false, value: encoder.encode(bodyText) }
        },
      }),
    },
  } as unknown as Response
}

function sseBody(events: object[]): string {
  return events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('')
}

async function mountView(pinia: ReturnType<typeof createPinia>) {
  const wrapper = mount(Host, {
    global: { plugins: [pinia, i18n], components: { NSpin, NButton, NTooltip, NIcon }, stubs },
  })
  await flushPromises()
  return wrapper
}

/** 建现场：GET task 走 request 替身（真 restorePendingApproval 解包链重建卡片）。 */
async function setupScene() {
  localStorage.setItem('docmind.agent.lastTaskId', 't-chain')
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useAgentStore()
  const wrapper = await mountView(pinia)
  return { wrapper, store }
}

describe('AgentView · 计划审批真链（只 mock 传输层）', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
    fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    requestMocks.get.mockImplementation((url: string) => {
      if (url === '/agent/tools') return Promise.resolve(_envelope([]))
      if (url === '/agent/sessions') {
        return Promise.resolve(_envelope({ sessions: [], total: 0, page: 1, page_size: 20 }))
      }
      if (url === '/agent/config') return Promise.resolve(_envelope({}))
      if (url.startsWith('/agent/tasks/t-chain')) {
        return Promise.resolve(_envelope({
          task_id: 't-chain',
          state: 'waiting_plan_approval',
          is_recoverable: true,
          progress: 0,
          completed_steps: 0,
          total_steps: 2,
          current_step_id: null,
          last_error: null,
          last_error_type: null,
          query: '分析财报风险',
          plan_approval: PENDING,
        }))
      }
      return Promise.resolve(_envelope(null))
    })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function lastFetchRequest() {
    const [url, init] = fetchMock.mock.calls[fetchMock.mock.calls.length - 1]
    return { url: String(url), init }
  }

  it('approve：卡片点击 → 真 decidePlanApproval → 请求体与流消费（现场收敛 completed）', async () => {
    fetchMock.mockResolvedValue(
      stubResponse(sseBody([{ type: 'done', content: '', task_id: 't-chain' }]), 'text/event-stream'),
    )
    const { wrapper, store } = await setupScene()
    expect(wrapper.findComponent(PlanApprovalCard).exists()).toBe(true)

    await wrapper.findComponent(PlanApprovalCard).find('.approve-btn').trigger('click')
    await flushPromises()

    const { url, init } = lastFetchRequest()
    expect(url).toMatch(/\/agent\/tasks\/t-chain\/plan-approval$/)
    expect(init.method).toBe('POST')
    expect(init.headers['X-Silent-Error']).toBe('1')
    expect(JSON.parse(init.body)).toEqual({ decision: 'approve' })
    // 真流消费：done 事件经 store 收敛现场
    expect(store.taskState).toBe('completed')
    expect(store.pendingPlanApproval).toBeNull()
    wrapper.unmount()
  })

  it('edit：编辑步骤后批准 → 真请求携带 edited_steps（id 保留、描述为编辑值）', async () => {
    fetchMock.mockResolvedValue(
      stubResponse(sseBody([{ type: 'done', content: '' }]), 'text/event-stream'),
    )
    const { wrapper } = await setupScene()

    await wrapper.findComponent(PlanApprovalCard).find('.edit-btn').trigger('click')
    await wrapper.findComponent(PlanApprovalCard).findAll('.step-input')[0].setValue('链上编辑后的第一步')
    await wrapper.findComponent(PlanApprovalCard).find('.approve-btn').trigger('click')
    await flushPromises()

    const { init } = lastFetchRequest()
    const body = JSON.parse(init.body)
    expect(body.decision).toBe('edit')
    expect(body.edited_steps[0]).toEqual({
      id: 's1',
      description: '链上编辑后的第一步',
      dependencies: [],
      tool_hint: 'search_knowledge_base',
    })
    expect(body.edited_steps[1].description).toBe('综合分析')
    wrapper.unmount()
  })

  it('reject：填理由 → 真请求携带 note；流内再暂停时报文保持等待态', async () => {
    fetchMock.mockResolvedValue(
      stubResponse(
        sseBody([
          { type: 'plan_approval_required', content: '计划已生成（1 个步骤），等待审批', plan_id: 'plan-2', plan_goal: '目标', plan_steps: PENDING.steps },
          { type: 'done', content: '', task_state: 'waiting_plan_approval' },
        ]),
        'text/event-stream',
      ),
    )
    const { wrapper, store } = await setupScene()

    await wrapper.findComponent(PlanApprovalCard).find('.note-input').setValue('请合并为一次检索')
    await wrapper.findComponent(PlanApprovalCard).find('.reject-btn').trigger('click')
    await flushPromises()

    const { init } = lastFetchRequest()
    expect(JSON.parse(init.body)).toEqual({ decision: 'reject', note: '请合并为一次检索' })
    // 重规划后的新计划再次抵达：现场保持等待（卡片数据来自事件自足载荷）
    expect(store.taskState).toBe('waiting_plan_approval')
    expect(store.pendingPlanApproval?.plan_id).toBe('plan-2')
    wrapper.unmount()
  })
})
