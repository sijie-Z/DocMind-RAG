/**
 * E1 · 工作流编辑器「清空画布」防护（二次确认 + 自动保存空画布保护）。
 *
 * 事故面：清空按钮一键把画布清空，随后 500ms 防抖自动保存把空画布静默写回，
 * 覆盖服务端已保存版本——误点清空即不可恢复的静默数据丢失。删除最后一个节点
 * 走的是同一条自动保存路径，同样会写回空版本。
 *
 * 验收口径：
 *  1) 点清空未确认（取消）→ 画布与工作流关联均不变，自动保存不写回；
 *     确认文案必须写明「自动保存 N 秒内写回空画布、覆盖已保存的版本」；
 *  2) 确认清空 → 画布清空，但 500ms 自动保存被空画布保护拦截（节点=0 且
 *     已保存版本非空 → 不静默写回，改为明确提示）；
 *  3) 核心防护回退判别：删除最后一个节点（同一条自动保存路径）也不得写回空版本
 *     —— 去掉守卫该用例红；
 *  4) 防回退：非空画布的正常变更仍按 500ms 防抖自动保存，提示不出现。
 *
 * 测试相：mock useDialog 可控返回 cancel（取消）/ confirm（确认）；
 * mock useDedupedMessage 捕获 warning 文案；vi.useFakeTimers 推进 500ms 防抖。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory, type Router } from 'vue-router'
import naive from 'naive-ui'
import { createI18n } from 'vue-i18n'

// jsdom 缺口补齐（产品代码合法使用 ResizeObserver）
if (!('ResizeObserver' in globalThis)) {
  class RO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  ;(globalThis as unknown as { ResizeObserver: typeof RO }).ResizeObserver = RO
}

interface DialogOptions {
  title?: string
  content?: string
  positiveText?: string
  negativeText?: string
  onPositiveClick?: () => unknown
  onNegativeClick?: () => unknown
  onClose?: () => unknown
}

const { dialogState, dialogWarning } = vi.hoisted(() => {
  const state = {
    decision: 'cancel' as 'cancel' | 'confirm',
    calls: [] as DialogOptions[],
  }
  const warning = vi.fn((opts: DialogOptions) => {
    state.calls.push(opts)
    if (state.decision === 'confirm') opts.onPositiveClick?.()
    else opts.onNegativeClick?.()
  })
  return { dialogState: state, dialogWarning: warning }
})

// 只替换 useDialog（确认框交互），其余 naive-ui 组件走真实实现
vi.mock('naive-ui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('naive-ui')>()
  return {
    ...actual,
    useDialog: () => ({ warning: dialogWarning, info: vi.fn() }),
  }
})

// 捕获自动保存保护的明确提示（不依赖 toast DOM）
const { msgState } = vi.hoisted(() => ({
  msgState: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
    loading: vi.fn(),
  },
}))
vi.mock('@/utils/message', () => ({
  useDedupedMessage: () => msgState,
  default: () => msgState,
}))

vi.mock('@/api/workflow', () => ({
  getWorkflows: vi.fn().mockResolvedValue({ data: { data: { items: [], total: 0 } } }),
  getWorkflow: vi.fn().mockResolvedValue({ data: { data: null } }),
  createWorkflow: vi.fn().mockResolvedValue({ data: { data: { id: 1 } } }),
  updateWorkflow: vi.fn().mockResolvedValue({ data: { data: {} } }),
  executeWorkflow: vi.fn().mockResolvedValue({ data: { data: null } }),
  executeWorkflowStream: vi.fn(),
  deleteWorkflow: vi.fn().mockResolvedValue({}),
  getNodeDefinitions: vi.fn().mockResolvedValue({
    data: {
      data: [
        { id: 1, node_type: 'input', name: '输入节点', category: 'io', description: '工作流入口' },
        { id: 2, node_type: 'output', name: '输出节点', category: 'io', description: '工作流出口' },
      ],
    },
  }),
}))

vi.mock('@/api/agent', () => ({
  agentApi: { listSkills: vi.fn().mockResolvedValue({ data: { data: [] } }) },
}))

vi.mock('@/api/llmConfig', () => ({
  llmConfigApi: {
    list: vi.fn().mockResolvedValue({ data: { data: [] } }),
    providers: vi.fn().mockResolvedValue({ data: { data: [] } }),
  },
}))

import WorkflowEditor from '../editor.vue'
import { useWorkflowStore } from '@/stores/workflow'
import { createWorkflow, updateWorkflow } from '@/api/workflow'
import zh from '@/locales/zh'

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

const sharedPinia = createPinia()

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/workflow/editor', name: 'WorkflowEditor', component: { template: '<div />' } },
      { path: '/dashboard', name: 'Dashboard', component: { template: '<div />' } },
    ],
  })
}

async function mountEditor() {
  const router = makeRouter()
  await router.push('/workflow/editor')
  await router.isReady()
  const Host = defineComponent({
    render: () => h(WorkflowEditor),
  })
  const wrapper = mount(Host, {
    global: {
      plugins: [naive, sharedPinia, router, i18n],
      stubs: { VueFlow: true, Background: true, Controls: true, MiniMap: true },
    },
  })
  await flushPromises()
  await nextTick()
  return wrapper
}

type EditorVm = {
  saveWorkflow: () => Promise<boolean>
}

/**
 * 造出「已保存版本非空」的前置态：画布放 1 个节点并手动保存一次
 * （createWorkflow 路径 → currentWorkflowId=1，已持久化节点数=1）。
 * 保存前工作流尚无 id，画布变更不会触发自动保存。
 */
