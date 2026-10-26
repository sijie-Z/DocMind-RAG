/** Citation preview/download authentication and real modal behavior. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import axios, { type AxiosRequestConfig, type AxiosResponse } from 'axios'
import { defineComponent, h } from 'vue'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createI18n } from 'vue-i18n'
import naive, { NDialogProvider } from 'naive-ui'
import zh from '@/locales/zh'
import request, { clearDefaultAuth } from '@/utils/request'
import { removeToken } from '@/utils/auth'
import type { KnowledgeSource } from '@/types/chat'

const { messageSpies, loginWithJsonMock, getUserSettingsMock, switchOrganizationMock } = vi.hoisted(() => ({
  messageSpies: { error: vi.fn(), info: vi.fn(), success: vi.fn(), warning: vi.fn(), loading: vi.fn() },
  loginWithJsonMock: vi.fn(),
  getUserSettingsMock: vi.fn(),
  switchOrganizationMock: vi.fn(),
}))

vi.mock('@/utils/message', () => ({ useDedupedMessage: () => messageSpies }))

vi.mock('@/api/chat', () => ({
  getConversationMessages: vi.fn(),
  clearConversationMessages: vi.fn().mockResolvedValue({ data: { success: true } }),
  updateMessageFeedback: vi.fn().mockResolvedValue({ data: {} }),
  unbindConversationDocs: vi.fn().mockResolvedValue({ data: { success: true } }),
  parseUpload: vi.fn().mockResolvedValue({ content: 'x', filename: 'f', chunk_count: 1, file_size: 1 }),
}))

vi.mock('@/api/conversation', () => ({
  getConversations: vi.fn().mockResolvedValue({ data: { data: { data: [], total: 0 } } }),
  deleteConversation: vi.fn().mockResolvedValue(undefined),
  createConversation: vi.fn().mockResolvedValue({ data: { data: {} } }),
  updateConversation: vi.fn().mockResolvedValue({ data: { data: {} } }),
  getConversation: vi.fn(),
  getConversationMessages: vi.fn(),
  clearConversationMessages: vi.fn(),
  unbindConversationDocs: vi.fn(),
  updateMessageFeedback: vi.fn(),
}))

vi.mock('@/api/organization', () => ({
  getOrganizationSettings: vi.fn().mockResolvedValue({ data: { settings: {} } }),
}))

vi.mock('@/api/user', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/user')>()
  return {
    ...actual,
    getUserSettings: getUserSettingsMock,
    switchOrganization: switchOrganizationMock,
  }
})

vi.mock('@/api/auth', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/auth')>()
  return { ...actual, loginWithJson: loginWithJsonMock }
})

import ChatPage from '@/views/chat/index.vue'
import { useUserStore } from '@/stores/user'

const SOURCE: KnowledgeSource = {
  filename: '架构文档.pdf',
  relevanceScore: 0.82,
  content: '命中的分块内容',
  documentId: 'doc-7',
}
const SOURCE_B: KnowledgeSource = {
  filename: '文档乙.pdf',
  relevanceScore: 0.81,
  content: '命中的乙文档内容',
  documentId: 'doc-8',
}

const MsgStub = defineComponent({
  name: 'ChatMessages',
  emits: ['openSource'],
  setup() { return () => h('div') },
})

const i18n = createI18n({
  legacy: false,
  locale: 'zh',
  missingWarn: false,
  fallbackWarn: false,
  messages: { zh },
})

const DOWNLOAD_PATH = '/documents/doc-7/download-url'
const DOWNLOAD_PATH_B = '/documents/doc-8/download-url'
const SIGNED_URL = 'https://minio.test/42/scope.pdf?X-Amz-Signature=fake'
const SIGNED_URL_B = 'https://minio.test/42/scope-b.pdf?X-Amz-Signature=fake-b'
const originalAdapter = request.defaults.adapter

type SeenRequest = {
  url: string
  authorization?: string
}

let seen: SeenRequest[] = []
let downloadImplementation: (config: AxiosRequestConfig) => Promise<AxiosResponse>
let downloadImplementationB: (config: AxiosRequestConfig) => Promise<AxiosResponse>

function response(config: AxiosRequestConfig, data: unknown, status = 200): AxiosResponse {
  return {
    data,
    status,
    statusText: String(status),
    headers: {},
    config: config as AxiosResponse['config'],
  }
}

function httpError(config: AxiosRequestConfig, status: number, data: unknown): never {
  const error = new Error(`HTTP ${status}`) as Error & {
    config: AxiosRequestConfig
    response: AxiosResponse
    isAxiosError: boolean
  }
  error.config = config
  error.response = response(config, data, status)
  error.isAxiosError = true
  throw error
}

function authorizationOf(config: AxiosRequestConfig): string | undefined {
  const headers = config.headers as Record<string, string> & {
    get?: (name: string) => string | undefined
  }
  return headers?.Authorization ?? headers?.authorization ?? headers?.get?.('Authorization')
}

function installAdapter() {
  request.defaults.adapter = (async (config) => {
    const url = config.url ?? ''
    seen.push({ url, authorization: authorizationOf(config) })
    if (url === '/documents/doc-7') {
      return response(config, {
        success: true,
        data: {
          id: 'doc-7',
          title: '架构文档',
          filename: '架构文档.pdf',
          file_size: 2048,
          file_type: 'pdf',
          status: 'indexed',
          created_at: '2026-10-01T00:00:00',
          upload_source: '知识库上传',
        },
      })
    }
    if (url === '/documents/doc-7/content') {
      return response(config, {
        success: true,
        data: {
          id: 'doc-7',
          filename: '架构文档.pdf',
          content: '全文内容甲',
          summary: '摘要',
          suggested_tags: ['tag'],
        },
      })
    }
    if (url === '/documents/doc-8') {
      return response(config, {
        success: true,
        data: {
          id: 'doc-8', title: '文档乙', filename: '文档乙.pdf', file_size: 1024,
          file_type: 'pdf', status: 'indexed', created_at: '2026-10-02T00:00:00',
          upload_source: '知识库上传',
        },
      })
    }
    if (url === '/documents/doc-8/content') {
      return response(config, {
        success: true,
        data: {
          id: 'doc-8', filename: '文档乙.pdf', content: '全文内容乙',
          summary: '摘要乙', suggested_tags: ['tag-b'],
        },
      })
    }
    if (url === DOWNLOAD_PATH) return downloadImplementation(config)
    if (url === DOWNLOAD_PATH_B) return downloadImplementationB(config)
    throw new Error(`Unexpected request: ${url}`)
  }) as never
}

function downloadSuccess(config: AxiosRequestConfig) {
  return Promise.resolve(response(config, {
    success: true,
    data: { downloadUrl: SIGNED_URL, filename: 'scope.pdf', expiresIn: 3600 },
  }))
}

function downloadSuccessB(config: AxiosRequestConfig) {
  return Promise.resolve(response(config, {
    success: true,
    data: { downloadUrl: SIGNED_URL_B, filename: 'scope-b.pdf', expiresIn: 3600 },
  }))
}

async function mountOpenPreview(source: KnowledgeSource = SOURCE) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/chat', name: 'Chat', component: { template: '<div />' } },
      { path: '/login', name: 'Login', component: { template: '<div />' } },
    ],
  })
  await router.push('/chat')
  await router.isReady()
  const pinia = createPinia()
  setActivePinia(pinia)
  const userStore = useUserStore()
  userStore.userInfo = { id: 1, username: 'u', organization_id: 42 } as never
  const Host = defineComponent({
    setup() { return () => h(NDialogProvider, null, { default: () => h(ChatPage) }) },
  })
  const wrapper = mount(Host, {
    global: {
      plugins: [naive, pinia, router, i18n],
      stubs: {
        ChatSidebar: true,
        ChatMessages: MsgStub,
        ChatInput: true,
        ChatHeader: true,
        DebugPanel: true,
        teleport: true,
      },
    },
  })
  await flushPromises()
  await flushPromises()
  wrapper.findComponent(MsgStub).vm.$emit('openSource', source)
  await flushPromises()
  await flushPromises()
  const button = wrapper.find('button[data-testid="download-document"]')
  expect(button.exists()).toBe(true)
  return { wrapper, button, userStore, router }
}

function downloadCalls() {
  return seen.filter((item) => item.url === DOWNLOAD_PATH)
}

async function loginAsB(userStore: ReturnType<typeof useUserStore>) {
  const result = await userStore.login({ username: 'user-B', password: 'secret' })
  expect(result.success).toBe(true)
  await flushPromises()
  await flushPromises()
}

beforeEach(() => {
  vi.restoreAllMocks()
  localStorage.clear()
  localStorage.setItem('docmind_token', 'token-A')
  localStorage.setItem('docmind_refresh_token', 'refresh-A')
  clearDefaultAuth()
  seen = []
  downloadImplementation = downloadSuccess
  downloadImplementationB = downloadSuccessB
  loginWithJsonMock.mockResolvedValue({
    data: {
      data: {
        access_token: 'token-B', refresh_token: 'refresh-B', expires_in: 3600,
        user_info: { id: 8, username: 'user-B', organization_id: 42, is_active: true },
      },
    },
  })
  getUserSettingsMock.mockResolvedValue({ data: { data: {} } })
  switchOrganizationMock.mockImplementation(async (orgId: number) => ({
    data: { success: true, data: { organization_id: orgId, is_org_admin: false } },
  }))
  installAdapter()
  vi.spyOn(window, 'open').mockImplementation(() => null)
  for (const spy of Object.values(messageSpies)) spy.mockClear()
})

afterEach(() => {
  request.defaults.adapter = originalAdapter
  clearDefaultAuth()
  localStorage.clear()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

enableAutoUnmount(afterEach)

describe('citation download through authenticated JSON URL', () => {
  it('keeps preview Bearer, blocks duplicate in-flight clicks, and opens only the signed URL once', async () => {
    let finishDownload!: () => void
    downloadImplementation = (config) => new Promise<AxiosResponse>((resolve) => {
      finishDownload = () => resolve(response(config, {
        success: true,
        data: { downloadUrl: SIGNED_URL, filename: 'scope.pdf', expiresIn: 3600 },
      }))
    })
    const { wrapper, button } = await mountOpenPreview()

    const previewRequests = seen.filter((item) => (
      item.url === '/documents/doc-7' || item.url === '/documents/doc-7/content'
    ))
    expect(previewRequests).toHaveLength(2)
    expect(previewRequests.every((item) => item.authorization === 'Bearer token-A')).toBe(true)

    const firstClick = button.trigger('click')
    const secondClick = button.trigger('click')
    await Promise.all([firstClick, secondClick])
    expect(downloadCalls()).toHaveLength(1)
    expect(button.attributes('disabled')).toBeDefined()
    expect(button.text()).toContain('正在准备下载')

    finishDownload()
    await flushPromises()
    await flushPromises()
    expect(downloadCalls()).toHaveLength(1)
    expect(downloadCalls()[0].authorization).toBe('Bearer token-A')
    expect(window.open).toHaveBeenCalledTimes(1)
    expect(window.open).toHaveBeenCalledWith(SIGNED_URL, '_blank', 'noopener,noreferrer')
    const openedUrl = String(vi.mocked(window.open).mock.calls[0][0])
    expect(openedUrl).not.toContain('/api/v1/documents/doc-7/download')
    expect(openedUrl).not.toContain('token-A')
    expect(new URL(openedUrl).searchParams.has('access_token')).toBe(false)
    expect(wrapper.find('[data-testid="download-error"]').exists()).toBe(false)
  })

  it('401 refreshes once and only the final successful response opens one tab', async () => {
    let attempts = 0
    downloadImplementation = async (config) => {
      attempts += 1
      if (attempts === 1) return httpError(config, 401, { detail: 'expired' })
      return downloadSuccess(config)
    }
    const refreshSpy = vi.spyOn(axios, 'post').mockResolvedValue({
      data: {
        data: {
          access_token: 'token-B',
          refresh_token: 'refresh-B',
          expires_in: 3600,
        },
      },
    } as never)
    const { button } = await mountOpenPreview()
    await button.trigger('click')
    await flushPromises()
    await flushPromises()

    expect(attempts).toBe(2)
    expect(refreshSpy).toHaveBeenCalledTimes(1)
    expect(downloadCalls().map((item) => item.authorization)).toEqual([
      'Bearer token-A',
      'Bearer token-B',
    ])
    expect(window.open).toHaveBeenCalledTimes(1)
    expect(window.open).toHaveBeenCalledWith(SIGNED_URL, '_blank', 'noopener,noreferrer')
  })

  it.each([
    { status: 403, text: '无权限访问' },
    { status: 500, text: '获取下载链接失败' },
  ])('HTTP $status keeps the error in the open modal and never opens a tab', async ({ status, text }) => {
    downloadImplementation = async (config) => httpError(config, status, { message: text })
    const { wrapper, button } = await mountOpenPreview()
    await button.trigger('click')
    await flushPromises()
    await flushPromises()

    expect(window.open).not.toHaveBeenCalled()
    const error = wrapper.find('[data-testid="download-error"]')
    expect(error.exists()).toBe(true)
    expect(error.attributes('role')).toBe('alert')
    expect(error.text()).toContain(text)
    expect(wrapper.text()).toContain('全文内容甲')
    expect(button.attributes('disabled')).toBeUndefined()
  })

  it('network retries still never open; final failure remains visible in the modal', async () => {
    downloadImplementation = async (config) => {
      const error = new Error('offline') as Error & { config: AxiosRequestConfig }
      error.config = config
      throw error
    }
    const { wrapper, button } = await mountOpenPreview()
    vi.useFakeTimers()
    await button.trigger('click')
    await vi.runAllTimersAsync()
    await flushPromises()

    expect(downloadCalls()).toHaveLength(4)
    expect(window.open).not.toHaveBeenCalled()
    const error = wrapper.find('[data-testid="download-error"]')
    expect(error.exists()).toBe(true)
    expect(error.attributes('role')).toBe('alert')
    expect(error.text()).toContain('下载失败')
    expect(wrapper.text()).toContain('全文内容甲')
  })
})

describe('independent citation download identity and timing probes', () => {
  it('A signed URL resolving after direct A-to-B login cannot open; B can still download once', async () => {
    let finishA!: () => void
    downloadImplementation = (config) => new Promise<AxiosResponse>((resolve) => {
      finishA = () => resolve(response(config, {
        success: true,
        data: { downloadUrl: SIGNED_URL, filename: 'a.pdf', expiresIn: 3600 },
      }))
    })
    const { button, userStore } = await mountOpenPreview()
    await button.trigger('click')
    await loginAsB(userStore)
    expect(userStore.userInfo?.id).toBe(8)

    downloadImplementation = downloadSuccessB
    finishA()
    await flushPromises()
    await button.trigger('click')
    await flushPromises()
    await flushPromises()

    expect(vi.mocked(window.open).mock.calls.map((call) => call[0])).toEqual([SIGNED_URL_B])
    expect(downloadCalls().map((item) => item.authorization)).toEqual([
      'Bearer token-A',
      'Bearer token-B',
    ])
  })

  it('organization switch invalidates an old pending URL and leaves B able to download', async () => {
    let finishA!: () => void
    downloadImplementation = (config) => new Promise<AxiosResponse>((resolve) => {
      finishA = () => resolve(response(config, {
        success: true,
        data: { downloadUrl: SIGNED_URL, filename: 'a.pdf', expiresIn: 3600 },
      }))
    })
    const { button, userStore } = await mountOpenPreview()
    await button.trigger('click')
    await userStore.switchOrganization(43)
    await flushPromises()
    expect(userStore.currentOrgId).toBe(43)

    finishA()
    await flushPromises()
    expect(window.open).not.toHaveBeenCalled()
    expect(button.attributes('disabled')).toBeUndefined()

    downloadImplementation = downloadSuccessB
    await button.trigger('click')
    await flushPromises()
    expect(vi.mocked(window.open).mock.calls.map((call) => call[0])).toEqual([SIGNED_URL_B])
  })

  it('an old request error and finally cannot write state after identity changes', async () => {
    let rejectA!: () => void
    downloadImplementation = (config) => new Promise<AxiosResponse>((_resolve, reject) => {
      rejectA = () => {
        try {
          httpError(config, 500, { message: 'A signing failed' })
        } catch (error) {
          reject(error)
        }
      }
    })
    const { wrapper, button, userStore } = await mountOpenPreview()
    await button.trigger('click')
    await loginAsB(userStore)

    rejectA()
    await flushPromises()
    expect(wrapper.find('[data-testid="download-error"]').exists()).toBe(false)
    expect(button.attributes('disabled')).toBeUndefined()

    downloadImplementation = downloadSuccessB
    await button.trigger('click')
    await flushPromises()
    expect(vi.mocked(window.open).mock.calls.map((call) => call[0])).toEqual([SIGNED_URL_B])
  })

  it('a signed URL resolving after the preview owner unmounts cannot open', async () => {
    let finishDownload!: () => void
    downloadImplementation = (config) => new Promise<AxiosResponse>((resolve) => {
      finishDownload = () => resolve(response(config, {
        success: true,
        data: { downloadUrl: SIGNED_URL, filename: 'a.pdf', expiresIn: 3600 },
      }))
    })
    const { wrapper, button } = await mountOpenPreview()
    await button.trigger('click')
    wrapper.unmount()
    finishDownload()
    await flushPromises()
    expect(window.open).not.toHaveBeenCalled()
  })

  it('switching documents accepts only the newest download URL and request order stays A then B', async () => {
    let finishA!: () => void
    downloadImplementation = (config) => new Promise<AxiosResponse>((resolve) => {
      finishA = () => resolve(response(config, {
        success: true,
        data: { downloadUrl: SIGNED_URL, filename: 'a.pdf', expiresIn: 3600 },
      }))
    })
    const { wrapper, button } = await mountOpenPreview()
    await button.trigger('click')

    wrapper.findComponent(MsgStub).vm.$emit('openSource', SOURCE_B)
    await flushPromises()
    await flushPromises()
    await wrapper.find('button[data-testid="download-document"]').trigger('click')
    await flushPromises()
    finishA()
    await flushPromises()

    expect(vi.mocked(window.open).mock.calls.map((call) => call[0])).toEqual([SIGNED_URL_B])
    expect(seen.filter((item) => item.url.includes('download-url')).map((item) => item.url)).toEqual([
      DOWNLOAD_PATH,
      DOWNLOAD_PATH_B,
    ])
    expect(wrapper.text()).toContain('全文内容乙')
  })

  it('a request without Bearer receives 401 and never opens', async () => {
    const { wrapper, button } = await mountOpenPreview()
    removeToken()
    clearDefaultAuth()
    downloadImplementation = async (config) => {
      if (!authorizationOf(config)) return httpError(config, 401, { detail: 'unauthenticated' })
      return downloadSuccess(config)
    }
    await button.trigger('click')
    await flushPromises()
    await flushPromises()

    expect(downloadCalls()).toHaveLength(1)
    expect(downloadCalls()[0].authorization).toBeUndefined()
    expect(window.open).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="download-error"]').attributes('role')).toBe('alert')
  })

  it('a final 401 after one refresh does not open or replay another save', async () => {
    let attempts = 0
    downloadImplementation = async (config) => {
      attempts += 1
      return httpError(config, 401, { detail: 'still expired' })
    }
    const refreshSpy = vi.spyOn(axios, 'post').mockResolvedValue({
      data: { data: { access_token: 'token-B', refresh_token: 'refresh-B', expires_in: 3600 } },
    } as never)
    const { wrapper, button } = await mountOpenPreview()
    await button.trigger('click')
    await flushPromises()
    await flushPromises()

    expect(attempts).toBe(2)
    expect(refreshSpy).toHaveBeenCalledTimes(1)
    expect(window.open).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="download-error"]').attributes('role')).toBe('alert')
  })

  it('network retry followed by success opens exactly one signed URL', async () => {
    let attempts = 0
    downloadImplementation = async (config) => {
      attempts += 1
      if (attempts === 1) {
        const error = new Error('offline') as Error & { config: AxiosRequestConfig }
        error.config = config
        throw error
      }
      return downloadSuccess(config)
    }
    const { button } = await mountOpenPreview()
    vi.useFakeTimers()
    await button.trigger('click')
    await vi.runAllTimersAsync()
    await flushPromises()

    expect(attempts).toBe(2)
    expect(window.open).toHaveBeenCalledTimes(1)
    expect(window.open).toHaveBeenCalledWith(SIGNED_URL, '_blank', 'noopener,noreferrer')
  })

  it('HTTP 200 signing failure stays visible and never opens', async () => {
    downloadImplementation = async (config) => response(config, {
      success: false,
      message: 'signing unavailable',
    })
    const { wrapper, button } = await mountOpenPreview()
    await button.trigger('click')
    await flushPromises()

    expect(window.open).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="download-error"]').attributes('role')).toBe('alert')
    expect(wrapper.text()).toContain('全文内容甲')
  })

  it.each([
    '/api/v1/documents/doc-7/download',
    '//minio.test/42/scope.pdf?X-Amz-Signature=fake',
    'https://minio.test/api/v1/documents/doc-7/download',
    'javascript:alert(1)',
    'data:text/plain,not-a-download',
    'https://user:pass@minio.test/42/scope.pdf?X-Amz-Signature=fake',
    'https://minio.test/42/scope.pdf?X-Amz-Signature=fake&access_token=token-A',
    'https://minio.test/42/scope.pdf?X-Amz-Signature=fake&authorization=Bearer',
    'https://minio.test/42/scope.pdf?jwt=eyJhbGciOiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJVadQssw5c',
    'https://minio.test/42/scope.pdf?code=aaa.bbb.ccc',
  ])('an invalid successful URL payload must not be opened: %s', async (downloadUrl) => {
    downloadImplementation = async (config) => response(config, {
      success: true,
      data: { downloadUrl, filename: 'scope.pdf', expiresIn: 3600 },
    })
    const { wrapper, button } = await mountOpenPreview()
    await button.trigger('click')
    await flushPromises()

    expect(window.open).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="download-error"]').attributes('role')).toBe('alert')
  })

  it('allows normal MinIO signing parameters including X-Amz-Security-Token', async () => {
    const signedUrl = (
      'https://minio.test/42/scope.pdf?X-Amz-Signature=fake'
      + '&X-Amz-Credential=AKIA%2F20261027&X-Amz-Date=20261027T000000Z'
      + '&X-Amz-Expires=3600&X-Amz-SignedHeaders=host&X-Amz-Security-Token=session-token'
    )
    downloadImplementation = async (config) => response(config, {
      success: true,
      data: { downloadUrl: signedUrl, filename: 'scope.pdf', expiresIn: 3600 },
    })
    const { wrapper, button } = await mountOpenPreview()
    await button.trigger('click')
    await flushPromises()

    expect(window.open).toHaveBeenCalledTimes(1)
    expect(window.open).toHaveBeenCalledWith(signedUrl, '_blank', 'noopener,noreferrer')
    expect(wrapper.find('[data-testid="download-error"]').exists()).toBe(false)
  })
})

describe('independent R2 download boundary probes', () => {
  it('old A error and finally cannot clear the loading state of an in-flight B request', async () => {
    let rejectA!: () => void
    let finishB!: () => void
    downloadImplementation = (config) => new Promise<AxiosResponse>((_resolve, reject) => {
      rejectA = () => {
        try {
          httpError(config, 500, { message: 'late A failure' })
        } catch (error) {
          reject(error)
        }
      }
    })
    const { wrapper, button, userStore } = await mountOpenPreview()
    await button.trigger('click')
    await loginAsB(userStore)

    downloadImplementation = (config) => new Promise<AxiosResponse>((resolve) => {
      finishB = () => resolve(response(config, {
        success: true,
        data: { downloadUrl: SIGNED_URL_B, filename: 'b.pdf', expiresIn: 3600 },
      }))
    })
    await button.trigger('click')
    rejectA()
    await flushPromises()

    expect(button.attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-testid="download-error"]').exists()).toBe(false)
    expect(window.open).not.toHaveBeenCalled()
    finishB()
    await flushPromises()
    expect(vi.mocked(window.open).mock.calls.map((call) => call[0])).toEqual([SIGNED_URL_B])
    expect(button.attributes('disabled')).toBeUndefined()
  })

  it('allows a realistic encoded temporary X-Amz-Security-Token', async () => {
    const signedUrl = (
      'https://minio.test/42/scope.pdf?X-Amz-Algorithm=AWS4-HMAC-SHA256'
      + '&X-Amz-Credential=AKIA%2F20261027&X-Amz-Date=20261027T000000Z'
      + '&X-Amz-Expires=3600&X-Amz-SignedHeaders=host'
      + '&X-Amz-Security-Token=FwoGZXIvYXdzEBQaDExampleToken1234567890%2B%2F%3D%3D'
      + '&X-Amz-Signature=0123456789abcdef'
    )
    downloadImplementation = async (config) => response(config, {
      success: true,
      data: { downloadUrl: signedUrl, filename: 'scope.pdf', expiresIn: 3600 },
    })
    const { wrapper, button } = await mountOpenPreview()
    await button.trigger('click')
    await flushPromises()
    expect(window.open).toHaveBeenCalledWith(signedUrl, '_blank', 'noopener,noreferrer')
    expect(wrapper.find('[data-testid="download-error"]').exists()).toBe(false)
  })

  it.each([
    'https://backend.test/%61%70%69/v1/documents/doc-7/download',
    'https://backend.test//api/v1/documents/doc-7/download',
    'https://backend.test/api%2Fv1%2Fdocuments%2Fdoc-7%2Fdownload',
    'https://minio.test/42/eyJhbGciOiJ9%2EeyJzdWIiOiIxIn0%2ESflKxwRJSMeKKF2QT4fwpMeJf36POk6yJVadQssw5c',
    'https://eyJhbGciOiJ9.eyJzdWIiOiIxIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJVadQssw5c/42/scope.pdf',
    'https://eyJhbGciOiJ9.eyJzdWIiOiIxIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJVadQssw5c./42/scope.pdf',
  ])('encoded or relocated protected credentials must not bypass validation: %s', async (downloadUrl) => {
    downloadImplementation = async (config) => response(config, {
      success: true,
      data: { downloadUrl, filename: 'scope.pdf', expiresIn: 3600 },
    })
    const { wrapper, button } = await mountOpenPreview()
    await button.trigger('click')
    await flushPromises()
    expect(window.open).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="download-error"]').attributes('role')).toBe('alert')
  })
})
