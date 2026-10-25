import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'

vi.mock('@/api/auth', () => ({
  loginWithJson: vi.fn(),
  register: vi.fn(),
  getUserProfile: vi.fn(),
  logout: vi.fn(),
}))
vi.mock('@/api/user', () => ({
  getUserSettings: vi.fn(() => Promise.resolve({ data: {} })),
  updateUserSettings: vi.fn(() => Promise.resolve({ data: {} })),
  getMyOrganizations: vi.fn(),
  switchOrganization: vi.fn(),
}))
vi.mock('@/utils/auth', () => ({
  getToken: vi.fn(() => 'token-A'),
  getRefreshToken: vi.fn(() => 'refresh-A'),
  setToken: vi.fn(),
  setRefreshToken: vi.fn(),
  removeToken: vi.fn(),
}))
vi.mock('@/utils/request', () => ({ clearDefaultAuth: vi.fn() }))

import { useAgentStore } from '@/stores/agent'
import { useChatStore } from '@/stores/chat'
import { useUserStore } from '@/stores/user'
import { logout as apiLogout, loginWithJson, getUserProfile } from '@/api/auth'
import { switchOrganization as switchOrganizationApi, getMyOrganizations } from '@/api/user'
import { getToken, removeToken, setToken } from '@/utils/auth'
import type { AgentSession } from '@/types/agent'
import type { Conversation } from '@/types/chat'
import type { UserInfo } from '@/types/user'

