/**
 * B3 · Profile 页死设定清理（任务书「四、B 批」B3）。
 *
 * 两处死设定：
 *  1. 个人 API Key 管理（安全设置 tab）：UI 可生成/撤销，但没有任何消费方
 *     使用该 key（仓库内无鉴权/网关读取 user.api_key），纯「配置了也没用」。
 *     修复：移除 Profile UI 入口 + 前端 API 通道（后端端点保留一个 minor，
 *     仅标 deprecated）。
 *  2. 「信任此设备」开关（设备管理 tab）：修前 toggleTrust 只往 localStorage
 *     写一份 JSON 集合并弹 toast，后端无任何设备信任逻辑读取它——纯本地假开关。
 *     修复：默认删除（未发现真实设备信任逻辑可接）。
 *
 * 变异点：
 *  - Profile 恢复 API Key 管理区块 / user.ts 恢复 generateApiKey|revokeApiKey → 红；
 *  - 会话操作列恢复「信任/取消信任」按钮或 localStorage.trustedDevices 读写 → 红。
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
    // 设备管理 tab 要渲染出真实操作列（否则「信任」按钮无从出现，用例无判别力）
    getMySessions: vi.fn().mockResolvedValue({
      data: [
        {
          id: 7, device_name: 'Windows PC', user_agent: 'Chrome/120 Windows',
          ip_address: '127.0.0.1', is_active: true,
          last_seen_at: '2026-10-18T10:00:00', created_at: '2026-10-18T09:00:00',
        },
      ],
    }),
    getMyActivityLogs: vi.fn().mockResolvedValue({ data: [] }),
    updateUserProfile: vi.fn(),
    updatePassword: vi.fn(),
    uploadAvatar: vi.fn(),
    revokeMySession: vi.fn(),
    getUserSettingsProviders: vi.fn().mockResolvedValue({
      data: {
        success: true,
        providers: [
          { id: 'deepseek', name: 'DeepSeek', base_url: 'https://api.deepseek.com/v1', models: ['deepseek-chat'], requires_key: true },
        ],
        current: { provider: 'deepseek', model: 'deepseek-chat', api_key: 'sk-a...zzzz', base_url: '' },
      },
    }),
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

import * as userApi from '@/api/user'

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

function mountPage() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const Host = defineComponent({
    render: () =>
      h(NDialogProvider, null, { default: () => h(NMessageProvider, null, { default: () => h(ProfilePage) }) }),
  })
  return mount(Host, { global: { plugins: [pinia, i18n, naive] } })
}

async function openTab(wrapper: ReturnType<typeof mountPage>, label: string) {
  const tab = wrapper.findAll('.n-tabs-tab').find(t => t.text().includes(label))
  expect(tab, `「${label}」tab 应在屏`).toBeTruthy()
  await tab!.trigger('click')
  await flushPromises()
  await flushPromises()
}

let capturedApi: ReturnType<typeof useProfilePage>

function mountComposableHarness() {
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
  localStorage.clear()
})

describe('B3 · 个人 API Key 入口移除', () => {
  it('安全设置 tab 不再渲染 API Key 管理区块与操作按钮', async () => {
    const wrapper = mountPage()
    await flushPromises()
    await openTab(wrapper, '账号安全')

    const text = wrapper.text()
    expect(text, '修前含「API Key 管理」区块').not.toContain('API Key 管理')
    expect(text, '修前含「生成 Key」按钮').not.toContain('生成 Key')
    expect(text, '修前含「重新生成」按钮').not.toContain('重新生成')
    expect(text, '修前含「撤销 Key」按钮').not.toContain('撤销 Key')
    expect(text, '修前含「暂无 API Key」').not.toContain('暂无 API Key')
  })

  it('前端不再持有发起 /users/api-key 死请求的 API 通道与组合式导出', () => {
    mountComposableHarness()
    expect('generateApiKey' in userApi, 'api/user.ts 仍导出 generateApiKey（POST /users/api-key 死请求通道）').toBe(false)
    expect('revokeApiKey' in userApi, 'api/user.ts 仍导出 revokeApiKey（DELETE /users/api-key 死请求通道）').toBe(false)
    expect(capturedApi).not.toHaveProperty('generateApiKey')
    expect(capturedApi).not.toHaveProperty('revokeApiKey')
    expect(capturedApi).not.toHaveProperty('copyApiKey')
    expect(capturedApi).not.toHaveProperty('apiKey')
  })
})

describe('B3 · 「信任此设备」假开关移除', () => {
  it('设备管理 tab 不再渲染「信任/取消信任」按钮与「已信任」徽标', async () => {
    const wrapper = mountPage()
    await flushPromises()
    await openTab(wrapper, '设备管理')

    const text = wrapper.text()
    expect(text, '会话行应已渲染（否则本断言无判别力）').toContain('Windows PC')
    expect(text, '修前操作列含「信任」按钮').not.toContain('信任')
    expect(text).not.toContain('已信任')
  })

  it('组合式不再导出 trustedDevices/toggleTrust，且加载流程不再读写 localStorage.trustedDevices', async () => {
    localStorage.setItem('trustedDevices', JSON.stringify([7]))
    mountComposableHarness()
    await flushPromises()

    expect(capturedApi).not.toHaveProperty('trustedDevices')
    expect(capturedApi).not.toHaveProperty('toggleTrust')
    // 修前 loadAllData 会把 localStorage 里的集合读进状态并原样保留；
    // 修后该 key 不再被本页消费（清理与否都不影响页面，不做强断言）。
    expect(capturedApi).not.toHaveProperty('toggleTrust')
  })
})