async function seedSavedWorkflow(wrapper: Awaited<ReturnType<typeof mountEditor>>) {
  const store = useWorkflowStore()
  store.workflowName = '测试流程'
  store.setNodes([
    { id: 'input_1', type: 'input', position: { x: 0, y: 0 }, data: { label: '输入', type: 'input' } },
  ])
  store.setEdges([])
  await nextTick()

  vi.mocked(createWorkflow).mockClear()
  const vm = wrapper.findComponent(WorkflowEditor).vm as unknown as EditorVm
  await vm.saveWorkflow()

  expect(store.currentWorkflowId).toBe(1)
  expect(vi.mocked(createWorkflow)).toHaveBeenCalledTimes(1)
  return store
}

beforeEach(() => {
  setActivePinia(sharedPinia)
  const store = useWorkflowStore()
  store.clearWorkflow()
  store.resetExecution()
  dialogState.decision = 'cancel'
  dialogState.calls.length = 0
  dialogWarning.mockClear()
  vi.mocked(updateWorkflow).mockClear()
  msgState.warning.mockClear()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('E1 · 清空画布二次确认', () => {
  it('点清空未确认（取消）→ 画布不变；确认文案写明自动保存 N 秒内覆盖已保存版本（先红）', async () => {
    const wrapper = await mountEditor()
    const store = await seedSavedWorkflow(wrapper)
    dialogState.decision = 'cancel'

    const clearBtn = wrapper.find('[data-testid="workflow-clear-canvas"]')
    expect(clearBtn.exists(), '清空按钮缺少稳定测试钩子 data-testid="workflow-clear-canvas"').toBe(true)
    await clearBtn.trigger('click')
    await flushPromises()

    // 必须先弹确认框
    expect(dialogWarning).toHaveBeenCalledTimes(1)
    const content = String(dialogState.calls[0]?.content ?? '')
    expect(content).toContain('0.5') // N 秒与 500ms 防抖同源
    expect(content).toContain('覆盖已保存的版本')

    // 取消 → 画布与工作流关联原样
    expect(store.nodes).toHaveLength(1)
    expect(store.currentWorkflowId).toBe(1)
    expect(vi.mocked(updateWorkflow)).not.toHaveBeenCalled()
  })

  it('确认清空 → 画布清空；500ms 自动保存被空画布保护拦截，不产生空版本（先红）', async () => {
    const wrapper = await mountEditor()
    const store = await seedSavedWorkflow(wrapper)
    dialogState.decision = 'confirm'
    vi.useFakeTimers()

    wrapper.find('[data-testid="workflow-clear-canvas"]').trigger('click')
    await nextTick()
    await nextTick()

    // 确认后：画布清空，当前工作流关联保留（清空的是画布，不是工作流）
    expect(store.nodes).toHaveLength(0)
    expect(store.currentWorkflowId).toBe(1)

    await vi.advanceTimersByTimeAsync(600)

    // 自动保存不得把空画布静默写回；改为明确提示
    expect(vi.mocked(updateWorkflow)).not.toHaveBeenCalled()
    expect(msgState.warning).toHaveBeenCalled()
    expect(String(msgState.warning.mock.calls[0]?.[0] ?? '')).toContain('已跳过自动保存')
  })
})

describe('E1 · 自动保存空画布保护（核心防护回退判别）', () => {
  it('删除最后一个节点（节点=0 且已保存版本非空）→ 自动保存跳过写回并提示（先红）', async () => {
    const wrapper = await mountEditor()
    const store = await seedSavedWorkflow(wrapper)
    vi.useFakeTimers()

    // 模拟逐个删节点删到 0：工作流关联仍在，自动保存会被触发
    store.setNodes([])
    await nextTick()
    await vi.advanceTimersByTimeAsync(600)

    expect(vi.mocked(updateWorkflow)).not.toHaveBeenCalled()
    expect(msgState.warning).toHaveBeenCalledTimes(1)
    expect(String(msgState.warning.mock.calls[0]?.[0] ?? '')).toContain('已跳过自动保存')
  })

  it('防回退：非空画布的正常变更仍按 500ms 防抖自动保存，且不触发保护提示', async () => {
    const wrapper = await mountEditor()
    const store = await seedSavedWorkflow(wrapper)
    vi.useFakeTimers()

    store.setNodes([
      ...store.nodes,
      { id: 'output_1', type: 'output', position: { x: 200, y: 0 }, data: { label: '输出', type: 'output' } },
    ])
    await nextTick()

    // 防抖窗口内不写
    await vi.advanceTimersByTimeAsync(499)
    expect(vi.mocked(updateWorkflow)).not.toHaveBeenCalled()

    // 500ms 到点 → 正常自动保存
    await vi.advanceTimersByTimeAsync(2)
    expect(vi.mocked(updateWorkflow)).toHaveBeenCalledTimes(1)
    const payload = vi.mocked(updateWorkflow).mock.calls[0][1] as { flow_data?: { nodes: unknown[] } }
    expect(payload.flow_data?.nodes).toHaveLength(2)
    expect(msgState.warning).not.toHaveBeenCalled()
  })
})
