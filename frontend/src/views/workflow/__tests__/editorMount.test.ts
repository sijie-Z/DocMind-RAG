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
import { getNodeDefinitions, getWorkflow } from '@/api/workflow'

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
  const pinia = createPinia()
  setActivePinia(pinia)
  const wrapper = mount(Host, {
    global: {
      plugins: [naive, pinia, router],
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
