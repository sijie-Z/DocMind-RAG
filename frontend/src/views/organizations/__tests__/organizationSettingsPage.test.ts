/**
 * B4：管理端「组织设置」页。
 *
 * 契约：
 * - 六个设定项都以「生效值 / 来源 / 是否需重启」三列展示；
 * - 保存只发送真正变化的字段（读一次默认值不应写出组织覆盖）；
 * - 保存后刷新审计列表。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import naive, { NMessageProvider, NInputNumber, NButton } from 'naive-ui'

vi.mock('@/api/organization', () => ({
  getOrganizationSettings: vi.fn(),
  getOrganizationSettingsAudit: vi.fn(),
  updateOrganizationSettings: vi.fn(),
}))

import {
  getOrganizationSettings,
  getOrganizationSettingsAudit,
  updateOrganizationSettings,
} from '@/api/organization'
import SettingsPage from '../settings.vue'
import { useUserStore } from '@/stores/user'
import zh from '@/locales/zh'

const mockedGet = vi.mocked(getOrganizationSettings)
const mockedAudit = vi.mocked(getOrganizationSettingsAudit)
const mockedUpdate = vi.mocked(updateOrganizationSettings)

const baseSettings = {
  organization_id: 7,
  settings: {
    default_provider: { value: 'deepseek', source: 'default', requires_restart: true },
    default_model: { value: 'deepseek-v4-flash', source: 'default', requires_restart: false },
    retrieval_top_k: { value: 10, source: 'default', requires_restart: false },
    allow_privacy_mode: { value: true, source: 'default', requires_restart: false },
    monthly_token_quota: { value: null, source: 'default', requires_restart: false },
    tool_whitelist: { value: null, source: 'default', requires_restart: false },
  },
  usage: { month_tokens: 0, monthly_token_quota: null },
}

const i18n = createI18n({
  legacy: false,
  locale: 'zh',
  missingWarn: false,
  fallbackWarn: false,
  messages: { zh },
})

const Host = defineComponent({
  setup() {
    return () => h(NMessageProvider, null, { default: () => h(SettingsPage) })
  },
})

async function mountPage() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const user = useUserStore()
  user.setCurrentOrganization(7)
  const wrapper = mount(Host, { global: { plugins: [pinia, naive, i18n] } })
  await flushPromises()
  return wrapper
}

describe('organization settings page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedGet.mockResolvedValue({ data: baseSettings } as never)
    mockedAudit.mockResolvedValue({ data: { items: [] } } as never)
    mockedUpdate.mockResolvedValue({ data: baseSettings } as never)
  })

  it('展示生效值 / 来源 / 是否需重启', async () => {
    const wrapper = await mountPage()
    expect(mockedGet).toHaveBeenCalledWith(7)

    const topK = wrapper.find('[data-testid="effective-retrieval_top_k"]')
    expect(topK.exists()).toBe(true)
    expect(topK.text()).toContain('10')
    expect(topK.text()).toContain('平台默认')
    expect(topK.text()).toContain('即时生效')

    const provider = wrapper.find('[data-testid="effective-default_provider"]')
    expect(provider.text()).toContain('需重启')
  })

  it('切换组织后按新组织重新加载', async () => {
    const wrapper = await mountPage()
    expect(mockedGet).toHaveBeenCalledWith(7)

    useUserStore().setCurrentOrganization(8)
    await flushPromises()
    expect(mockedGet).toHaveBeenCalledWith(8)
    wrapper.unmount()
  })

  it('保存只发送真正变化的字段', async () => {
    const wrapper = await mountPage()

    // 第一个 NInputNumber 是检索 top_k
    const numbers = wrapper.findAllComponents(NInputNumber)
    numbers[0].vm.$emit('update:value', 3)
    await nextTick()

    const saveButton = wrapper
      .findAllComponents(NButton)
      .find((button) => button.text().includes(zh.orgSettings.save))
    expect(saveButton).toBeTruthy()
    await saveButton!.trigger('click')
    await flushPromises()

    expect(mockedUpdate).toHaveBeenCalledWith(7, { retrieval_top_k: 3 })
    expect(mockedAudit).toHaveBeenCalledTimes(2)
  })
})
