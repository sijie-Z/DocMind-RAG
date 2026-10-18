/**
 * E10 · 工作流保存反馈（B8）：保存状态永远真实可见，失败不沉默。
 *
 * 事故面：
 *  - 自动保存失败被静默吞掉（原实现 catch 注释 "Auto-save failures are silent"），
 *    用户以为改动已落库，服务端实际从未收到；
 *  - 界面没有常驻保存状态：手动保存成功无确认、自动保存失败无提示，
 *    成功与失败在 UI 上完全不可区分（「已保存」只能靠猜）。
 *
 * 验收口径：
 *  1) 自动保存失败 → 出现可见错误提示，状态点进入「保存失败」，
 *     不得显示「已保存」（失败不得误报成功）；
 *  2) 连续失败 → 提示按节流窗口只出现一次（不重复打扰）；
 *  3) 失败后点击状态点可重试，重试成功 → 状态点恢复「已保存」；
 *  4) 防回退：手动保存成功 → 一次成功 toast + 状态点「已保存」；
 *     自动保存成功 → 状态点更新为「已保存」但不弹 toast（不打扰）。
 *
 * 测试相：mock @/utils/message 捕获 toast；mock updateWorkflow 注入失败；
 * vi.useFakeTimers 推进 500ms 自动保存防抖窗口。
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

// 只替换 useDialog（编辑器 setup 依赖；本用例不操作确认框），其余组件走真实实现
vi.mock('naive-ui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('naive-ui')>()
  return {
    ...actual,
    useDialog: () => ({ warning: vi.fn(), info: vi.fn() }),
  }
})

// 捕获保存反馈 toast（不依赖 toast DOM）
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
  saveStatus: 'unsaved' | 'saving' | 'saved' | 'error'
  retrySave: () => Promise<void>
}

function statusEl(wrapper: Awaited<ReturnType<typeof mountEditor>>) {
  return wrapper.find('[data-testid="workflow-save-status"]')
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

/** 触发一次画布变更 → 推进 500ms 防抖 → 自动保存请求 */
async function triggerAutoSave(store: ReturnType<typeof useWorkflowStore>) {
  store.setNodes([
    ...store.nodes,
    { id: `output_${Date.now()}`, type: 'output', position: { x: 200, y: 0 }, data: { label: '输出', type: 'output' } },
  ])
  await nextTick()
  await vi.advanceTimersByTimeAsync(600)
  await flushPromises()
}

