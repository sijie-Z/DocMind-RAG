/**
 * 个人设置 providers 路径判别用例（UI 验收 round2 · P1 / A_sweep PROF-1）。
 *
 * 真 UI 实测：偏好设置 → AI 模型设置 拉 providers 时
 *   GET /api/v1/settings/providers → 404（缺 /user 前缀）；
 *   GET /api/v1/user/settings/providers → 200（正确路径）。
 * 路由真源：router.py:51 prefix="/user" + user_settings.py:117。
 *
 * 变异点：路径改回缺 /user 前缀 → 本用例精确红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import { NMessageProvider, NDialogProvider } from 'naive-ui'

import { useProfilePage } from '../composables/useProfilePage'
import zh from '@/locales/zh'

vi.mock('@/api/user', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/user')>()
  return {
    ...actual,
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
    generateApiKey: vi.fn(),
    revokeApiKey: vi.fn(),
    revokeMySession: vi.fn(),
  }
})
vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock('@/utils/echarts', () => ({
  default: {
    init: vi.fn(() => ({ setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() })),
  },
}))

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

const fetchMock = vi.fn().mockResolvedValue({
  ok: true,
  json: async () => ({ providers: ['deepseek', 'ollama'], current: null }),
})

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

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock)
  fetchMock.mockClear()
})

describe('useProfilePage · providers 请求路径（P1）', () => {
  it('请求 /api/v1/user/settings/providers（旧缺 /user 前缀 404 已修）', async () => {
    mountHarness()
    await flushPromises()

    await capturedApi.loadProviders()
    await flushPromises()

    const urls = fetchMock.mock.calls.map((c) => String(c[0]))
    expect(urls).toContain('/api/v1/user/settings/providers')
    // 缺前缀的旧路径不得再出现
    expect(urls).not.toContain('/api/v1/settings/providers')
  })

  it('响应 providers/current 正常落入页面状态（未静默吞掉 200 直连）', async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({ providers: ['deepseek', 'ollama'], current: { provider: 'deepseek', model: 'deepseek-chat', api_key: '', base_url: '' } }),
    } as never)

    mountHarness()
    await flushPromises()
    await capturedApi.loadProviders()
    await flushPromises()

    const state = capturedApi as unknown as {
      providers: { value: string[] }
      providerForm: { provider: string; model: string }
    }
    expect(state.providers.value).toEqual(['deepseek', 'ollama'])
    expect(state.providerForm.provider).toBe('deepseek')
  })
})
