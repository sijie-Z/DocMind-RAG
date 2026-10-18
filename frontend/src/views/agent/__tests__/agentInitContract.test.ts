/**
 * Agent 页初始化嵌套解包判别用例（UI 验收 round2 · B22/B23/B24/B25/B26/B27）。
 *
 * 后端四端点均为 {success, data} 信封（agent.py:458-472、500-556、585-591、
 * 637-647、696-710），真载荷在 res.data.data（axios body 再一层 data）。
 * 旧实现把信封当载荷：工具列表永不下发（状态栏恒「工具: 0/0」）、会话下拉
 * 恒空、整信封写进 store.config、切会话配置静默 no-op、新建会话产生幻影
 * 「Untitled ()」项（选中即 404）。
 *
 * 既有测试桩曾按错误形状 mock（lock-in）——本提交一并改为真实信封形状。
 *
 * 变异点：把 loadInit 的 toolsRes.data?.data 还原为 toolsRes.data →
 * 「工具/会话/配置落 store」用例精确红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createI18n } from 'vue-i18n'
import { NMessageProvider, NDialogProvider, NSpin, NButton, NTooltip, NIcon } from 'naive-ui'
import { createPinia, setActivePinia } from 'pinia'

import AgentView from '../index.vue'
import { useAgentStore } from '@/stores/agent'
import zh from '@/locales/zh'

const { agentApiMock } = vi.hoisted(() => ({
  agentApiMock: {
    listTools: vi.fn(),
    listSessions: vi.fn(),
    getConfig: vi.fn(),
    chat: vi.fn(),
    getSession: vi.fn(),
    deleteSession: vi.fn(),
    createSession: vi.fn(),
    updateConfig: vi.fn(),
    submitFeedback: vi.fn(),
  },
}))

vi.mock('@/api/agent', () => ({ agentApi: agentApiMock }))

const Host = defineComponent({
  render: () => h(NMessageProvider, null, { default: () => h(NDialogProvider, null, { default: () => h(AgentView) }) }),
})

const stubs = {
  PlanTree: true,
  ThinkingStream: true,
  ToolCallCard: true,
  ReflectionBanner: true,
  AgentConfigPanel: true,
  SessionSelector: true,
  AgentInput: true,
  Markdown: true,
  // replay 支线并入后的新子组件（装配对齐：n-popover 未注册时子组件会被直接渲染）
  RecentTaskList: true,
  ReplayTimeline: true,
  NSpin: true,
  NButton: true,
  NTooltip: true,
  NIcon: true,
}

// replay 支线给 AgentView 引入 useI18n()——mount 必须装 zh i18n 实例
// （仓内先例：searchResultsRender/monitoringContract 等）
const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

// 真实响应 fixture（字段逐字对齐 agent.py 各端点）
const TOOLS = [
  { name: 'web_search', description: '网页搜索', tags: [], parameters: {}, requires_auth: false, disabled_by_default: false },
  { name: 'code_exec', description: '代码执行', tags: [], parameters: {}, requires_auth: false, disabled_by_default: false },
  { name: 'translate', description: '翻译', tags: [], parameters: {}, requires_auth: false, disabled_by_default: false },
]
const SESSIONS = [
  { id: 's-1', title: '架构讨论', message_count: 2, created_at: '2026-10-10T04:44:58', updated_at: '' },
]

async function setupView() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useAgentStore()
  const wrapper = mount(Host, {
    global: { plugins: [pinia, i18n], components: { NSpin, NButton, NTooltip, NIcon }, stubs },
  })
  await flushPromises()
  return { wrapper, store }
}

beforeEach(() => {
  vi.clearAllMocks()
  agentApiMock.listTools.mockResolvedValue({ data: { success: true, data: [], total: 0 } })
  agentApiMock.listSessions.mockResolvedValue({
    data: { success: true, data: { sessions: [], total: 0, page: 1, page_size: 20 } },
  })
  agentApiMock.getConfig.mockResolvedValue({ data: { success: true, data: {} } })
  agentApiMock.getSession.mockResolvedValue({ data: { success: true, data: null } })
  agentApiMock.createSession.mockResolvedValue({ data: { success: true, data: null } })
})

describe('AgentView · 初始化嵌套解包（B22/B23/B24）', () => {
  it('真实信封：工具/会话/配置全部落 store，状态栏显示真实工具数（旧恒 0/0）', async () => {
    agentApiMock.listTools.mockResolvedValue({ data: { success: true, data: TOOLS, total: 3 } })
    agentApiMock.listSessions.mockResolvedValue({
      data: { success: true, data: { sessions: SESSIONS, total: 1, page: 1, page_size: 20 } },
    })
    agentApiMock.getConfig.mockResolvedValue({
      data: { success: true, data: { enable_tools: false, disabled_tools: ['web_search'] } },
    })

    const { wrapper, store } = await setupView()

    expect(store.tools).toHaveLength(3)
    expect(store.sessions).toHaveLength(1)
    expect(store.sessions[0].title).toBe('架构讨论')
    // 配置真实落账（旧实现整信封写入 → enable_tools 保持默认 true）
    expect(store.config.enable_tools).toBe(false)
    expect(store.config.disabled_tools).toEqual(['web_search'])
    // 状态栏：3 工具 - 1 禁用 = 2/3（旧实现恒「工具: 0/0」）
    expect(wrapper.text()).toContain('工具: 2/3')
  })

  it('空信封不崩：无工具/无会话时状态栏 0/0、不产生幻影数据', async () => {
    const { wrapper, store } = await setupView()
    expect(store.tools).toHaveLength(0)
    expect(store.sessions).toHaveLength(0)
    expect(wrapper.text()).toContain('工具: 0/0')
  })
})

describe('AgentView · 切会话读配置（B25）', () => {
  it('update:modelValue 触发后读 data.data.config（旧读 data.config 恒 no-op）', async () => {
    const { wrapper, store } = await setupView()
    agentApiMock.getSession.mockResolvedValue({
      data: {
        success: true,
        data: { id: 's-1', title: '架构讨论', messages: [], config: { enable_tools: false, disabled_tools: ['translate'] }, created_at: '', updated_at: '' },
      },
    })

    const selector = wrapper.findComponent({ name: 'SessionSelector' })
    expect(selector.exists()).toBe(true)
    selector.vm.$emit('update:modelValue', 's-1')
    await flushPromises()

    expect(store.currentSessionId).toBe('s-1')
    expect(store.config.enable_tools).toBe(false)
    expect(store.config.disabled_tools).toEqual(['translate'])
  })
})

describe('AgentView · 新建会话（B26）', () => {
  it('点「+ 新建」→ 只把 data.data 的 {id,title} 入列（旧实现产生幻影 Untitled 信封项）', async () => {
    agentApiMock.createSession.mockResolvedValue({ data: { success: true, data: { id: 's-new', title: 'New Agent Session' } } })

    const { wrapper, store } = await setupView()
    const newBtn = wrapper.findAll('button').find((b) => b.text().includes('新建'))
    expect(newBtn, '「+ 新建」按钮应在屏').toBeTruthy()
    await newBtn!.trigger('click')
    await flushPromises()

    expect(store.sessions).toHaveLength(1)
    expect(store.sessions[0].id).toBe('s-new')
    expect(store.sessions[0].title).toBe('New Agent Session')
    expect(store.currentSessionId).toBe('s-new')
    // 不再有信封壳（旧实现的幻影项 id 为 undefined）
    expect(store.sessions.some((s) => !s.id)).toBe(false)
  })
})