beforeEach(() => {
  setActivePinia(sharedPinia)
  const store = useWorkflowStore()
  store.clearWorkflow()
  store.resetExecution()
  vi.mocked(updateWorkflow).mockClear()
  vi.mocked(createWorkflow).mockClear()
  msgState.success.mockClear()
  msgState.error.mockClear()
  msgState.warning.mockClear()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('E10 · 自动保存失败不沉默', () => {
  it('失败注入 → 可见错误提示，状态点进入「保存失败」且不显示「已保存」（先红）', async () => {
    const wrapper = await mountEditor()
    const store = await seedSavedWorkflow(wrapper)
    expect(statusEl(wrapper).attributes('data-status')).toBe('saved')
    msgState.success.mockClear() // seed 的手动保存成功 toast 不计入失败路径断言

    vi.useFakeTimers()
    vi.mocked(updateWorkflow).mockRejectedValueOnce(new Error('network down'))
    await triggerAutoSave(store)

    // 请求真实发生且被拒绝
    expect(vi.mocked(updateWorkflow)).toHaveBeenCalledTimes(1)
    // 可见提示：自动保存失败不得静默
    expect(msgState.error).toHaveBeenCalled()
    expect(String(msgState.error.mock.calls[0]?.[0] ?? '')).toContain('自动保存失败')
    // 状态点不得误报成功
    const el = statusEl(wrapper)
    expect(el.exists(), '缺少常驻保存状态点 data-testid="workflow-save-status"').toBe(true)
    expect(el.attributes('data-status')).toBe('error')
    expect(el.text()).toContain('保存失败')
    expect(el.text()).not.toContain('已保存')
    // 失败路径不得弹成功 toast
    expect(msgState.success).not.toHaveBeenCalled()
  })

  it('连续失败 → 错误提示按节流窗口只出现一次（不重复打扰）', async () => {
    const wrapper = await mountEditor()
    const store = await seedSavedWorkflow(wrapper)

    vi.useFakeTimers()
    vi.mocked(updateWorkflow).mockRejectedValueOnce(new Error('first failure'))
    await triggerAutoSave(store)
    expect(msgState.error).toHaveBeenCalledTimes(1)

    vi.mocked(updateWorkflow).mockRejectedValueOnce(new Error('second failure'))
    await triggerAutoSave(store)

    expect(vi.mocked(updateWorkflow)).toHaveBeenCalledTimes(2)
    // 两次失败间隔在节流窗口内 → 提示不重复弹
    expect(msgState.error).toHaveBeenCalledTimes(1)
    expect(statusEl(wrapper).attributes('data-status')).toBe('error')
  })

  it('失败后点击状态点可重试；重试成功 → 状态点恢复「已保存」并给出成功 toast', async () => {
    const wrapper = await mountEditor()
    const store = await seedSavedWorkflow(wrapper)

    vi.useFakeTimers()
    vi.mocked(updateWorkflow).mockRejectedValueOnce(new Error('network down'))
    await triggerAutoSave(store)
    expect(statusEl(wrapper).attributes('data-status')).toBe('error')
    msgState.success.mockClear()

    // 下一次 updateWorkflow 走 mock 默认实现（resolve）
    await statusEl(wrapper).trigger('click')
    await flushPromises()

    expect(vi.mocked(updateWorkflow)).toHaveBeenCalledTimes(2)
    expect(statusEl(wrapper).attributes('data-status')).toBe('saved')
    expect(statusEl(wrapper).text()).toContain('已保存')
    expect(msgState.success).toHaveBeenCalledTimes(1)
  })
})

describe('E10 · 保存成功反馈（防回退）', () => {
  it('状态点常驻：新编辑器初始为「未保存」；手动保存成功 → 一次成功 toast + 「已保存」', async () => {
    const wrapper = await mountEditor()
    const store = useWorkflowStore()
    expect(statusEl(wrapper).exists(), '保存状态点必须常驻工具栏').toBe(true)
    expect(statusEl(wrapper).attributes('data-status')).toBe('unsaved')
    expect(statusEl(wrapper).text()).toContain('未保存')

    store.workflowName = '测试流程'
    store.setNodes([
      { id: 'input_1', type: 'input', position: { x: 0, y: 0 }, data: { label: '输入', type: 'input' } },
    ])
    await nextTick()

    const vm = wrapper.findComponent(WorkflowEditor).vm as unknown as EditorVm
    await vm.saveWorkflow()

    expect(vi.mocked(createWorkflow)).toHaveBeenCalledTimes(1)
    expect(msgState.success).toHaveBeenCalledTimes(1)
    expect(String(msgState.success.mock.calls[0]?.[0] ?? '')).toContain('已保存')
    expect(statusEl(wrapper).attributes('data-status')).toBe('saved')
  })

  it('自动保存成功 → 状态点先「未保存」后「已保存」，但不弹成功 toast（不打扰）', async () => {
    const wrapper = await mountEditor()
    const store = await seedSavedWorkflow(wrapper)
    msgState.success.mockClear()

    vi.useFakeTimers()
    store.setNodes([
      ...store.nodes,
      { id: 'output_1', type: 'output', position: { x: 200, y: 0 }, data: { label: '输出', type: 'output' } },
    ])
    await nextTick()

    // 防抖窗口内：改动未落库 → 状态点如实显示未保存
    await vi.advanceTimersByTimeAsync(499)
    expect(vi.mocked(updateWorkflow)).not.toHaveBeenCalled()
    expect(statusEl(wrapper).attributes('data-status')).toBe('unsaved')

    // 到点自动保存成功 → 状态点更新，且不打扰（无 toast）
    await vi.advanceTimersByTimeAsync(2)
    await flushPromises()
    expect(vi.mocked(updateWorkflow)).toHaveBeenCalledTimes(1)
    expect(statusEl(wrapper).attributes('data-status')).toBe('saved')
    expect(msgState.success).not.toHaveBeenCalled()
    expect(msgState.error).not.toHaveBeenCalled()
  })
})
