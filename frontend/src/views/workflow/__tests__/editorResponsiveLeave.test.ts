/**
 * E12 · 工作流编辑器响应式 / 触屏添加 / 离开保护。
 *
 * 事故面：
 *  - 工具栏单行固定高（h-14）且不换行：1440×900 下列宽 = 1440 − 288（节点库）
 *    − 320（属性面板）= 832px，而工具栏左组内容 ≈ 886px → 横向溢出；
 *  - 三栏固定宽度、不可折叠：窄屏（<1280）画布被两侧面板挤没；
 *  - 节点只能从节点库拖拽添加：触屏没有 HTML5 drag 事件 → 无法添加节点；
 *  - 离开编辑器（路由离开 / 关闭标签页）无任何保护：未保存改动直接丢失。
 *
 * 验收口径：
 *  1) 1440×900（宽屏）：属性面板可见；工具栏允许换行（min-h + flex-wrap，
 *     不再是固定 h-14 单行）；中间画布列可收缩（min-w-0）、两侧固定列
 *     shrink-0 —— 这是「不横向溢出」的结构前提（真实像素级复验待栈恢复）；
 *  2) <1280（窄屏）：属性面板默认收起；点工具栏开关以覆盖层展开；
 *  3) 触屏：点击节点卡片即可添加节点（tap = click），拖拽路径保留（防回退）；
 *  4) 离开保护：dirty 时路由离开弹确认（取消留下 / 确认离开），已保存 /
 *     无改动时静默放行；dirty 时 beforeunload 被 preventDefault。
 *
 * 测试相：window.matchMedia 可控（模拟 1440×900 与 1024 宽）；
 * mock useDialog 捕获确认框并可控制 positive/negative；组件必须经
 * <RouterView> 渲染，onBeforeRouteLeave 才能挂到路由记录上。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory, RouterView, type Router } from 'vue-router'
import naive from 'naive-ui'
import { createI18n } from 'vue-i18n'

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
        { id: 3, node_type: 'llm_deepseek', name: 'DeepSeek', category: 'llm', description: '' },
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
import zh from '@/locales/zh'

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

const sharedPinia = createPinia()

// ── matchMedia 可控桩：模拟 1440×900（宽）与 1024×900（窄）──
const mediaState = {
  wide: true,
  listeners: new Set<(ev: { matches: boolean }) => void>(),
}

function setViewport(wide: boolean) {
  mediaState.wide = wide
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: wide ? 1440 : 1024 })
  Object.defineProperty(window, 'innerHeight', { configurable: true, value: 900 })
  mediaState.listeners.forEach((cb) => cb({ matches: wide }))
}

function installMatchMedia() {
  mediaState.listeners.clear()
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    configurable: true,
    value: (query: string): MediaQueryList => {
      const isMinWidth = query.includes('min-width')
      return {
        media: query,
        get matches() {
          return isMinWidth ? mediaState.wide : !mediaState.wide
        },
        onchange: null,
        addListener: (cb: (ev: { matches: boolean }) => void) => mediaState.listeners.add(cb),
        removeListener: (cb: (ev: { matches: boolean }) => void) => mediaState.listeners.delete(cb),
        addEventListener: (_: string, cb: (ev: { matches: boolean }) => void) =>
          mediaState.listeners.add(cb),
        removeEventListener: (_: string, cb: (ev: { matches: boolean }) => void) =>
          mediaState.listeners.delete(cb),
        dispatchEvent: () => false,
      } as unknown as MediaQueryList
    },
  })
}

const mountedWrappers: VueWrapper[] = []

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/workflow/editor', name: 'WorkflowEditor', component: WorkflowEditor },
      { path: '/dashboard', name: 'Dashboard', component: { template: '<div>dashboard</div>' } },
    ],
  })
}

async function mountEditor() {
  installMatchMedia()
  const router = makeRouter()
  await router.push('/workflow/editor')
  await router.isReady()
  // 必须经 RouterView 渲染：onBeforeRouteLeave 依赖最近 RouterView 提供的
  // matchedRouteKey injection，直接 mount 组件时守卫不会被挂到路由记录上。
  const Host = defineComponent({ render: () => h(RouterView) })
  const wrapper = mount(Host, {
    global: {
      plugins: [naive, sharedPinia, router, i18n],
      stubs: { VueFlow: true, Background: true, Controls: true, MiniMap: true },
    },
  })
  mountedWrappers.push(wrapper)
  await flushPromises()
  await nextTick()
  return { wrapper, router }
}

type EditorVm = {
  saveWorkflow: () => Promise<boolean>
  onDragStart: (e: DragEvent, node: unknown) => void
  onDrop: (e: DragEvent) => void
}

function editorVm(wrapper: VueWrapper): EditorVm {
  return wrapper.findComponent(WorkflowEditor).vm as unknown as EditorVm
}

function addDirtyNode() {
  const store = useWorkflowStore()
  store.setNodes([
    { id: 'input_1', type: 'input', position: { x: 0, y: 0 }, data: { label: '输入', type: 'input' } },
  ])
  return store
}

function dispatchBeforeUnload(): Event {
  const ev = new Event('beforeunload', { cancelable: true })
  window.dispatchEvent(ev)
  return ev
}

/**
 * v-show 的可见性判定：直接看内联 display。
 * jsdom 的 getComputedStyle 在同一 tick 内（v-show 刚切换后）会读到缓存的旧值，
 * isVisible() 因此不稳定；内联 style 是 v-show 的真实输出，判定确定。
 */
