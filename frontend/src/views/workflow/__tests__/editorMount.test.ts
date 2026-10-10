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
import { useWorkflowStore } from '@/stores/workflow'
import { getNodeDefinitions, getWorkflow } from '@/api/workflow'

const Host = defineComponent({
  setup() {
    // 真实 App 的包裹层：n-message-provider 在根（App.vue），编辑器是其后代
    return () => h(NMessageProvider, null, { default: () => h(WorkflowEditor) })
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

async function mountEditor(query = '') {
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
        // 本文件的判别目标是 setup 生命周期与 toast 链路，不依赖画布内部
        VueFlow: true,
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
