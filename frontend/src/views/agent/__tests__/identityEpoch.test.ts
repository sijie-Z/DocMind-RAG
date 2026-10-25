import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import { NButton, NDialogProvider, NIcon, NMessageProvider, NSpin, NTooltip } from 'naive-ui'
import { createPinia, setActivePinia } from 'pinia'
import zh from '@/locales/zh'

const apiMocks = vi.hoisted(() => ({
  listTools: vi.fn(),
  listSessions: vi.fn(),
  getConfig: vi.fn(),
  chat: vi.fn(),
  getSession: vi.fn(),
  deleteSession: vi.fn(),
  createSession: vi.fn(),
  updateConfig: vi.fn(),
  submitFeedback: vi.fn(),
  getTask: vi.fn(),
  cancelTask: vi.fn(),
  decideApproval: vi.fn(),
  steerTask: vi.fn(),
  decidePlanApproval: vi.fn(),
  listTasks: vi.fn(),
  getReplay: vi.fn(),
}))
const authMocks = vi.hoisted(() => ({
  loginWithJson: vi.fn(),
  logout: vi.fn(),
  getToken: vi.fn(() => 'token-A'),
}))

vi.mock('@/api/agent', () => ({ agentApi: apiMocks }))
vi.mock('@/api/auth', () => ({
  loginWithJson: authMocks.loginWithJson,
  logout: authMocks.logout,
  register: vi.fn(),
  getUserProfile: vi.fn(),
}))
vi.mock('@/api/user', () => ({
  getUserSettings: vi.fn(() => Promise.resolve({ data: {} })),
  updateUserSettings: vi.fn(() => Promise.resolve({ data: {} })),
  updateUserProfile: vi.fn(() => Promise.resolve({ data: {} })),
  getMyOrganizations: vi.fn(),
  switchOrganization: vi.fn(),
}))
vi.mock('@/utils/auth', () => ({
  getToken: authMocks.getToken,
  getRefreshToken: vi.fn(() => 'refresh-A'),
  setToken: vi.fn(),
  setRefreshToken: vi.fn(),
  removeToken: vi.fn(),
}))
vi.mock('@/utils/request', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  clearDefaultAuth: vi.fn(),
}))

import AgentView from '../index.vue'
import { useAgentStore } from '@/stores/agent'
import { useUserStore } from '@/stores/user'
import type { AgentEvent, AgentSession } from '@/types/agent'
import type { UserInfo } from '@/types/user'

const i18n = createI18n({ legacy: false, locale: 'zh', fallbackLocale: 'zh', messages: { zh } })
const Host = defineComponent({
  render: () => h(NMessageProvider, null, {
    default: () => h(NDialogProvider, null, { default: () => h(AgentView) }),
  }),
})
const stubs = {
  PlanTree: true,
  ThinkingStream: true,
  ToolCallCard: true,
  ReflectionBanner: true,
  RetryHintLine: true,
  RunReportBlock: true,
  RunStats: true,
  AgentConfigPanel: true,
  SessionSelector: true,
  AgentInput: true,
  ApprovalCard: true,
  PlanApprovalCard: true,
  ReplayTimeline: true,
  RecentTaskList: true,
  TerminalPanel: true,
  CommandResultCard: true,
  Markdown: true,
  NSpin: true,
  NButton: true,
  NTooltip: true,
  NIcon: true,
}

function envelope(data: unknown) {
  return { data: { success: true, data } }
}

function makeSession(id: string): AgentSession {
  return { id, title: `${id} title`, message_count: 0, created_at: '', updated_at: '' } as AgentSession
}

