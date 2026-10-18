/**
 * 个人设置 providers 加载判别用例（UI 验收 round2 · P1 / A_sweep PROF-1；T10 错误态）。
 *
 * 真 UI 实测：偏好设置 → AI 模型设置 拉 providers 时
 *   GET /api/v1/settings/providers → 404（缺 /user 前缀）；
 *   GET /api/v1/user/settings/providers → 200（正确路径）。
 * 路由真源：router.py:51 prefix="/user" + user_settings.py:117。
 *
 * T10：加载改走 utils/request.ts 统一客户端（api/user.ts getUserSettingsProviders），
 * 不再裸 fetch；失败时错误态可判别、页面可见，且保留上一次成功值。
 *
 * 变异点：
 * - 退回裸 fetch → 用例 1 红（fetch 不得被调用）；
 * - 失败静默 / 失败清空旧值 → 用例 3 红；
 * - 页面不显示错误提示 → 用例 4 红；
 * - 路径改回缺 /user 前缀 → api/__tests__/userSettingsProviders.test.ts 红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import naive, { NMessageProvider, NDialogProvider } from 'naive-ui'

import { useProfilePage } from '../composables/useProfilePage'
import ProfilePage from '../index.vue'
import zh from '@/locales/zh'

const { providersMock } = vi.hoisted(() => ({ providersMock: vi.fn() }))

vi.mock('@/api/user', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/user')>()
  return {
    ...actual,
    getUserSettingsProviders: providersMock,
    getUserProfile: vi.fn().mockResolvedValue({
      data: {
        id: 1, username: 'tester', email: 't@t', nickname: 'tester', full_name: null,
        phone: null, avatar_url: null, bio: null, organization_id: 3, role: 'user',
        is_active: true, created_at: '2026-10-08T05:39:53', last_login_at: null,
      },
    }),
    getUserStats: vi.fn().mockResolvedValue({
      data: { conversation_count: 0, message_count: 0, file_count: 0, knowledge_count: 0, storage_used: 0 },
    }),
    getUserActivities: vi.fn().mockResolvedValue({ data: [] }),
    getMySessions: vi.fn().mockResolvedValue({ data: [] }),
    getMyActivityLogs: vi.fn().mockResolvedValue({ data: [] }),
    updateUserProfile: vi.fn(),
    updatePassword: vi.fn(),
    uploadAvatar: vi.fn(),
    revokeMySession: vi.fn(),
  }
})
vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock('@/utils/echarts', () => ({
  default: {
    init: vi.fn(() => ({ setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() })),
    graphic: { LinearGradient: class {} },
  },
}))

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

const okPayload = {
  data: {
    success: true,
    providers: [
      { id: 'deepseek', name: 'DeepSeek', base_url: 'https://api.deepseek.com/v1', models: ['deepseek-chat'], requires_key: true },
      { id: 'ollama', name: 'Ollama', base_url: 'http://localhost:11434/v1', models: ['llama3'], requires_key: false },
    ],
    current: { provider: 'deepseek', model: 'deepseek-chat', api_key: '', base_url: '' },
  },
}

const fetchMock = vi.fn()

let capturedApi: ReturnType<typeof useProfilePage>

const Harness = defineComponent({
  setup() {
    capturedApi = useProfilePage()
    return () => null
  },
})

function mountHarness() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const Host = defineComponent({
    render: () =>
      h(NDialogProvider, null, { default: () => h(NMessageProvider, null, { default: () => h(Harness) }) }),
  })
  return mount(Host, { global: { plugins: [pinia, i18n] } })
}

function mountPage() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const Host = defineComponent({
    render: () =>
      h(NDialogProvider, null, { default: () => h(NMessageProvider, null, { default: () => h(ProfilePage) }) }),
  })
  return mount(Host, { global: { plugins: [pinia, i18n, naive] } })
}

async function openPreferencesTab(wrapper: ReturnType<typeof mountPage>) {
  const tab = wrapper.findAll('.n-tabs-tab').find(t => t.text().includes('偏好设置'))
  expect(tab, '「偏好设置」tab 应在屏（provider 区块宿主）').toBeTruthy()
  await tab!.trigger('click')
  await flushPromises()
}

interface CapturedState {
  providers: { value: Array<{ id: string }> }
  providerForm: { provider: string; model: string }
  providersLoadError: { value: string | null }
}

beforeEach(() => {
  providersMock.mockReset()
  providersMock.mockResolvedValue(okPayload as never)
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
})

describe('useProfilePage · providers 加载走统一客户端（P1）', () => {
  it('通过 api/user 的 getUserSettingsProviders 加载，不再裸 fetch', async () => {
    mountHarness()
    await flushPromises()

    expect(providersMock).toHaveBeenCalled()
    expect(fetchMock, '不得再走裸 fetch（统一客户端负责鉴权/错误提示/刷新）').not.toHaveBeenCalled()
  })

  it('响应 providers/current 正常落入页面状态（未静默吞掉 200 直连）', async () => {
    mountHarness()
    await flushPromises()
    await capturedApi.loadProviders()
    await flushPromises()

    const state = capturedApi as unknown as CapturedState
    expect(state.providers.value.map(p => p.id)).toEqual(['deepseek', 'ollama'])
    expect(state.providerForm.provider).toBe('deepseek')
    expect(state.providerForm.model).toBe('deepseek-chat')
    expect(state.providersLoadError.value).toBeNull()
  })
})

describe('useProfilePage · providers 加载失败错误态（T10）', () => {
  it('接口 500 → 显示错误态且不清空上一次成功值', async () => {
    mountHarness()
    await flushPromises()
    await capturedApi.loadProviders()
    await flushPromises()

    const state = capturedApi as unknown as CapturedState
    const before = JSON.parse(JSON.stringify(state.providers.value))
    expect(before.map((p: { id: string }) => p.id)).toEqual(['deepseek', 'ollama'])

    providersMock.mockRejectedValueOnce(
      Object.assign(new Error('Request failed with status code 500'), { response: { status: 500 } }),
    )
    await capturedApi.loadProviders()
    await flushPromises()

    expect(state.providersLoadError.value, '失败必须可判别（不得静默吞错）').toBeTruthy()
    expect(state.providers.value, '失败不得清空上一次成功值').toEqual(before)
    expect(state.providerForm.provider, '失败不得重置已选 provider').toBe('deepseek')
  })

  it('页面可见错误提示（500 时 Provider 区块出现错误态文案）', async () => {
    providersMock.mockRejectedValue(
      Object.assign(new Error('Request failed with status code 500'), { response: { status: 500 } }),
    )
    const wrapper = mountPage()
    await flushPromises()
    await flushPromises()
    await openPreferencesTab(wrapper)

    expect(wrapper.text()).toContain('Provider 配置加载失败')
  })
})
