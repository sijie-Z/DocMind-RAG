/**
 * round2 UI 验收 · workflow/editor 整页崩溃回归（A_sweep 实锤）。
 *
 * 原根因：useWorkflowEditor.ts 在**模块顶层**调用 useRoute/useRouter/
 * useDedupedMessage（模块求值发生在组件 setup 之外，inject() 无上下文）→
 * `message` 为 undefined → loadTemplate 尾部 `message.success('模板加载成功')`
 * 抛 `TypeError: Cannot read properties of undefined (reading 'success')` →
 * 点「快速模板」整页进 ErrorBoundary（快速模板→RAG 问答流程，2/2 复现）。
 * 同因连带：模块级 onMounted/onUnmounted 永不执行（节点定义/LLM 配置/skills
 * 初始化加载全部不发生）。
 *
 * 修复：全部 setup 级调用（inject 族 + watch + onMounted/onUnmounted）移入
 * useWorkflowEditor()，由 <script setup> 同步调用。
 *
 * 变异点：把 `message` 恢复为模块顶层获取（或把 onMounted 移回模块层）→
 * 对应用例红 —— 见提交说明与 _uiw_crash_mutation.txt。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory, type Router } from 'vue-router'
import naive, { NMessageProvider } from 'naive-ui'

// jsdom 缺口补齐（仅本文件使用；产品代码合法使用这些浏览器 API）
if (!('ResizeObserver' in globalThis)) {
  class RO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  ;(globalThis as unknown as { ResizeObserver: typeof RO }).ResizeObserver = RO
}

vi.mock('@/api/workflow', () => ({
  getWorkflows: vi.fn().mockResolvedValue({ data: { data: { items: [], total: 0 } } }),
  getWorkflow: vi.fn().mockResolvedValue({ data: { data: null } }),
  createWorkflow: vi.fn().mockResolvedValue({ data: { data: { id: 1 } } }),
  updateWorkflow: vi.fn().mockResolvedValue({ data: { data: {} } }),
  executeWorkflow: vi.fn().mockResolvedValue({ data: { data: null } }),
  executeWorkflowStream: vi.fn(),
  deleteWorkflow: vi.fn().mockResolvedValue({}),
  // 真实 stub 形状对齐端点：{"success":true,"data":[定义...]}（workflow.py:455-）
  getNodeDefinitions: vi.fn().mockResolvedValue({
    data: {
      data: [
        { id: 1, node_type: 'input', name: '输入节点', category: 'io', description: '工作流入口' },
        { id: 2, node_type: 'llm_deepseek', name: 'DeepSeek', category: 'llm', description: '' },
        { id: 3, node_type: 'output', name: '输出节点', category: 'io', description: '工作流出口' },
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
import InputNode from '../nodes/InputNode.vue'
import OutputNode from '../nodes/OutputNode.vue'
import LLMNode from '../nodes/LLMNode.vue'
import ToolNode from '../nodes/ToolNode.vue'
import ConditionNode from '../nodes/ConditionNode.vue'
import MemoryNode from '../nodes/MemoryNode.vue'
import CodeNode from '../nodes/CodeNode.vue'
import ApiNode from '../nodes/ApiNode.vue'
import TransformNode from '../nodes/TransformNode.vue'
import { useWorkflowStore } from '@/stores/workflow'
import { getNodeDefinitions, getWorkflow, executeWorkflowStream, createWorkflow } from '@/api/workflow'

const Host = defineComponent({
  setup() {
    // 真实 App 的包裹层：n-message-provider 在根（App.vue），编辑器是其后代
    return () => h(NMessageProvider, null, { default: () => h(WorkflowEditor) })
  },
})

/**
 * 渲染全部 node-* 具名插槽的 VueFlow 替身：模板里 14 个
 * <template #node-xxx="nodeProps"> 委托给本 stub 逐一渲染。
 * 未注册的节点组件会退化为裸元素（Failed to resolve component），
 * findComponent(真实组件) 即不存在 → 判别注册是否真实生效。
 */
const VueFlowSlotStub = defineComponent({
  name: 'VueFlow',
  setup(_, { slots }) {
    const nodeProps = { id: 'n1', data: { label: '测试节点', type: 'input' } }
    return () =>
      h(
        'div',
        Object.entries(slots)
          .filter(([name]) => name.startsWith('node-'))
          .map(([name, slot]) => h('div', { class: `slot-${name}` }, slot?.(nodeProps)))
      )
  },
})

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/workflow/editor', name: 'WorkflowEditor', component: { template: '<div />' } },
    ],
  })
}

async function mountEditor(query = '', opts: { resolveCanvasSlots?: boolean } = {}) {
  const router = makeRouter()
  await router.push(`/workflow/editor${query}`)
  await router.isReady()
  const wrapper = mount(Host, {
    global: {
      plugins: [naive, sharedPinia, router],
      stubs: {
        // VueFlow 画布在 jsdom 下无 viewport 语义，整棵子树直通 stub 掉；
        // 节点注册用例用「渲染全部 node-* 插槽」的替身验证组件解析
        VueFlow: opts.resolveCanvasSlots ? VueFlowSlotStub : true,
        Background: true,
        Controls: true,
        MiniMap: true,
      },
    },
  })
  await flushPromises()
  await nextTick()
  return wrapper
}

