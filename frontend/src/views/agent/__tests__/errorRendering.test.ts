/**
 * issue #288 判别用例：error（整次运行失败）事件此前四层里断在视图层 ——
 * 发射（service.py:71/162、loop.py:460、agent.py:184 兜底）✓、传输
 * （api/agent.ts 透传）✓、store（stores/agent.ts:170-176 落 msg.events +
 * 停 loading）✓，但 Agent 视图事件循环没有 `evt.type === 'error'` 渲染分支，
 * 失败时用户只见空气泡、看不到失败原因（"LLM 未配置"、"LLM Error: …"、
 * "Agent 执行出错: …"）。本文件钉住视图渲染分支及与 tool_error 的视觉区分。
 *
 * 变异点（已逐条验证）：
 * - 删 index.vue 里 `v-else-if="evt.type === 'error'"` 分支块 → 用例 (a)(b)(c)
 *   红、(d) 绿（「无事件」负例按设计保持绿）；
 * - 分支条件改为无条件 `v-else` → 用例 (d) 红（其余绿）—— 钉住「不是恒显」；
 * - 分支改用 tool_error 的 class（丢「运行失败」徽标体系）→ 用例 (a)(b)(c)
 *   红、(d) 绿 —— 钉住两类失败的视觉区分。
 */
import { describe, it, expect, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { NMessageProvider, NSpin, NButton, NTooltip, NIcon } from 'naive-ui'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@/locales'

import AgentView from '../index.vue'
import { useAgentStore } from '@/stores/agent'

// 视图 onMounted 会拉初始数据；测试里全部打桩为空。
vi.mock('@/api/agent', () => ({
  agentApi: {
    listTools: vi.fn().mockResolvedValue({ data: [] }),
    listSessions: vi.fn().mockResolvedValue({ data: { sessions: [] } }),
    getConfig: vi.fn().mockResolvedValue({ data: {} }),
    chat: vi.fn(),
    getSession: vi.fn().mockResolvedValue({ data: {} }),
    deleteSession: vi.fn().mockResolvedValue({}),
    createSession: vi.fn().mockResolvedValue({ data: null }),
    updateConfig: vi.fn().mockResolvedValue({}),
    submitFeedback: vi.fn().mockResolvedValue({}),
  },
}))

// 视图在 setup 里调 useDedupedMessage()（naive-ui useMessage 需要 Provider）
const Host = defineComponent({
  render: () => h(NMessageProvider, null, { default: () => h(AgentView) }),
})

const stubs = {
  // 显式导入的子组件（脚本里 import 的，按名 stub 掉，避免深挂载）
  PlanTree: true,
  ThinkingStream: true,
  ToolCallCard: true,
  ReflectionBanner: true,
  AgentConfigPanel: true,
  SessionSelector: true,
  AgentInput: true,
  Markdown: true,
  // 模板里用到但未本地注册的 naive-ui 组件：注册 + stub，避免解析告警
  NSpin: true,
  NButton: true,
  NTooltip: true,
  NIcon: true,
}

/** 建 pinia + 一条 assistant 消息，供视图挂载。 */
function seedStore(overrides: Record<string, unknown> = {}) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useAgentStore()
  store.addMessage({
    role: 'assistant',
    content: '',
    events: [],
    loading: false,
    currentTool: '',
    thinkingText: '',
    planSteps: [],
    planId: '',
    progress: 0,
    ...overrides,
  } as any)
  return { pinia, store }
}

async function mountView(pinia: ReturnType<typeof createPinia>) {
  const wrapper = mount(Host, {
    global: {
      plugins: [pinia, i18n],
      components: { NSpin, NButton, NTooltip, NIcon },
      stubs,
    },
  })
  await flushPromises()
  return wrapper
}

async function setupView(events: unknown[] = []) {
  const { pinia, store } = seedStore({ events })
  const wrapper = await mountView(pinia)
  return { wrapper, store }
}

describe('AgentView · error 渲染分支（#288 整次运行失败）', () => {
  it('有 error 事件 → 失败原因渲染出屏（带「运行失败」徽标）', async () => {
    const { wrapper } = await setupView([
      { type: 'error', content: 'LLM 未配置，请检查 DEEPSEEK_API_KEY 环境变量。' },
    ])

    const box = wrapper.find('.run-error-standalone')
    expect(box.exists()).toBe(true)
    expect(box.text()).toContain('运行失败')
    expect(box.text()).toContain('LLM 未配置，请检查 DEEPSEEK_API_KEY 环境变量。')
  })

  it('error 与 tool_error 并存 → 各自独立渲染，整次失败 ≠ 单工具失败（视觉区分）', async () => {
    const { wrapper } = await setupView([
      { type: 'tool_error', content: '工具 web_search 超时', tool_call_id: 'c1' },
      { type: 'error', content: 'Agent 执行出错: LLM Error: connection refused' },
    ])

    const toolErr = wrapper.find('.tool-error-standalone')
    const runErr = wrapper.find('.run-error-standalone')
    expect(toolErr.exists()).toBe(true)
    expect(runErr.exists()).toBe(true)
    // 两类失败用不同元素/样式体系：tool_error 保持 ❌ 行，error 是徽标框
    expect(toolErr.text()).toContain('工具 web_search 超时')
    expect(toolErr.text()).not.toContain('connection refused')
    expect(runErr.text()).toContain('Agent 执行出错: LLM Error: connection refused')
    expect(runErr.find('.run-error-badge').exists()).toBe(true)
    expect(wrapper.findAll('.run-error-standalone')).toHaveLength(1)
    expect(wrapper.findAll('.tool-error-standalone')).toHaveLength(1)
  })

  it('store processEvent 全链路：失败后 loading 停止 + 错误出屏 + 不再转圈', async () => {
    // 中途失败现场：loading 中、尚无正文的 assistant 气泡
    const { pinia, store } = seedStore({ loading: true })
    const wrapper = await mountView(pinia)

    // 失败前：转圈指示器在屏
    expect(wrapper.find('.loading-indicator').exists()).toBe(true)

    store.processEvent({
      type: 'error',
      content: 'LLM Error: connection refused',
    } as any)
    await nextTick()

    expect((store.messages[0] as any).loading).toBe(false)
    expect(store.isLoading).toBe(false)
    expect(wrapper.find('.loading-indicator').exists()).toBe(false)
    expect(wrapper.find('.run-error-standalone').text()).toContain('LLM Error: connection refused')
  })

  it('无 error 事件 → 不渲染失败框（分支可关，不是恒显）', async () => {
    // 注意带一个会走到分支链末尾的 tool_result 事件：若 error 分支被改成
    // 无条件 v-else，它会把该事件误渲染成运行失败框 —— 本用例钉住此点。
    const { wrapper } = await setupView([
      { type: 'tool_result', content: '工具结果', tool_call_id: 'c1' },
      { type: 'tool_error', content: '出错了' },
    ])

    expect(wrapper.find('.run-error-standalone').exists()).toBe(false)
  })
})