function isShown(el: { attributes: (name: string) => string | undefined; element: Element }): boolean {
  const style = el.attributes('style') ?? ''
  if (/display:\s*none/.test(style)) return false
  return (el.element as HTMLElement).style.display !== 'none'
}

beforeEach(() => {
  setActivePinia(sharedPinia)
  const store = useWorkflowStore()
  store.clearWorkflow()
  store.resetExecution()
  dialogState.decision = 'cancel'
  dialogState.calls.length = 0
  dialogWarning.mockClear()
  msgState.success.mockClear()
  msgState.error.mockClear()
  msgState.warning.mockClear()
  msgState.info.mockClear()
})

afterEach(() => {
  // 逐个卸载：onUnmounted 会移除 beforeunload / matchMedia 监听，
  // 避免上一个用例的 dirty 监听污染后续用例。
  mountedWrappers.splice(0).forEach((w) => w.unmount())
})

describe('E12 · 1440×900 布局（宽屏三栏）', () => {
  it('属性面板可见；工具栏可换行、画布列可收缩（无横向溢出的结构前提）（先红）', async () => {
    setViewport(true)
    const { wrapper } = await mountEditor()

    const panel = wrapper.find('[data-testid="workflow-property-panel"]')
    expect(panel.exists(), '缺少属性面板稳定钩子 data-testid="workflow-property-panel"').toBe(true)
    expect(isShown(panel)).toBe(true)

    const toolbar = wrapper.find('[data-testid="workflow-toolbar"]')
    expect(toolbar.exists(), '缺少工具栏稳定钩子 data-testid="workflow-toolbar"').toBe(true)
    // 溢出根因：固定 h-14 单行 + 不换行；修复后 min-h-14 + flex-wrap
    expect(toolbar.classes()).toContain('flex-wrap')
    expect(toolbar.classes()).toContain('min-h-14')
    expect(toolbar.classes()).not.toContain('h-14')

    // flex 行溢出防护：两侧固定列 shrink-0，中间画布列 min-w-0 可收缩
    const library = wrapper.find('[data-testid="workflow-node-library"]')
    expect(library.exists(), '缺少节点库稳定钩子 data-testid="workflow-node-library"').toBe(true)
    expect(library.classes()).toContain('shrink-0')
    expect(panel.classes()).toContain('shrink-0')

    const canvasCol = wrapper.find('[data-testid="workflow-canvas-column"]')
    expect(canvasCol.exists(), '缺少画布列稳定钩子 data-testid="workflow-canvas-column"').toBe(true)
    expect(canvasCol.classes()).toContain('min-w-0')
  })

  it('宽度跨过 1280 断点：窄屏自动收起面板，回到宽屏自动展开', async () => {
    setViewport(true)
    const { wrapper } = await mountEditor()
    const panel = wrapper.find('[data-testid="workflow-property-panel"]')
    expect(isShown(panel)).toBe(true)

    setViewport(false)
    await nextTick()
    expect(isShown(panel)).toBe(false)

    setViewport(true)
    await nextTick()
    expect(isShown(panel)).toBe(true)
  })
})