// 与生产同构：整个应用只有一个 pinia（App 只 install 一次）。useWorkflowEditor.ts
// 是模块级单例，其内部 computed 首次求值后按依赖缓存——若每个测试新建 pinia，
// 单例 computed 仍绑定旧 store、不再重追踪，会产生跨测试串扰（实测：executionStatus
// 保留上一个 store 的缓存值）。共享 pinia + 每测试显式重置状态即可复现生产语义。
const sharedPinia = createPinia()

beforeEach(() => {
  setActivePinia(sharedPinia)
  const store = useWorkflowStore()
  store.clearWorkflow()
  store.resetExecution()
})

type EditorVm = {
  loadTemplate: (_type: string) => void
}

describe('workflow/editor 挂载与快速模板（A_sweep 崩溃回归）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('loadTemplate 不抛错：修复前 message.success 即 TypeError（整页 ErrorBoundary 根因）', async () => {
    const wrapper = await mountEditor()
    const vm = wrapper.findComponent(WorkflowEditor).vm as unknown as EditorVm

    expect(() => vm.loadTemplate('rag')).not.toThrow()
    await nextTick()
    await flushPromises()

    // 模板内容真实落到 store（修复前虽然 store 也先被填充，但随后即抛错中止——
    // 该断言与 not.toThrow 联合给出「跑完整条链」的判别力）
    const store = useWorkflowStore()
    expect(store.nodes).toHaveLength(4)
    expect(store.edges).toHaveLength(3)
    // toast 真实发生（naive message 可能渲染在 provider 子树或 document.body）
    const toastText = wrapper.text() + (document.body.textContent || '')
    expect(toastText).toContain('模板加载成功')
  })

  it('onMounted 真实触发：节点定义加载 + 路由 ?id= 拉取工作流（修复前模块级注册永不执行）', async () => {
    await mountEditor('?id=42')

    expect(getNodeDefinitions).toHaveBeenCalledTimes(1)
    // 路由 query 被消费 → loadWorkflowById(42)（getWorkflow 命中）
    expect(getWorkflow).toHaveBeenCalledWith(42)
  })
})

describe('workflow/editor 画布节点组件注册（C_polish「九节点 import 断裂」回归）', () => {
  it('14 个 node-* 具名插槽全部解析到真实节点组件（修复前全部 Failed to resolve component）', async () => {
    const wrapper = await mountEditor('', { resolveCanvasSlots: true })

    // 与 editor.vue 模板 14 个 <template #node-*> 一一对应
    const cases: Array<[string, unknown]> = [
      ['node-input', InputNode],
      ['node-output', OutputNode],
      ['node-llm', LLMNode],
      ['node-llm_openai', LLMNode],
      ['node-llm_deepseek', LLMNode],
      ['node-llm_qwen', LLMNode],
      ['node-tool_search', ToolNode],
      ['node-tool_tts', ToolNode],
      ['node-condition', ConditionNode],
      ['node-router', ConditionNode],
      ['node-memory', MemoryNode],
      ['node-code', CodeNode],
      ['node-api_call', ApiNode],
      ['node-transform', TransformNode],
    ]
    for (const [slotName, component] of cases) {
      const holder = wrapper.find(`.slot-${slotName}`)
      expect(holder.exists(), `${slotName} 插槽未渲染`).toBe(true)
      expect(
        holder.findComponent(component as never).exists(),
        `${slotName} 未解析到真实组件（组件未 import 注册）`
      ).toBe(true)
    }
  })
})