function makeInfo(id: number): UserInfo {
  return {
    id,
    username: `user-${id}`,
    email: `user-${id}@example.com`,
    isAdmin: false,
    createdAt: '',
    organizations: [],
    organization_id: 1,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
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

async function send(wrapper: ReturnType<typeof mount>, text: string) {
  const input = wrapper.findComponent({ name: 'AgentInput' })
  expect(input.exists()).toBe(true)
  input.vm.$emit('send', text)
  await nextTick()
}

describe('AgentView identity epoch', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
    authMocks.getToken.mockReturnValue('token-A')
    authMocks.logout.mockResolvedValue(envelope({}))
    apiMocks.listTools.mockResolvedValue(envelope([]))
    apiMocks.getConfig.mockResolvedValue(envelope({}))
    apiMocks.getSession.mockResolvedValue(envelope(null))
    apiMocks.getTask.mockResolvedValue(envelope({ state: 'completed' }))
    apiMocks.listTasks.mockResolvedValue(envelope({ tasks: [] }))
  })

  it('logout aborts A, B sends without A IDs, and late A events/completion cannot write back', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const agent = useAgentStore()
    const user = useUserStore()
    user.userInfo = makeInfo(1)
    user.token = 'token-A'
    user.currentOrganizationId = 1

    const chatA = deferred<{ abort: () => void }>()
    const chatB = deferred<{ abort: () => void }>()
    apiMocks.chat.mockReturnValueOnce(chatA.promise).mockReturnValueOnce(chatB.promise)
    apiMocks.listSessions
      .mockResolvedValueOnce(envelope({ sessions: [makeSession('session-A')] }))
      .mockResolvedValueOnce(envelope({ sessions: [makeSession('session-B')] }))

    const wrapperA = await mountView(pinia)
    agent.setCurrentSession('session-A')
    await send(wrapperA, 'A private question')
    expect(apiMocks.chat).toHaveBeenCalledTimes(1)
    const [, onEventA, optionsA] = apiMocks.chat.mock.calls[0]
    expect(optionsA.sessionId).toBe('session-A')

    onEventA({ type: 'chunk', content: 'A private answer' } as AgentEvent)
    onEventA({
      type: 'approval_required', content: 'A private approval', task_id: 'task-A',
      tool_name: 'execute_python', tool_args: { code: 'A private code' },
    } as AgentEvent)
    expect(agent.messages.some((msg) => msg.content.includes('A private answer'))).toBe(true)
    expect(agent.taskId).toBe('task-A')

    await user.logout({ navigate: false })
    expect(optionsA.signal.aborted).toBe(true)
    expect(agent.messages).toEqual([])
    expect(agent.taskId).toBeNull()
    expect(localStorage.getItem('docmind.agent.lastTaskId')).toBeNull()
    wrapperA.unmount()

    authMocks.loginWithJson.mockResolvedValueOnce(envelope({
      access_token: 'token-B', refresh_token: 'refresh-B',
      user_info: makeInfo(2),
    }))
    const loginResult = await user.login({ username: 'user-2', password: 'secret' })
    expect(loginResult.success).toBe(true)

    const wrapperB = await mountView(pinia)
    expect(agent.sessions.map((session) => session.id)).toEqual(['session-B'])
    expect(agent.currentSessionId).toBeNull()
    expect(apiMocks.getTask).not.toHaveBeenCalled()

    await send(wrapperB, 'B question')
    expect(apiMocks.chat).toHaveBeenCalledTimes(2)
    const [, onEventB, optionsB] = apiMocks.chat.mock.calls[1]
    expect(optionsB.sessionId).toBeUndefined()
    expect(agent.messages.map((msg) => msg.content)).toEqual(['B question', ''])
    onEventB({ type: 'chunk', content: 'B fresh answer' } as AgentEvent)
    expect(agent.isLoading).toBe(true)

    // A 的流和 Promise 都在 B 已开始后收尾：两条路径都必须被旧 epoch 拒绝。
    onEventA({ type: 'chunk', content: 'A late answer' } as AgentEvent)
    onEventA({
      type: 'approval_required', content: 'A late approval', task_id: 'task-A-late',
      tool_name: 'execute_python', tool_args: { code: 'A late code' },
    } as AgentEvent)
    chatA.resolve({ abort: vi.fn() })
    await flushPromises()

    expect(agent.messages.map((msg) => msg.content)).toEqual(['B question', 'B fresh answer'])
    expect(agent.taskId).toBeNull()
    expect(agent.pendingApproval).toBeNull()
    expect(agent.isLoading).toBe(true)
    expect(apiMocks.getTask).not.toHaveBeenCalled()

    onEventB({ type: 'done', content: '' } as AgentEvent)
    chatB.resolve({ abort: vi.fn() })
    await flushPromises()
    expect(agent.isLoading).toBe(false)
    expect(wrapperB.text()).not.toContain('A private')
    expect(wrapperB.text()).not.toContain('A late')
    wrapperB.unmount()
  })

  it('late replay-flag success after reset cannot enable replay for B', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const agent = useAgentStore()
    const chatA = deferred<{ abort: () => void }>()
    const replayA = deferred<unknown>()
    apiMocks.chat.mockReturnValueOnce(chatA.promise)
    apiMocks.getTask.mockImplementationOnce(() => replayA.promise as never)

    const wrapper = await mountView(pinia)
    await send(wrapper, 'A question')
    const [, onEventA] = apiMocks.chat.mock.calls[0]
    onEventA({ type: 'done', content: '', task_id: 'task-A' } as AgentEvent)
    chatA.resolve({ abort: vi.fn() })
    await flushPromises()
    expect(apiMocks.getTask).toHaveBeenCalledWith('task-A')

    agent.resetForIdentityChange()
    agent.addMessage({
      role: 'assistant', content: 'B answer', events: [], loading: false,
      currentTool: '', thinkingText: '', planSteps: [], planId: '', progress: 0,
    })
    agent.setTaskId('task-B')
    await nextTick()
    expect(wrapper.find('.replay-entry-btn').exists()).toBe(false)

    replayA.resolve(envelope({ state: 'completed', has_replay: true }))
    await flushPromises()
    expect(wrapper.find('.replay-entry-btn').exists()).toBe(false)
    wrapper.unmount()
  })

  it('late replay-flag failure after reset cannot clear the replay flag B already loaded', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const agent = useAgentStore()
    const chatA = deferred<{ abort: () => void }>()
    const chatB = deferred<{ abort: () => void }>()
    const replayA = deferred<unknown>()
    apiMocks.chat.mockReturnValueOnce(chatA.promise).mockReturnValueOnce(chatB.promise)
    apiMocks.getTask
      .mockImplementationOnce(() => replayA.promise as never)
      .mockResolvedValueOnce(envelope({ state: 'completed', has_replay: true }))

    const wrapper = await mountView(pinia)
    await send(wrapper, 'A question')
    const [, onEventA] = apiMocks.chat.mock.calls[0]
    onEventA({ type: 'done', content: '', task_id: 'task-A' } as AgentEvent)
    chatA.resolve({ abort: vi.fn() })
    await flushPromises()
    expect(apiMocks.getTask).toHaveBeenCalledWith('task-A')

    agent.resetForIdentityChange()
    await send(wrapper, 'B question')
    const [, onEventB] = apiMocks.chat.mock.calls[1]
    onEventB({ type: 'done', content: '', task_id: 'task-B' } as AgentEvent)
    chatB.resolve({ abort: vi.fn() })
    await flushPromises()
    expect(apiMocks.getTask).toHaveBeenCalledWith('task-B')
    expect(wrapper.find('.replay-entry-btn').exists()).toBe(true)

    replayA.reject(new Error('A replay flag failed'))
    await flushPromises()
    expect(wrapper.find('.replay-entry-btn').exists()).toBe(true)
    wrapper.unmount()
  })
})
