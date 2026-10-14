/**
 * W2-T7 项 1：个人设置 LLM Provider 诚实化（第三轮终检 §2.4「配置了却没反应」）。
 *
 * 现状（修前）：① settingsChanged 只看语言/主题 → 改 provider 字段保存按钮不亮；
 * ② 保存时把 providerForm 整体塞进 preferences.ai_provider 落库，但后端全局
 *    无任何服务消费该字段（推理只走 .env 全局 DeepSeek）——纯陷阱配置；
 * ③ GET /settings/providers 返回的 api_key 是掩码值，若随保存回写还会覆盖真 key。
 * 决策（接线成本评估：rag_service 全局单例被 agent/core_tools/memory/delegation/
 * workflow 等 8+ 模块消费，真接线远超半天且涉 SSRF/明文密钥评审）→ 按任务书兜底
 * 走「实验功能·暂不生效」诚实标注 + 禁用保存路径。
 *
 * 变异点：
 * - 保存 payload 重新带上 preferences.ai_provider → 用例 1 红；
 * - 删掉实验标注/不置 disabled → 用例 2/3 红。
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
    getAuditLogs: vi.fn().mockResolvedValue({ data: { success: true, data: { items: [], total: 0 } } }),
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

const fetchMock = vi.fn().mockResolvedValue({
  ok: true,
  json: async () => ({
    providers: [
      { id: 'deepseek', name: 'DeepSeek', base_url: 'https://api.deepseek.com/v1', models: ['deepseek-chat'], requires_key: true },
    ],
    current: { provider: 'deepseek', model: 'deepseek-chat', api_key: 'sk-a...zzzz', base_url: '' },
  }),
})

let capturedApi: ReturnType<typeof useProfilePage>

function mountHarness() {
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
  return mount(Host, { global: { plugins: [pinia, i18n, naive] } })
}

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock)
  fetchMock.mockClear()
})

describe('W2-T7 · LLM Provider 保存路径（掩码密钥不得回写）', () => {
  it('保存不再携带 ai_provider preferences（后端零消费 + 掩码回写风险一并拆除）', async () => {
    mountHarness()
    const spy = vi.spyOn(capturedApi.userStore, 'updateSettings').mockResolvedValue({ success: true } as never)
    capturedApi.providerForm.api_key = 'sk-secret-123456'
    capturedApi.providerForm.provider = 'deepseek'

    await capturedApi.handleSaveSettings()

    expect(spy).toHaveBeenCalledTimes(1)
    const payload = spy.mock.calls[0][0] as Record<string, unknown>
    expect(payload, `保存载荷不得再含 preferences（修前含 ai_provider）: ${JSON.stringify(payload)}`)
      .not.toHaveProperty('preferences')
    expect(payload).toHaveProperty('language')
    expect(payload).toHaveProperty('theme')
  })
})

describe('W2-T7 · LLM Provider 区块诚实标注 + 禁用（页面）', () => {
  function mountPage() {
    const pinia = createPinia()
    setActivePinia(pinia)
    const Host = defineComponent({
      render: () =>
        h(NDialogProvider, null, { default: () => h(NMessageProvider, null, { default: () => h(ProfilePage) }) }),
    })
    return mount(Host, { global: { plugins: [pinia, i18n, naive] } })
  }

  // n-tabs 懒渲染：provider 区块在「偏好设置」tab 内，先切换再断言
  async function openPreferencesTab(wrapper: ReturnType<typeof mountPage>) {
    const tab = wrapper.findAll('.n-tabs-tab').find(t => t.text().includes('偏好设置'))
    expect(tab, '「偏好设置」tab 应在屏（provider 区块宿主）').toBeTruthy()
    await tab!.trigger('click')
    await flushPromises()
  }

  it('实验标注在屏：区块明示「暂不生效」', async () => {
    const wrapper = mountPage()
    await flushPromises()
    await flushPromises()
    await openPreferencesTab(wrapper)

    const text = wrapper.text()
    expect(text).toContain('实验功能')
    expect(text).toContain('暂不生效')
  })

  it('provider 表单控件全部 disabled（修复"能改但改了没用"的陷阱感）', async () => {
    const wrapper = mountPage()
    await flushPromises()
    await flushPromises()
    await openPreferencesTab(wrapper)

    // 两个 n-select（provider/model）禁用：naive 内部渲染 n-base-selection--disabled
    expect(wrapper.findAll('.n-base-selection--disabled').length).toBeGreaterThanOrEqual(2)
    // API Key 输入（placeholder=sk-...，全页唯一）禁用
    const pwd = wrapper.find('input[placeholder="sk-..."]')
    expect(pwd.exists()).toBe(true)
    expect(pwd.attributes('disabled')).toBeDefined()
  })
})