describe('workflow/editor 调试执行链路（流式接线 + B8-B10 契约回归）', () => {
  it('走 executeWorkflowStream：事件按序驱动日志/进度，成功判定 + 最终输出上屏', async () => {
    const wrapper = await mountEditor()
    const editorVm = wrapper.findComponent(WorkflowEditor).vm as unknown as {
      executeWorkflow: () => Promise<void>
      testInput: string
      executionStatus: string
      executionProgress: number
      executing: boolean
    }

    const store = useWorkflowStore()
    store.currentWorkflowId = 5
    store.workflowName = '测试流程'
    editorVm.testInput = '测试输入'

    // 真事件序列（workflow.py:239-368 + workflow_engine 实时帧）：
    // workflow_start → node_start/node_complete（引擎实时）→ 端点回放一轮
    // node_complete（NodeResult 全字段，:317-319）→ complete（引擎总体结果）
    vi.mocked(executeWorkflowStream).mockImplementation((_id, _input, onEvent, onClose) => {
      onEvent('workflow_start', { execution_id: 9, workflow_name: '测试流程' })
      onEvent('node_start', { node_id: 'input_1', node_type: 'input', timestamp: 't1' })
      onEvent('node_complete', { node_id: 'input_1', node_type: 'input', output: { text: 'hi' }, duration: 12, timestamp: 't2' })
      onEvent('node_start', { node_id: 'llm_1', node_type: 'llm_deepseek', timestamp: 't3' })
      onEvent('node_complete', { node_id: 'llm_1', node_type: 'llm_deepseek', output: { content: 'x' }, duration: 340, timestamp: 't4' })
      onEvent('node_complete', { node_id: 'input_1', node_type: 'input', status: 'completed', output: { text: 'hi' }, duration: 12 })
      onEvent('node_complete', { node_id: 'llm_1', node_type: 'llm_deepseek', status: 'completed', output: { content: 'x' }, duration: 340 })
      onEvent('complete', { success: true, output: '最终答案', node_outputs: {}, context: {}, memory: {} })
      onClose?.()
      return () => {}
    })

    await editorVm.executeWorkflow()
    await nextTick()

    // 接线断言：调试入口必须走真 SSE 流（变异：换回非流式 executeWorkflow → 红）
    expect(executeWorkflowStream).toHaveBeenCalledTimes(1)
    expect(vi.mocked(executeWorkflowStream).mock.calls[0][0]).toBe(5)
    expect(vi.mocked(executeWorkflowStream).mock.calls[0][1]).toEqual({ text: '测试输入' })

    expect(store.executionResults.map(r => [r.nodeId, r.status])).toEqual([
      ['input_1', 'completed'],
      ['llm_1', 'completed'],
    ])
    expect(editorVm.executionStatus).toBe('success')
    expect(editorVm.executionProgress).toBe(100)
    expect(store.finalOutput).toBe('最终答案')
    expect(editorVm.executing).toBe(false)

    // 事件按序到达 → 日志有序（开始 → 节点1完成 → 节点2完成 → 成功）
    const logs = store.executionLogs
    const idxStart = logs.findIndex(l => l.includes('开始执行'))
    const idxIn = logs.findIndex(l => l.includes('[输入节点]'))
    const idxLlm = logs.findIndex(l => l.includes('[DeepSeek]'))
    const idxOk = logs.findIndex(l => l.includes('执行成功'))
    expect(idxStart).toBe(0)
    expect(idxIn).toBeGreaterThan(idxStart)
    expect(idxLlm).toBeGreaterThan(idxIn)
    expect(idxOk).toBeGreaterThan(idxLlm)
    // 实时帧 + 回放帧双 node_complete → 日志必须按节点去重（各恰一行）
    expect(logs.filter(l => l.includes('[输入节点]')).length).toBe(1)
    expect(logs.filter(l => l.includes('[DeepSeek]')).length).toBe(1)
  })

  it('失败上浮：node_error 实时帧 + complete(success:false) → 不谎报成功、状态回 failed', async () => {
    const wrapper = await mountEditor()
    const editorVm = wrapper.findComponent(WorkflowEditor).vm as unknown as {
      executeWorkflow: () => Promise<void>
      testInput: string
      executionStatus: string
      executing: boolean
    }

    const store = useWorkflowStore()
    store.currentWorkflowId = 5
    store.workflowName = '测试流程'
    editorVm.testInput = '测试输入'

    vi.mocked(executeWorkflowStream).mockImplementation((_id, _input, onEvent, onClose) => {
      onEvent('node_start', { node_id: 'llm_1', node_type: 'llm_deepseek', timestamp: 't1' })
      onEvent('node_error', { node_id: 'llm_1', error: '超时', traceback: '...', timestamp: 't2' })
      onEvent('node_complete', { node_id: 'llm_1', node_type: 'llm_deepseek', status: 'failed', error: '超时', duration: 30000 })
      onEvent('complete', { success: false, error: '节点 llm_1 执行失败: 超时', node_outputs: {} })
      onClose?.()
      return () => {}
    })

    await editorVm.executeWorkflow()
    await nextTick()

    expect(store.executionResults[0].status).toBe('failed')
    expect(store.finalOutput).toBeNull()
    expect(editorVm.executionStatus).toBe('failed')
    expect(editorVm.executing).toBe(false)
    expect(store.executionLogs.some(l => l.includes('❌'))).toBe(true)
  })
})

describe('workflow/editor 保存载荷契约（B11）', () => {
  it('保存不再发送后端没有的 engine_type（旧实现属静默不落库的假持久化）', async () => {
    const wrapper = await mountEditor()
    const editorVm = wrapper.findComponent(WorkflowEditor).vm as unknown as {
      saveWorkflow: () => Promise<boolean>
    }
    const store = useWorkflowStore()
    store.workflowName = '流程X'

    await editorVm.saveWorkflow()

    // currentWorkflowId 为空 → 走 createWorkflow 分支（真实载荷断言）
    expect(createWorkflow).toHaveBeenCalledTimes(1)
    const payload = vi.mocked(createWorkflow).mock.calls[0][0] as Record<string, unknown>
    expect(payload.name).toBe('流程X')
    expect('engine_type' in payload).toBe(false)
  })
})