describe('E12 · 窄屏（<1280）属性面板折叠', () => {
  it('面板默认收起；点工具栏开关以覆盖层展开（先红）', async () => {
    setViewport(false)
    const { wrapper } = await mountEditor()

    const panel = wrapper.find('[data-testid="workflow-property-panel"]')
    expect(panel.exists()).toBe(true)
    expect(isShown(panel)).toBe(false)

    const toggle = wrapper.find('[data-testid="workflow-panel-toggle"]')
    expect(toggle.exists(), '缺少属性面板开关 data-testid="workflow-panel-toggle"').toBe(true)
    await toggle.trigger('click')
    await nextTick()

    expect(isShown(panel)).toBe(true)
    // 覆盖式（fixed），不挤占画布
    expect(panel.classes()).toContain('fixed')

    await toggle.trigger('click')
    await nextTick()
    expect(isShown(panel)).toBe(false)
  })
})

describe('E12 · 触屏点击添加节点', () => {
  it('点击节点卡片即可添加节点（tap = click），新节点被选中；拖拽路径保留（防回退）（先红）', async () => {
    setViewport(true)
    const { wrapper } = await mountEditor()
    const store = useWorkflowStore()
    const vm = editorVm(wrapper)

    const card = wrapper.find('[data-testid="node-card-input"]')
    expect(card.exists(), '节点卡片缺少稳定钩子 data-testid="node-card-<type>"').toBe(true)
    await card.trigger('click')

    expect(store.nodes).toHaveLength(1)
    expect(store.nodes[0].type).toBe('input')
    expect(store.selectedNode?.id).toBe(store.nodes[0].id)

    // 防回退：拖拽添加仍可用
    const dt = {
      data: '',
      setData(_type: string, value: string) {
        this.data = value
      },
      getData() {
        return this.data
      },
      effectAllowed: '',
    }
    vm.onDragStart(
      { dataTransfer: dt } as unknown as DragEvent,
      { node_type: 'output', name: '输出节点' },
    )
    vm.onDrop({
      dataTransfer: dt,
      clientX: 10,
      clientY: 10,
      target: { getBoundingClientRect: () => ({ left: 0, top: 0 }) },
    } as unknown as DragEvent)

    expect(store.nodes).toHaveLength(2)
    expect(store.nodes[1].type).toBe('output')
  })
})

describe('E12 · 离开保护（onBeforeRouteLeave + beforeunload）', () => {
  it('dirty 路由离开：弹确认；取消留在编辑器，确认后才离开（先红）', async () => {
    setViewport(true)
    const { router } = await mountEditor()
    addDirtyNode()
    await nextTick()

    dialogState.decision = 'cancel'
    await router.push('/dashboard')
    await flushPromises()

    expect(dialogWarning).toHaveBeenCalledTimes(1)
    expect(String(dialogState.calls[0]?.content ?? '')).toContain('未保存')
    expect(router.currentRoute.value.path).toBe('/workflow/editor')

    dialogState.decision = 'confirm'
    await router.push('/dashboard')
    await flushPromises()

    expect(router.currentRoute.value.path).toBe('/dashboard')
  })

  it('无改动离开：不弹确认，静默放行', async () => {
    setViewport(true)
    const { router } = await mountEditor()

    await router.push('/dashboard')
    await flushPromises()

    expect(dialogWarning).not.toHaveBeenCalled()
    expect(router.currentRoute.value.path).toBe('/dashboard')
  })

  it('已保存后离开：不弹确认（保存成功把 dirty 归零）', async () => {
    setViewport(true)
    const { wrapper, router } = await mountEditor()
    const store = addDirtyNode()
    store.workflowName = '测试流程'
    await nextTick()

    const vm = editorVm(wrapper)
    await expect(vm.saveWorkflow()).resolves.toBe(true)
    await flushPromises()

    await router.push('/dashboard')
    await flushPromises()

    expect(dialogWarning).not.toHaveBeenCalled()
    expect(router.currentRoute.value.path).toBe('/dashboard')
  })

  it('dirty 时 beforeunload 被阻止（关闭/刷新页面前原生确认）；无改动时不阻止', async () => {
    setViewport(true)
    await mountEditor()
    addDirtyNode()
    await nextTick()
    expect(dispatchBeforeUnload().defaultPrevented).toBe(true)

    // 干净实例（卸载上面的 dirty 实例后重新挂载）
    mountedWrappers.splice(0).forEach((w) => w.unmount())
    useWorkflowStore().clearWorkflow()
    await mountEditor()
    expect(dispatchBeforeUnload().defaultPrevented).toBe(false)
  })
})
