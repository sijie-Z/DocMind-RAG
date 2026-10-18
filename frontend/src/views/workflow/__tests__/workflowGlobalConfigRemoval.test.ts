/**
 * B3 · 工作流「全局 LLM 配置」入口与「使用全局配置」开关清理。
 *
 * 现状（修前）：
 *  - 工具栏「LLM配置」按钮打开全局配置弹窗（CRUD，落 Redis）；
 *  - 属性面板「使用全局配置」开关 + configProvider 下拉；
 *  - 编辑器挂载即 llmConfigStore.fetchAll() → GET /llm-config 与
 *    /llm-config/providers 两个请求。
 *  但工作流执行引擎（backend/app/services/workflow_engine.py）完全未读取
 *  useGlobalConfig/configProvider/llm-config 存储：节点级回退始终是
 *  node.data.{model,temperature,...} → llm_settings → .env Settings 真字段
 *  （issue #273 已由既有回归用例锁住）。整套入口是死设定。
 *
 * 修复：移除入口/开关/前端请求通道；后端端点保留（标 deprecated）。
 *
 * 变异点：
 *  - 恢复「LLM配置」按钮或「使用全局配置」开关 → UI 断言红；
 *  - 恢复挂载时 llmConfigStore.fetchAll() → 请求断言红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory, type Router } from 'vue-router'
import naive, { NMessageProvider, NDialogProvider } from 'naive-ui'
import { createI18n } from 'vue-i18n'

if (!('ResizeObserver' in globalThis)) {
  class RO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  ;(globalThis as unknown as { ResizeObserver: typeof RO }).ResizeObserver = RO
}

const { requestCalls } = vi.hoisted(() => ({ requestCalls: [] as string[] }))

// 编辑器直接 import 的 workflow/agent API 走桩；其余真实 API 模块（修前含
// @/api/llmConfig）统一落到本 request 桩 —— 记录一切发往后端的 URL。
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
        { id: 2, node_type: 'llm_deepseek', name: 'DeepSeek', category: 'llm', description: '' },
        { id: 3, node_type: 'output', name: '输出节点', category: 'io', description: '工作流出口' },
      ],
    },
  }),
}))

vi.mock('@/api/agent', () => ({
  agentApi: { listSkills: vi.fn().mockResolvedValue({ data: { data: [] } }) },
}))

vi.mock('@/utils/request', () => {
  const make = (method: string) =>
    vi.fn((url: string, ..._args: unknown[]) => {
      requestCalls.push(`${method} ${url}`)
      return Promise.resolve({ data: { data: [] } })
    })
  return {
    default: {
      get: make('GET'),
      post: make('POST'),
      put: make('PUT'),
      patch: make('PATCH'),
      delete: make('DELETE'),
    },
  }
})

import WorkflowEditor from '../editor.vue'
import { useWorkflowStore } from '@/stores/workflow'

const i18n = createI18n({
  legacy: false,
  locale: 'zh',
  messages: { zh: {} },
})

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
    render: () =>
      h(NDialogProvider, null, {
        default: () => h(NMessageProvider, null, { default: () => h(WorkflowEditor) }),
      }),
  })
  const wrapper = mount(Host, {
    global: {
      plugins: [naive, sharedPinia, router, i18n],
      stubs: {
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

beforeEach(() => {
  requestCalls.length = 0
  setActivePinia(sharedPinia)
  const store = useWorkflowStore()
  store.clearWorkflow()
  store.resetExecution()
})

describe('B3 · 工作流编辑器不再请求已弃用的 /llm-config', () => {
  it('挂载编辑器不发出任何 /llm-config 请求（修前 fetchAll 会打 list + providers）', async () => {
    await mountEditor()
    const dead = requestCalls.filter(call => call.toLowerCase().includes('/llm-config'))
    expect(dead, `编辑器仍在请求已弃用的全局配置端点：${dead.join(', ')}`).toEqual([])
  })
})

describe('B3 · 全局配置 UI 入口移除', () => {
  it('工具栏不再有「LLM配置」按钮', async () => {
    const wrapper = await mountEditor()
    expect(wrapper.text()).not.toContain('LLM配置')
  })

  it('LLM 节点属性面板不再有「使用全局配置」开关与配置下拉', async () => {
    const wrapper = await mountEditor()

    // 选中一个 LLM 节点，让属性面板渲染出该分支
    const store = useWorkflowStore()
    const node = {
      id: 'llm_x',
      type: 'llm_deepseek',
      position: { x: 0, y: 0 },
      data: { label: 'DeepSeek', systemPrompt: '', temperature: 0.7, maxTokens: 2048 },
    }
    store.setNodes([node] as never)
    store.selectNode(node as never)
    await nextTick()
    await flushPromises()

    const text = wrapper.text()
    expect(text, '属性面板未渲染 LLM 节点分支，断言无判别力').toContain('系统提示词')
    expect(text).not.toContain('使用全局配置')
    expect(text).not.toContain('将从所选全局配置自动加载')
  })
})
