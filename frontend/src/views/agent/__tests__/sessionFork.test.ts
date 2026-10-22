/**
 * Session Fork (#476) · 视图接线判别：消息级「从此处分叉」、谱系徽标、
 * parent_session_id 过滤入口（查看分叉）、分叉后跳转与 seed 说明、409 如实提示。
 *
 * 契约来源：
 * - POST /api/v1/sessions/{id}/fork {message_id?} → {id, copied_message_count, is_seeded…}
 * - GET  /api/v1/agent/sessions?parent_session_id=…（列表过滤）
 * - GET  /api/v1/agent/sessions/{id} → messages[].id / meta（边界可用性）
 * - 409 错误体：{success:false, error_code:"FORK_BOUNDARY_INVALID"|…, message}
 *
 * 变异点：
 * - 消息不携带 serverMessageId / 不加载会话消息 → 分叉入口用例红；
 * - fork 请求不带 message_id / 成功后不跳转子会话 → 请求与跳转用例红；
 * - 未结算 meta(tool_status=pending) 的边界仍可点 → 可用性用例红；
 * - 409 不展示原因 → 错误提示用例红；
 * - 谱系徽标/查看分叉不接 parent_session_id → 徽标/子列表用例红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { NMessageProvider, NDialogProvider, NSpin, NButton, NTooltip, NIcon } from 'naive-ui'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import zh from '@/locales/zh'

import AgentView from '../index.vue'
import { useAgentStore } from '@/stores/agent'

const apiMocks = vi.hoisted(() => ({
  listTools: vi.fn().mockResolvedValue({ data: { success: true, data: [] } }),
  listSessions: vi.fn(),
  getConfig: vi.fn().mockResolvedValue({ data: { success: true, data: {} } }),
  chat: vi.fn(),
  getSession: vi.fn(),
  deleteSession: vi.fn().mockResolvedValue({}),
  createSession: vi.fn().mockResolvedValue({ data: { success: true, data: null } }),
  updateConfig: vi.fn().mockResolvedValue({}),
  submitFeedback: vi.fn().mockResolvedValue({}),
  getTask: vi.fn().mockResolvedValue({ data: { success: true, data: { state: 'completed' } } }),
  cancelTask: vi.fn().mockResolvedValue({ data: { state: 'cancel_requested' } }),
  decideApproval: vi.fn().mockResolvedValue({ abort: () => {} }),
  decidePlanApproval: vi.fn(),
  listTasks: vi.fn().mockResolvedValue({ data: { success: true, data: { tasks: [] } } }),
  getReplay: vi.fn(),
  steerTask: vi.fn(),
}))

vi.mock('@/api/agent', () => ({ agentApi: apiMocks }))

const { forkMock, lineageMock } = vi.hoisted(() => ({
  forkMock: vi.fn(),
  lineageMock: vi.fn(),
}))
vi.mock('@/api/sessions', () => ({
  sessionsApi: { fork: forkMock, lineage: lineageMock },
}))

const { listCommandsMock } = vi.hoisted(() => ({ listCommandsMock: vi.fn() }))
vi.mock('@/api/commands', () => ({
  commandsApi: { list: listCommandsMock, execute: vi.fn() },
}))

vi.mock('@/utils/artifact', () => ({
  downloadTextFile: vi.fn(),
  artifactFilename: vi.fn(),
  isUnifiedDiff: vi.fn(() => false),
  looksLikeJson: vi.fn(() => false),
  looksLikeMarkdown: vi.fn(() => false),
  detectArtifactKind: vi.fn(() => 'text'),
}))

const i18n = createI18n({ legacy: false, locale: 'zh', fallbackLocale: 'zh', messages: { zh } })

const ParentSession = {
  id: 's1',
  title: '母会话',
  message_count: 4,
  created_at: '',
  updated_at: '',
  parent_session_id: null,
  root_session_id: 's1',
  fork_point_message_id: null,
  fork_reason: null,
  is_fork: false,
  is_seeded: false,
}
const ChildSession = {
  id: 'c1',
  title: '母会话 (fork)',
  message_count: 2,
  created_at: '2026-10-22T10:00:00',
  updated_at: '2026-10-22T10:00:00',
  parent_session_id: 's1',
  root_session_id: 's1',
  fork_point_message_id: 'm2',
  fork_reason: 'user_fork',
  is_fork: true,
  is_seeded: true,
}

const PARENT_MESSAGES = [
  { id: 'm1', content: '第一问', message_type: 'user', created_at: '' },
  { id: 'm2', content: '第一答', message_type: 'assistant', created_at: '' },
  { id: 'm3', content: '第二问', message_type: 'user', created_at: '', meta: { tool_status: 'pending' } },
  { id: 'm4', content: '第二答', message_type: 'assistant', created_at: '' },
]
const CHILD_MESSAGES = [
  { id: 'c-m1', content: '第一问', message_type: 'user', created_at: '' },
  { id: 'c-m2', content: '第一答', message_type: 'assistant', created_at: '' },
]

const okSession = (id: string, messages: unknown[]) => ({
  data: {
    success: true,
    data: {
      id,
      title: id === 'c1' ? '母会话 (fork)' : '母会话',
      messages,
      config: null,
      parent_session_id: id === 'c1' ? 's1' : null,
      root_session_id: 's1',
      fork_point_message_id: id === 'c1' ? 'm2' : null,
      fork_reason: id === 'c1' ? 'user_fork' : null,
      is_fork: id === 'c1',
      is_seeded: id === 'c1',
    },
  },
})

const sessionsResponse = (sessions: unknown[]) => ({
  data: { success: true, data: { sessions, total: sessions.length, page: 1, page_size: 50 } },
})

const PassthroughStub = defineComponent({
  name: 'Passthrough',
  setup(_, { slots }) {
    return () => h('div', [slots.trigger?.(), slots.default?.()])
  },
})

const stubs = {
  PlanTree: true,
  ThinkingStream: true,
  ToolCallCard: true,
  ReflectionBanner: true,
  AgentConfigPanel: true,
  Markdown: true,
  RunReportBlock: true,
  RunStats: true,
  ReplayTimeline: true,
  RecentTaskList: true,
  ApprovalCard: true,
  PlanApprovalCard: true,
  NSpin: true,
  NButton: true,
  NTooltip: true,
  NIcon: true,
  'n-popover': PassthroughStub,
}

const Host = defineComponent({
  render: () =>
    h(NMessageProvider, null, {
      default: () => h(NDialogProvider, null, { default: () => h(AgentView) }),
    }),
})

async function mountView() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useAgentStore()
  const wrapper = mount(Host, {
    global: {
      plugins: [pinia, i18n],
      components: { NSpin, NButton, NTooltip, NIcon },
      stubs,
    },
  })
  await flushPromises()
  return { wrapper, store }
}

async function selectSession(wrapper: Awaited<ReturnType<typeof mountView>>['wrapper'], id: string) {
  await wrapper.find('.session-select').setValue(id)
  await flushPromises()
}

describe('AgentView · Session Fork 接线', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    listCommandsMock.mockResolvedValue({
      data: { success: true, data: { enabled: true, scope: 'global', commands: [] } },
    })
    apiMocks.listSessions.mockImplementation(async (params?: { parent_session_id?: string }) =>
      params?.parent_session_id === 's1'
        ? sessionsResponse([ChildSession])
        : sessionsResponse([ParentSession, ChildSession]),
    )
    apiMocks.getSession.mockImplementation(async (id: string) =>
      id === 'c1' ? okSession('c1', CHILD_MESSAGES) : okSession('s1', PARENT_MESSAGES),
    )
    lineageMock.mockImplementation(async (id: string) => ({
      data: {
        success: true,
        data:
          id === 'c1'
            ? {
                session_id: 'c1',
                title: '母会话 (fork)',
                root_session_id: 's1',
                parent_session_id: 's1',
                fork_point_message_id: 'm2',
                fork_reason: 'user_fork',
                is_fork: true,
                is_seeded: true,
                depth: 1,
                ancestors: [
                  {
                    id: 's1',
                    title: '根会话A',
                    parent_session_id: null,
                    fork_point_message_id: null,
                    fork_reason: null,
                    is_fork: false,
                    created_at: '',
                  },
                ],
                children: [],
                parent_missing: false,
                lineage_truncated: false,
              }
            : {
                session_id: 's1',
                title: '母会话',
                root_session_id: 's1',
                parent_session_id: null,
                fork_point_message_id: null,
                fork_reason: null,
                is_fork: false,
                is_seeded: false,
                depth: 0,
                ancestors: [],
                children: [],
                parent_missing: false,
                lineage_truncated: false,
              },
      },
    }))
  })

  it('加载会话消息后每条消息带分叉入口；未结算边界禁用', async () => {
    const { wrapper } = await mountView()
    await selectSession(wrapper, 's1')

    expect(apiMocks.getSession).toHaveBeenCalledWith('s1')
    const forkButtons = wrapper.findAll('[data-testid="fork-here"]')
    expect(forkButtons).toHaveLength(4)
    // 边界语义如实标注：USER=在该消息之前分叉，ASSISTANT=复制到该消息为止
    expect(forkButtons[0].text()).toContain('在此消息之前分叉')
    expect(forkButtons[1].text()).toContain('从此处分叉')
    expect(forkButtons[2].attributes('disabled')).toBeDefined()
    expect(forkButtons[2].attributes('title')).toContain('未结算')
    expect(forkButtons[1].attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('点击「从此处分叉」→ POST fork 带边界 message_id；成功后跳转子会话并保留 seed 说明', async () => {
    forkMock.mockResolvedValue({
      data: {
        success: true,
        data: {
          id: 'c1',
          title: '母会话 (fork)',
          parent_session_id: 's1',
          root_session_id: 's1',
          fork_point_message_id: 'm2',
          fork_reason: 'user_fork',
          message_count: 2,
          copied_message_count: 2,
          boundary_seq: 2,
          is_seeded: true,
          checkpoint: { cloned: false, task_id: null, source_task_id: null },
          idempotent_replay: false,
        },
      },
    })
    const { wrapper, store } = await mountView()
    await selectSession(wrapper, 's1')

    await wrapper.findAll('[data-testid="fork-here"]')[1].trigger('click')
    await flushPromises()

    expect(forkMock).toHaveBeenCalledTimes(1)
    expect(forkMock).toHaveBeenCalledWith('s1', { message_id: 'm2' })
    expect(apiMocks.chat).not.toHaveBeenCalled()

    expect(store.currentSessionId).toBe('c1')
    expect(apiMocks.getSession).toHaveBeenCalledWith('c1')
    expect(store.messages).toHaveLength(2)

    const note = wrapper.find('[data-testid="fork-seed-note"]')
    expect(note.exists()).toBe(true)
    expect(note.text()).toContain('母会话')
    expect(note.text()).toContain('2')
    expect(note.text()).toContain('已写入会话记忆')
    wrapper.unmount()
  })

  it('409 边界非法 → 如实展示原因（不伪装成功、不跳转）', async () => {
    forkMock.mockRejectedValue({
      response: {
        status: 409,
        data: {
          success: false,
          code: 409,
          error_code: 'FORK_BOUNDARY_INVALID',
          message: '该 ASSISTANT 消息不是轮次最终消息（后面还有 assistant），不能作为分叉边界',
          detail: { message_id: 'm2' },
        },
      },
    })
    const { wrapper, store } = await mountView()
    await selectSession(wrapper, 's1')

    await wrapper.findAll('[data-testid="fork-here"]')[1].trigger('click')
    await flushPromises()

    const error = wrapper.find('[data-testid="fork-error"]')
    expect(error.exists()).toBe(true)
    expect(error.text()).toContain('分叉边界非法')
    expect(error.text()).toContain('只能从已结算轮次处分叉')
    expect(store.currentSessionId).toBe('s1')
    wrapper.unmount()
  })

  it('分叉子会话显示谱系徽标（父会话/边界信息可悬停查看）', async () => {
    const { wrapper } = await mountView()
    await selectSession(wrapper, 'c1')

    expect(lineageMock).toHaveBeenCalledWith('c1')
    const badge = wrapper.find('[data-testid="lineage-badge"]')
    expect(badge.exists()).toBe(true)
    expect(badge.text()).toContain('分叉会话')
    const title = badge.attributes('title') || ''
    expect(title).toContain('母会话')
    expect(title).toContain('m2')
    // 祖先链来自 GET /sessions/{id}/lineage（而不是只拼列表字段）
    expect(title).toContain('根会话A')
    wrapper.unmount()
  })

  it('谱系显示父会话缺失（父已删除仍可查，如实标注）', async () => {
    lineageMock.mockResolvedValueOnce({
      data: {
        success: true,
        data: {
          session_id: 'c1',
          title: '母会话 (fork)',
          root_session_id: 's1',
          parent_session_id: 's1',
          fork_point_message_id: 'm2',
          fork_reason: 'user_fork',
          is_fork: true,
          is_seeded: true,
          depth: 0,
          ancestors: [],
          children: [],
          parent_missing: true,
          lineage_truncated: true,
        },
      },
    })
    const { wrapper } = await mountView()
    await selectSession(wrapper, 'c1')
    const title = wrapper.find('[data-testid="lineage-badge"]').attributes('title') || ''
    expect(title).toContain('父会话已删除或不可见')
    wrapper.unmount()
  })

  it('「查看分叉」以 parent_session_id 过滤列出子会话，点击进入', async () => {
    const { wrapper, store } = await mountView()
    await selectSession(wrapper, 's1')

    await wrapper.find('[data-testid="fork-children-btn"]').trigger('click')
    await flushPromises()

    const childCall = apiMocks.listSessions.mock.calls.find(
      (call) => call[0]?.parent_session_id === 's1',
    )
    expect(childCall).toBeDefined()
    const items = wrapper.findAll('[data-testid="fork-child-item"]')
    expect(items).toHaveLength(1)
    expect(items[0].text()).toContain('母会话 (fork)')

    await items[0].trigger('click')
    await flushPromises()
    expect(store.currentSessionId).toBe('c1')
    wrapper.unmount()
  })

  it('父会话列表项不显示谱系徽标', async () => {
    const { wrapper } = await mountView()
    await selectSession(wrapper, 's1')
    expect(wrapper.find('[data-testid="lineage-badge"]').exists()).toBe(false)
    wrapper.unmount()
  })
})