function makeInfo(id = 1): UserInfo {
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

function makeConversation(id: string): Conversation {
  return { id, title: `conversation-${id}`, userId: 1, createdAt: '', updatedAt: '' }
}

let activeToken = 'token-A'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

async function loginAsB(user: ReturnType<typeof useUserStore>) {
  vi.mocked(loginWithJson).mockResolvedValueOnce({
    data: {
      data: {
        access_token: 'token-B', refresh_token: 'refresh-B', user_info: makeInfo(2),
      },
    },
  } as never)
  const result = await user.login({ username: 'user-2', password: 'secret' })
  expect(result.success).toBe(true)
  await nextTick()
}

function addBareAssistant(agent: ReturnType<typeof useAgentStore>, content: string) {
  agent.addMessage({
    role: 'assistant', content, events: [], loading: false,
    currentTool: '', thinkingText: '', planSteps: [], planId: '', progress: 0,
  })
}

function httpError(status: number) {
  return Object.assign(new Error(`HTTP ${status}`), { response: { status } })
}

function seedIdentityData() {
  const agent = useAgentStore()
  const chat = useChatStore()
  const user = useUserStore()
  const session = {
    id: 'session-A', title: 'A private session', message_count: 1,
    created_at: '', updated_at: '',
  } as AgentSession
  const conversation = makeConversation('conversation-A')

  user.userInfo = makeInfo()
  user.currentOrganizationId = 1
  user.myOrganizations = [{ id: 1, name: 'Org A' }, { id: 2, name: 'Org B' }]
  agent.setSessions([session])
  agent.setCurrentSession(session.id)
  agent.addMessage({
    role: 'assistant', content: 'A private answer', events: [], loading: true,
    currentTool: 'execute_python', thinkingText: 'A private thought',
    planSteps: [], planId: 'plan-A', progress: 0.4,
  })
  agent.isLoading = true
  agent.isThinking = true
  agent.currentToolName = 'execute_python'
  agent.planSteps = [{ id: 'step-A', description: 'A private step' } as never]
  agent.planId = 'plan-A'
  agent.overallProgress = 0.4
  agent.tools = [{ name: 'execute_python' } as never]
  agent.updateConfig({ model: 'model-A' })
  agent.setTaskId('task-A')
  agent.setTaskState('waiting_approval')
  agent.setPendingApproval({
    tool_name: 'execute_python', tool_args: { code: 'A private code' }, risk_level: 'high',
  })
  agent.setPendingPlanApproval({
    plan_id: 'plan-A', goal: 'A private goal', steps: [],
  })
  agent.expandedEvents = new Set(['event-A'])
  chat.conversations = [conversation]
  chat.setCurrentConversation(conversation)
  return { agent, chat, user }
}

function expectAgentCleared(agent: ReturnType<typeof useAgentStore>) {
  expect(agent.messages).toEqual([])
  expect(agent.sessions).toEqual([])
  expect(agent.currentSessionId).toBeNull()
  expect(agent.currentSession).toBeUndefined()
  expect(agent.taskId).toBeNull()
  expect(agent.taskState).toBe('idle')
  expect(agent.pendingApproval).toBeNull()
  expect(agent.pendingPlanApproval).toBeNull()
  expect(agent.planSteps).toEqual([])
  expect(agent.planId).toBe('')
  expect(agent.overallProgress).toBe(0)
  expect(agent.tools).toEqual([])
  expect(agent.expandedEvents.size).toBe(0)
  expect(agent.isLoading).toBe(false)
  expect(agent.isThinking).toBe(false)
  expect(agent.currentToolName).toBe('')
  expect(agent.config.model).toBe('deepseek-v4-flash')
  expect(localStorage.getItem('docmind.agent.lastTaskId')).toBeNull()
}

describe('identity-scoped store reset', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    vi.clearAllMocks()
    activeToken = 'token-A'
    vi.mocked(getToken).mockImplementation(() => activeToken)
    vi.mocked(setToken).mockImplementation((value: string) => {
      activeToken = value
    })
    vi.mocked(removeToken).mockImplementation(() => {
      activeToken = ''
    })
    vi.mocked(apiLogout).mockResolvedValue({ data: {} } as never)
    vi.mocked(switchOrganizationApi).mockResolvedValue({
      data: { success: true, data: { organization_id: 2 } },
    } as never)
    localStorage.setItem('language', 'zh')
    localStorage.setItem('theme', 'dark')
    localStorage.setItem('activeSystemPrompt', 'A prompt')
    localStorage.setItem('activeSystemPromptName', 'A prompt name')
  })

  it('actual logout clears Agent/Chat and rejects late events but preserves UI preferences', async () => {
    const { agent, chat, user } = seedIdentityData()
    const staleEpoch = agent.identityEpoch

    await user.logout({ navigate: false })

    expectAgentCleared(agent)
    expect(chat.currentConversation).toBeNull()
    expect(chat.conversations).toEqual([])
    expect(user.userInfo).toBeNull()
    expect(user.currentOrganizationId).toBeNull()
    expect(user.myOrganizations).toEqual([])
    expect(localStorage.getItem('activeSystemPrompt')).toBeNull()
    expect(localStorage.getItem('activeSystemPromptName')).toBeNull()
    expect(localStorage.getItem('language')).toBe('zh')
    expect(localStorage.getItem('theme')).toBe('dark')

    agent.addMessage({
      role: 'assistant', content: 'B answer', events: [], loading: true,
      currentTool: '', thinkingText: '', planSteps: [], planId: '', progress: 0,
    })
    agent.processEvent({
      type: 'chunk', content: 'A late answer', task_id: 'task-A-late',
    } as never, staleEpoch)
    agent.processEvent({
      type: 'approval_required', content: 'A late approval', task_id: 'task-A-late',
      tool_name: 'execute_python', tool_args: { code: 'A late code' },
    } as never, staleEpoch)

    expect(agent.messages[0].content).toBe('B answer')
    expect(agent.taskId).toBeNull()
    expect(agent.pendingApproval).toBeNull()
    expect(agent.taskState).toBe('idle')
    expect(localStorage.getItem('docmind.agent.lastTaskId')).toBeNull()
  })

  it('successful organization switch clears the old organization scope', async () => {
    const { agent, chat, user } = seedIdentityData()
    const staleEpoch = agent.identityEpoch

    await user.switchOrganization(2)

    expect(user.currentOrgId).toBe(2)
    expect(user.organizationVersion).toBe(1)
    expectAgentCleared(agent)
    expect(chat.currentConversation).toBeNull()
    expect(chat.conversations).toEqual([])
    expect(localStorage.getItem('language')).toBe('zh')
    expect(localStorage.getItem('theme')).toBe('dark')

    agent.processEvent({
      type: 'approval_required', content: 'Org A late approval', task_id: 'task-A-late',
      tool_name: 'execute_python', tool_args: { code: 'Org A code' },
    } as never, staleEpoch)
    expect(agent.taskId).toBeNull()
    expect(agent.pendingApproval).toBeNull()
  })

  it('failed organization switch preserves the original context and epoch', async () => {
    const { agent, chat, user } = seedIdentityData()
    const originalEpoch = agent.identityEpoch
    vi.mocked(switchOrganizationApi).mockRejectedValueOnce(new Error('forbidden'))

    await expect(user.switchOrganization(2)).rejects.toThrow('forbidden')

    expect(agent.identityEpoch).toBe(originalEpoch)
    expect(agent.currentSessionId).toBe('session-A')
    expect(agent.messages[0].content).toBe('A private answer')
    expect(agent.taskId).toBe('task-A')
    expect(agent.pendingApproval?.tool_args).toEqual({ code: 'A private code' })
    expect(chat.currentConversation?.id).toBe('conversation-A')
    expect(user.currentOrgId).toBe(1)
    expect(user.organizationVersion).toBe(0)
    expect(localStorage.getItem('docmind.agent.lastTaskId')).toBe('task-A')
  })

  it('direct A to B login without logout clears all identity keys and preserves preferences', async () => {
    const { agent, chat, user } = seedIdentityData()

    await loginAsB(user)

    expect(apiLogout).not.toHaveBeenCalled()
    expect(user.userInfo?.id).toBe(2)
    expect(user.token).toBe('token-B')
    expectAgentCleared(agent)
    expect(chat.currentConversation).toBeNull()
    expect(chat.conversations).toEqual([])
    expect(localStorage.getItem('activeSystemPrompt')).toBeNull()
    expect(localStorage.getItem('activeSystemPromptName')).toBeNull()
    expect(localStorage.getItem('language')).toBe('zh')
    expect(localStorage.getItem('theme')).toBe('dark')
    expect(JSON.parse(localStorage.getItem('user_info')!).id).toBe(2)
  })

  it('organization switch invalidates an older GET without rollback or a second reset', async () => {
    const { agent, user } = seedIdentityData()
    const oldGet = deferred<unknown>()
    const currentGet = deferred<unknown>()
    vi.mocked(getMyOrganizations)
      .mockReturnValueOnce(oldGet.promise as never)
      .mockReturnValueOnce(currentGet.promise as never)

    const oldRequest = user.fetchMyOrganizations()
    await user.switchOrganization(2)
    const switchedEpoch = agent.identityEpoch
    const switchedVersion = user.organizationVersion
    addBareAssistant(agent, 'B current answer')

    const currentRequest = user.fetchMyOrganizations()
    expect(getMyOrganizations).toHaveBeenCalledTimes(2)
    oldGet.resolve({
      data: {
        success: true,
        data: {
          current_organization_id: 1,
          organizations: [{ id: 99, name: 'Stale Org A' }],
        },
      },
    })
    await expect(oldRequest).resolves.toEqual([])

    // 旧 GET 的 finally 也不能释放新 GET 的单飞槽位。
    const duplicateCurrentRequest = user.fetchMyOrganizations()
    expect(getMyOrganizations).toHaveBeenCalledTimes(2)
    expect(user.currentOrgId).toBe(2)
    expect(user.myOrganizations).toEqual([{ id: 1, name: 'Org A' }, { id: 2, name: 'Org B' }])
    expect(agent.identityEpoch).toBe(switchedEpoch)
    expect(user.organizationVersion).toBe(switchedVersion)
    expect(agent.messages[0].content).toBe('B current answer')

    currentGet.resolve({
      data: {
        success: true,
        data: {
          current_organization_id: 2,
          organizations: [{ id: 2, name: 'Current Org B' }],
        },
      },
    })
    await expect(currentRequest).resolves.toEqual([{ id: 2, name: 'Current Org B' }])
    await duplicateCurrentRequest
    expect(user.currentOrgId).toBe(2)
    expect(user.myOrganizations).toEqual([{ id: 2, name: 'Current Org B' }])
    expect(agent.identityEpoch).toBe(switchedEpoch)
    expect(user.organizationVersion).toBe(switchedVersion)
    expect(agent.messages[0].content).toBe('B current answer')
  })

  it('profile from organization A arriving after a successful switch cannot roll back B', async () => {
    const { agent, user } = seedIdentityData()
    const profile = deferred<unknown>()
    const currentProfile = deferred<unknown>()
    vi.mocked(getUserProfile)
      .mockReturnValueOnce(profile.promise as never)
      .mockReturnValueOnce(currentProfile.promise as never)
    const staleRequest = user.getUserInfo()

    await user.switchOrganization(2)
    const currentEpoch = agent.identityEpoch
    const currentVersion = user.organizationVersion
    addBareAssistant(agent, 'Org B current answer')
    const currentRequest = user.getUserInfo()

    const assertion = expect(staleRequest).rejects.toMatchObject({ authScopeChanged: true })
    profile.resolve({ data: { data: { ...makeInfo(1), organization_id: 1 } } })
    await assertion

    expect(user.currentOrgId).toBe(2)
    expect(user.userInfo?.organization_id).toBe(2)
    expect(user.organizationVersion).toBe(currentVersion)
    expect(agent.identityEpoch).toBe(currentEpoch)
    expect(agent.messages[0].content).toBe('Org B current answer')

    const currentAssertion = expect(currentRequest).resolves.toEqual({
      data: expect.objectContaining({ organization_id: 2, username: 'org-b-profile' }),
    })
    currentProfile.resolve({
      data: { data: { ...makeInfo(1), organization_id: 2, username: 'org-b-profile' } },
    })
    await currentAssertion
    expect(user.currentOrgId).toBe(2)
    expect(user.userInfo?.username).toBe('org-b-profile')
    expect(user.organizationVersion).toBe(currentVersion)
    expect(agent.identityEpoch).toBe(currentEpoch)
    expect(agent.messages[0].content).toBe('Org B current answer')
  })

  it('late getUserInfo success after logout and B login cannot restore A', async () => {
    const { agent, user } = seedIdentityData()
    const profile = deferred<unknown>()
    vi.mocked(getUserProfile).mockReturnValueOnce(profile.promise as never)
    const staleRequest = user.getUserInfo()

    await user.logout({ navigate: false })
    await loginAsB(user)
    const currentEpoch = agent.identityEpoch
    addBareAssistant(agent, 'B current answer')
    localStorage.setItem('activeSystemPrompt', 'B prompt')
    localStorage.setItem('activeSystemPromptName', 'B prompt name')

    const assertion = expect(staleRequest).rejects.toMatchObject({ authScopeChanged: true })
    profile.resolve({ data: { data: makeInfo(1) } })
    await assertion

    expect(user.userInfo?.id).toBe(2)
    expect(user.token).toBe('token-B')
    expect(agent.identityEpoch).toBe(currentEpoch)
    expect(agent.messages[0].content).toBe('B current answer')
    expect(localStorage.getItem('activeSystemPrompt')).toBe('B prompt')
    expect(localStorage.getItem('activeSystemPromptName')).toBe('B prompt name')
    expect(JSON.parse(localStorage.getItem('user_info')!).id).toBe(2)
  })

  it('late getUserInfo 401 after logout and B login cannot logout B', async () => {
    const { agent, user } = seedIdentityData()
    const profile = deferred<unknown>()
    vi.mocked(getUserProfile).mockReturnValueOnce(profile.promise as never)
    const staleRequest = user.getUserInfo()

    await user.logout({ navigate: false })
    await loginAsB(user)
    const currentEpoch = agent.identityEpoch
    addBareAssistant(agent, 'B current answer')
    const logoutCalls = vi.mocked(apiLogout).mock.calls.length
    const removeTokenCalls = vi.mocked(removeToken).mock.calls.length

    const assertion = expect(staleRequest).rejects.toMatchObject({ authScopeChanged: true })
    profile.reject(httpError(401))
    await assertion

    expect(apiLogout).toHaveBeenCalledTimes(logoutCalls)
    expect(removeToken).toHaveBeenCalledTimes(removeTokenCalls)
    expect(user.userInfo?.id).toBe(2)
    expect(user.token).toBe('token-B')
    expect(user.authServiceUnavailable).toBe(false)
    expect(agent.identityEpoch).toBe(currentEpoch)
    expect(agent.messages[0].content).toBe('B current answer')
  })

  it('token replacement within the same scope also shields the session from an old 401', async () => {
    const { agent, user } = seedIdentityData()
    const profile = deferred<unknown>()
    vi.mocked(getUserProfile).mockReturnValueOnce(profile.promise as never)
    const staleRequest = user.getUserInfo()

    // 模拟静默续期：authScopeVersion 不变，只有请求身份（token）更新。
    setToken('token-B', 3600)
    user.token = 'token-B'
    const assertion = expect(staleRequest).rejects.toMatchObject({ authScopeChanged: true })
    profile.reject(httpError(401))
    await assertion

    expect(apiLogout).not.toHaveBeenCalled()
    expect(removeToken).not.toHaveBeenCalled()
    expect(user.token).toBe('token-B')
    expect(user.userInfo?.id).toBe(1)
    expect(agent.messages[0].content).toBe('A private answer')
  })
})
