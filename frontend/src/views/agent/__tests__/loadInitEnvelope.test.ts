/**
 * 同族清零（SEAM-3 第三轮）：loadInit / 会话链在真实信封契约下必须真加载。
 *
 * mock 形状 = 真实拦截器输出（**完整 AxiosResponse**，信封在 res.data =
 * {success, data}，消费者读 res.data.data）。
 *
 * 变异点：视图读回旧"已解包"假设 res.data.xxx → 工具/会话列表为空、
 * config 复原为信封对象 → 本文件用例红（修复前真实运行时即如此：
 * 工具/会话永不加载、config 漂移）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { NMessageProvider, NSpin, NButton, NTooltip, NIcon } from 'naive-ui'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@/locales'

import AgentView from '../index.vue'
import SessionSelector from '@/components/agent/SessionSelector.vue'
import { useAgentStore } from '@/stores/agent'

const TOOL = {
  name: 'search_knowledge_base', description: 't', tags: [],
  parameters: {}, requires_auth: false, disabled_by_default: false,
}
const SESSION = {
  id: 'sess-1', title: '会话一', message_count: 2,
  created_at: '', updated_at: '',
}

const apiMocks = vi.hoisted(() => ({
  listTools: vi.fn(),
  listSessions: vi.fn(),
  getConfig: vi.fn(),
  getSession: vi.fn(),
  createSession: vi.fn(),
  chat: vi.fn(),
  deleteSession: vi.fn().mockResolvedValue({}),
  updateConfig: vi.fn().mockResolvedValue({}),
  submitFeedback: vi.fn().mockResolvedValue({}),
  getTask: vi.fn().mockResolvedValue({ data: { success: true, data: { state: 'completed' } } }),
  cancelTask: vi.fn().mockResolvedValue({ data: {} }),
  decideApproval: vi.fn().mockResolvedValue({ abort: () => {} }),
  listTasks: vi.fn().mockResolvedValue({ data: { success: true, data: { tasks: [], total: 0, page: 1, page_size: 10 } } }),
  getReplay: vi.fn(),
}))

vi.mock('@/api/agent', () => ({ agentApi: apiMocks }))

const Host = defineComponent({
  render: () => h(NMessageProvider, null, { default: () => h(AgentView) }),
})

const stubs = {
  PlanTree: true,
  ThinkingStream: true,
  ToolCallCard: true,
  ReflectionBanner: true,
  AgentConfigPanel: true,
  Markdown: true,
  NSpin: true,
  NButton: true,
  NTooltip: true,
  NIcon: true,
  // SessionSelector **不 stub**：会话切换链路要在真组件上驱动
}

async function mountView(pinia: ReturnType<typeof createPinia>) {
  const wrapper = mount(Host, {
    global: { plugins: [pinia, i18n], components: { NSpin, NButton, NTooltip, NIcon }, stubs },
  })
  await flushPromises()
  return wrapper
}

describe('AgentView · loadInit/会话链信封解包（同族清零判别）', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
    apiMocks.listTools.mockResolvedValue({ data: { success: true, data: [TOOL] } })
    apiMocks.listSessions.mockResolvedValue({
      data: { success: true, data: { sessions: [SESSION], total: 1, page: 1, page_size: 20 } },
    })
    apiMocks.getConfig.mockResolvedValue({ data: { success: true, data: { model: 'envelope-model' } } })
    apiMocks.getSession.mockResolvedValue({ data: { success: true, data: { config: { model: 'sess-model' } } } })
    apiMocks.createSession.mockResolvedValue({ data: { success: true, data: { id: 'new-1', title: 'New Agent Session' } } })
    apiMocks.getTask.mockResolvedValue({ data: { success: true, data: { state: 'completed' } } })
  })

  it('信封 mock 下工具/会话/config 必须真加载（工具 1 / 会话 1 / config 复原）', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useAgentStore()
    const wrapper = await mountView(pinia)

    expect(store.tools.map((t) => t.name)).toContain('search_knowledge_base')
    expect(store.sessions.map((s) => s.id)).toContain('sess-1')
    expect(store.config.model).toBe('envelope-model')
    wrapper.unmount()
  })

  it('切换会话（getSession 信封）→ config 从 data.config 复原', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useAgentStore()
    const wrapper = await mountView(pinia)

    wrapper.findComponent(SessionSelector).vm.$emit('update:modelValue', 'sess-1')
    await flushPromises()

    expect(apiMocks.getSession).toHaveBeenCalledWith('sess-1')
    expect(store.config.model).toBe('sess-model')
    wrapper.unmount()
  })

  it('新建会话（createSession 信封）→ 列表插入 + 选中新会话', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useAgentStore()
    const wrapper = await mountView(pinia)

    const btn = wrapper.findAll('button.header-btn').find((b) => b.text().includes('新建'))
    expect(btn, '头部必须有新建入口').toBeTruthy()
    await btn!.trigger('click')
    await flushPromises()

    expect(apiMocks.createSession).toHaveBeenCalled()
    expect(store.sessions[0]?.id).toBe('new-1')
    expect(store.currentSessionId).toBe('new-1')
    wrapper.unmount()
  })
})
