/**
 * 个人设置「AI 模型设置」真实生效（替代 W2-T7 的「实验功能·暂不生效」标注）。
 *
 * 本批要求：选择 provider/model/API key/base_url 后 Chat（RAG）与 Agent 的真实
 * LLM 调用使用该配置。前端职责：
 * - 控件启用（不再 disabled）、警告移除；
 * - 保存走 PUT /user/settings/providers（独立于语言/主题保存）；
 * - GET 回读的掩码 key 绝不作为新 key 回写（掩码时提交空串，后端保留真实 key）；
 * - 「使用服务端默认」提交 use_server_default=true；
 * - 保存后展示实际生效来源；四语词条齐备。
 *
 * 变异点：
 * - 控件重新 disabled → 用例 1 红；
 * - 保存仍把掩码 key 回写 → 用例 2 红；
 * - use_server_default 未提交 → 用例 3 红；
 * - 任一语缺词条 → 用例 4 红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import naive, { NMessageProvider, NDialogProvider } from 'naive-ui'

import { useProfilePage } from '../composables/useProfilePage'
import ProfilePage from '../index.vue'
import { getUserSettingsProviders } from '@/api/user'
import zh from '@/locales/zh'
import en from '@/locales/en'
import ja from '@/locales/ja'
import fr from '@/locales/fr'

const { updateProvidersMock } = vi.hoisted(() => ({ updateProvidersMock: vi.fn() }))

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
    revokeMySession: vi.fn(),
    getUserSettingsProviders: vi.fn().mockResolvedValue({
      data: {
        success: true,
        providers: [
          { id: 'deepseek', name: 'DeepSeek-backend', base_url: 'https://api.deepseek.com/v1', models: ['deepseek-chat'], requires_key: true },
          { id: 'ollama', name: 'Ollama-backend (本地)', base_url: 'http://localhost:11434/v1', models: ['llama3'], requires_key: false },
          { id: 'openai', name: 'OpenAI-backend', base_url: 'https://api.openai.com/v1', models: ['gpt-4o'], requires_key: true },
          { id: 'zhipu', name: '智谱后端', base_url: 'https://open.bigmodel.cn/api/paas/v4', models: ['glm-4-flash'], requires_key: true },
          { id: 'custom', name: 'Custom-backend (OpenAI compatible)', base_url: '', models: [], requires_key: true },
          { id: 'azure', name: 'Azure OpenAI (backend)', base_url: '', models: [], requires_key: true },
        ],
        current: {
          provider: 'deepseek', model: 'deepseek-chat', api_key: 'sk-a...zzzz',
          base_url: '', has_api_key: true, source: 'user',
        },
      },
    }),
    updateUserSettingsProviders: updateProvidersMock,
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
const i18nEn = createI18n({ legacy: false, locale: 'en', messages: { en } })

let capturedApi: ReturnType<typeof useProfilePage>

function mountHarness(localeI18n = i18n) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const Harness = defineComponent({
    setup() {
      capturedApi = useProfilePage()
      return () => null
    },
  })
  const Host = defineComponent({
    render: () =>
      h(NDialogProvider, null, { default: () => h(NMessageProvider, null, { default: () => h(Harness) }) }),
  })
  return mount(Host, { global: { plugins: [pinia, localeI18n, naive] } })
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

beforeEach(() => {
  updateProvidersMock.mockReset()
  updateProvidersMock.mockResolvedValue({
    data: {
      success: true,
      current: {
        provider: 'deepseek', model: 'deepseek-chat', api_key: 'sk-a...zzzz',
        base_url: '', has_api_key: true, source: 'user',
      },
    },
  })
})

describe('AI 模型设置 · 页面启用（警告移除 + 控件可编辑）', () => {
  it('不再出现「实验功能·暂不生效」警告', async () => {
    const wrapper = mountPage()
    await flushPromises()
    await flushPromises()
    await openPreferencesTab(wrapper)

    const text = wrapper.text()
    expect(text).not.toContain('实验功能')
    expect(text).not.toContain('暂不生效')
    expect(text).toContain('AI 模型设置')
  })

  it('provider/model/API Key 控件可用（不再 disabled），并有保存按钮', async () => {
    const wrapper = mountPage()
    await flushPromises()
    await flushPromises()
    await openPreferencesTab(wrapper)

    // source=user 时不应有禁用态 select
    expect(wrapper.findAll('.n-base-selection--disabled').length).toBe(0)
    const pwd = wrapper.find('input[placeholder="sk-..."]')
    expect(pwd.exists()).toBe(true)
    expect(pwd.attributes('disabled')).toBeUndefined()
    expect(wrapper.text()).toContain('保存模型设置')
  })
})

describe('AI 模型设置 · 保存路径', () => {
  it('保存调用 PUT providers，掩码 key 提交空串（不得回写覆盖真实 key）', async () => {
    mountHarness()
    await flushPromises()
    await flushPromises()

    expect(capturedApi.providerForm.api_key).toBe('sk-a...zzzz')
    await capturedApi.handleSaveProviders()

    expect(updateProvidersMock).toHaveBeenCalledTimes(1)
    const payload = updateProvidersMock.mock.calls[0][0] as Record<string, unknown>
    expect(payload.provider).toBe('deepseek')
    expect(payload.model).toBe('deepseek-chat')
    expect(payload.api_key, '掩码 key 不得作为新 key 回写').toBe('')
    expect(payload).not.toHaveProperty('use_server_default')
    expect(capturedApi.providerSaveSuccess.value).toBe(true)
  })

  it('输入新 key 时按明文提交（由后端加密落库）', async () => {
    mountHarness()
    await flushPromises()
    await flushPromises()

    capturedApi.providerForm.api_key = 'sk-new-key-987654321'
    await capturedApi.handleSaveProviders()

    const payload = updateProvidersMock.mock.calls[0][0] as Record<string, unknown>
    expect(payload.api_key).toBe('sk-new-key-987654321')
  })

  it('切换 provider 清空旧 key 掩码；切回已保存 provider 恢复掩码', async () => {
    mountHarness()
    await flushPromises()
    await flushPromises()

    expect(capturedApi.providerForm.api_key).toBe('sk-a...zzzz')
    capturedApi.onProviderChange('openai')
    expect(capturedApi.providerForm.api_key, '切换 provider 不得复用旧 key 掩码').toBe('')
    expect(capturedApi.providerForm.hasApiKey).toBe(false)

    // 新 provider 未填 key：保存被拦截，不得把空 key 发给后端
    capturedApi.providerForm.provider = 'openai'
    capturedApi.providerForm.model = 'gpt-4o'
    await capturedApi.handleSaveProviders()
    expect(updateProvidersMock).not.toHaveBeenCalled()

    capturedApi.onProviderChange('deepseek')
    expect(capturedApi.providerForm.api_key).toBe('sk-a...zzzz')
    expect(capturedApi.providerForm.hasApiKey).toBe(true)
  })

  it('切回已保存的 custom provider 恢复 model/base_url/掩码', async () => {
    vi.mocked(getUserSettingsProviders).mockResolvedValueOnce({
      data: {
        success: true,
        providers: [
          { id: 'deepseek', name: 'DeepSeek-backend', base_url: 'https://api.deepseek.com/v1', models: ['deepseek-chat'], requires_key: true },
          { id: 'ollama', name: 'Ollama-backend (本地)', base_url: 'http://localhost:11434/v1', models: ['llama3'], requires_key: false },
          { id: 'openai', name: 'OpenAI-backend', base_url: 'https://api.openai.com/v1', models: ['gpt-4o'], requires_key: true },
          { id: 'zhipu', name: '智谱后端', base_url: 'https://open.bigmodel.cn/api/paas/v4', models: ['glm-4-flash'], requires_key: true },
          { id: 'custom', name: 'Custom-backend (OpenAI compatible)', base_url: '', models: [], requires_key: true },
          { id: 'azure', name: 'Azure OpenAI (backend)', base_url: '', models: [], requires_key: true },
        ],
        current: {
          provider: 'custom', model: 'my-model', api_key: 'sk-a...zzzz',
          base_url: 'http://localhost:9999/v1', has_api_key: true, source: 'user',
          has_saved_config: true,
        },
      },
    } as never)
    mountHarness()
    await flushPromises()
    await flushPromises()

    expect(capturedApi.providerForm.model).toBe('my-model')
    expect(capturedApi.providerForm.base_url).toBe('http://localhost:9999/v1')

    capturedApi.onProviderChange('deepseek')
    expect(capturedApi.providerForm.model).toBe('deepseek-chat')
    expect(capturedApi.providerForm.api_key).toBe('')

    capturedApi.onProviderChange('custom')
    expect(capturedApi.providerForm.model).toBe('my-model')
    expect(capturedApi.providerForm.base_url).toBe('http://localhost:9999/v1')
    expect(capturedApi.providerForm.api_key).toBe('sk-a...zzzz')
  })

  it('保存了但不可用：提示回退且不勾选服务端默认', async () => {
    const fallbackPayload = {
      success: true,
      providers: [
        { id: 'deepseek', name: 'DeepSeek', base_url: 'https://api.deepseek.com/v1', models: ['deepseek-chat'], requires_key: true },
      ],
      current: {
        provider: 'deepseek', model: 'deepseek-chat', api_key: '',
        base_url: '', has_api_key: false, source: 'server',
        has_saved_config: true, fallback_reason: 'missing_key',
      },
    }
    vi.mocked(getUserSettingsProviders).mockResolvedValueOnce({ data: fallbackPayload } as never)
    mountHarness()
    await flushPromises()
    await flushPromises()

    expect(capturedApi.providerForm.source).toBe('server')
    expect(capturedApi.providerForm.useServerDefault).toBe(false)
    expect(capturedApi.providerForm.hasSavedConfig).toBe(true)

    vi.mocked(getUserSettingsProviders).mockResolvedValueOnce({ data: fallbackPayload } as never)
    const wrapper = mountPage()
    await flushPromises()
    await flushPromises()
    await openPreferencesTab(wrapper)
    expect(wrapper.find('[data-testid="provider-fallback-hint"]').exists()).toBe(true)
  })

  it('「使用服务端默认」提交 use_server_default=true', async () => {
    mountHarness()
    await flushPromises()
    await flushPromises()

    capturedApi.providerForm.useServerDefault = true
    await capturedApi.handleSaveProviders()

    const payload = updateProvidersMock.mock.calls[0][0] as Record<string, unknown>
    expect(payload).toEqual({ use_server_default: true })
  })

  it('保存失败时如实提示错误（不静默）', async () => {
    updateProvidersMock.mockRejectedValueOnce(
      Object.assign(new Error('bad request'), {
        response: { data: { detail: '该提供商需要 API Key' } },
      }),
    )
    mountHarness()
    await flushPromises()
    await flushPromises()

    await capturedApi.handleSaveProviders()
    expect(capturedApi.providerSaveSuccess.value).toBe(false)
    expect(capturedApi.providerSaveMessage.value).toContain('API Key')
  })
})

describe('AI 模型设置 · 四语词条齐备', () => {
  const keys = [
    'aiProviderTitle',
    'aiProviderDesc',
    'aiProviderUseServerDefault',
    'aiProviderUseServerDefaultDesc',
    'aiProviderProvider',
    'providerDeepseek',
    'providerOllama',
    'providerOpenai',
    'providerZhipu',
    'providerCustom',
    'aiProviderModel',
    'aiProviderKeyHint',
    'aiProviderSave',
    'aiProviderSavedUser',
    'aiProviderSavedServer',
    'aiProviderSaveFailed',
    'aiProviderModelRequired',
    'aiProviderKeyRequired',
    'aiProviderSourceUser',
    'aiProviderSourceServer',
  ] as const

  for (const [name, messages] of Object.entries({ zh, en, ja, fr })) {
    it(`${name} 含全部 AI 模型设置词条`, () => {
      const profile = (messages as unknown as { profile: Record<string, string> }).profile
      for (const key of keys) {
        expect(profile[key], `${name} 缺少 profile.${key}`).toBeTruthy()
        expect(profile[key]).not.toBe(`profile.${key}`)
      }
    })
  }

  it('provider 名称按当前语言本地化（zh），未知 id 回退后端 name', async () => {
    mountHarness()
    await flushPromises()
    await flushPromises()
    const labels = capturedApi.providerOptions.value
    const labelOf = (id: string) => labels.find((option) => option.value === id)?.label
    expect(labelOf('deepseek')).toBe('DeepSeek')
    expect(labelOf('ollama')).toBe('Ollama（本地）')
    expect(labelOf('openai')).toBe('OpenAI')
    expect(labelOf('zhipu')).toBe('智谱 GLM')
    expect(labelOf('custom')).toBe('自定义（OpenAI 兼容）')
    expect(labelOf('azure')).toBe('Azure OpenAI (backend)')
  })

  it('provider 名称在 en 下本地化，未知 id 回退后端 name', async () => {
    mountHarness(i18nEn as unknown as typeof i18n)
    await flushPromises()
    await flushPromises()
    const labels = capturedApi.providerOptions.value
    const labelOf = (id: string) => labels.find((option) => option.value === id)?.label
    expect(labelOf('deepseek')).toBe('DeepSeek')
    expect(labelOf('ollama')).toBe('Ollama (local)')
    expect(labelOf('openai')).toBe('OpenAI')
    expect(labelOf('zhipu')).toBe('Zhipu GLM')
    expect(labelOf('custom')).toBe('Custom (OpenAI-compatible)')
    expect(labelOf('azure')).toBe('Azure OpenAI (backend)')
  })
})
