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

// 页面通过 useDedupedMessage 呈现唯一提示；替身直接记录调用，断言「提示数量/文案」。
const messageMock = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
  loading: vi.fn(),
}))
vi.mock('@/utils/message', () => ({
  useDedupedMessage: () => messageMock,
  default: () => messageMock,
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

/** 改检索 top_k 触发一次真实变更，再点击保存。 */
async function saveWithTopKChange(wrapper: Awaited<ReturnType<typeof mountPage>>) {
  const numbers = wrapper.findAllComponents(NInputNumber)
  numbers[0].vm.$emit('update:value', 3)
  await nextTick()
  const saveButton = wrapper
    .findAllComponents(NButton)
    .find((button) => button.text().includes(zh.orgSettings.save))
  expect(saveButton).toBeTruthy()
  await saveButton!.trigger('click')
  await flushPromises()
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
    expect(mockedGet).toHaveBeenCalledWith(7, { silent: true })

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
    expect(mockedGet).toHaveBeenCalledWith(7, { silent: true })

    useUserStore().setCurrentOrganization(8)
    await flushPromises()
    expect(mockedGet).toHaveBeenCalledWith(8, { silent: true })
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
    wrapper.unmount()
  })

  it('保存成功但审计刷新失败：仍提示保存成功，审计区降级且无失败提示', async () => {
    mockedAudit.mockResolvedValue({
      data: { items: [{ id: 1, user_id: 7, action: 'settings.update', detail: 'top_k' }] },
    } as never)
    const wrapper = await mountPage()
    // 首次 audit 是页面加载；保存后的第二次刷新失败。
    mockedAudit.mockRejectedValueOnce(
      Object.assign(new Error('Request failed with status code 500'), {
        isAxiosError: true,
        response: { status: 500, data: {} },
      }),
    )

    await saveWithTopKChange(wrapper)

    expect(messageMock.success).toHaveBeenCalledWith(zh.orgSettings.saveSuccess)
    expect(messageMock.error).not.toHaveBeenCalled()
    // 审计区降级：内联提示 + 旧列表保留
    const auditAlert = wrapper.find('[data-testid="audit-error"]')
    expect(auditAlert.exists()).toBe(true)
    expect(auditAlert.text()).toContain(zh.orgSettings.auditLoadFailed)
    expect(wrapper.text()).toContain('top_k')
    // 原始 axios 文案不得上屏（既不在提示里，也不在页面里）
    const allMessageText = messageMock.error.mock.calls.flat().join(' ')
    expect(allMessageText).not.toContain('Request failed')
    expect(wrapper.text()).not.toContain('Request failed')
    wrapper.unmount()
  })

  it('保存失败：显示本地化文案且仅一条提示，不透出原始 axios 文案', async () => {
    const wrapper = await mountPage()
    mockedUpdate.mockRejectedValueOnce(
      Object.assign(new Error('Request failed with status code 502'), {
        isAxiosError: true,
        response: { status: 502, data: {} },
      }),
    )

    await saveWithTopKChange(wrapper)

    expect(messageMock.error).toHaveBeenCalledTimes(1)
    expect(messageMock.error).toHaveBeenCalledWith(zh.orgSettings.saveFailed)
    expect(messageMock.success).not.toHaveBeenCalled()
    const allMessageText = messageMock.error.mock.calls.flat().join(' ')
    expect(allMessageText).not.toContain('Request failed')
    wrapper.unmount()
  })

  it('加载时审计失败：设置照常展示，仅审计区内联降级，不谎称「暂无变更记录」', async () => {
    mockedAudit.mockRejectedValueOnce(
      Object.assign(new Error('Request failed with status code 500'), {
        isAxiosError: true,
        response: { status: 500, data: {} },
      }),
    )
    const wrapper = await mountPage()

    // 设置区照常可用
    expect(wrapper.find('[data-testid="effective-retrieval_top_k"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('10')
    // 审计区唯一降级呈现
    const auditAlert = wrapper.find('[data-testid="audit-error"]')
    expect(auditAlert.exists()).toBe(true)
    expect(auditAlert.text()).toContain(zh.orgSettings.auditLoadFailed)
    expect(wrapper.text()).not.toContain(zh.orgSettings.auditEmpty)
    expect(messageMock.error).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('审计加载中：不提前显示「暂无变更记录」', async () => {
    let resolveAudit!: (value: unknown) => void
    mockedAudit.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveAudit = resolve
      }) as never,
    )
    const wrapper = await mountPage()

    expect(wrapper.find('[data-testid="audit-loading"]').exists()).toBe(true)
    expect(wrapper.text()).not.toContain(zh.orgSettings.auditEmpty)

    resolveAudit({ data: { items: [] } })
    await flushPromises()
    expect(wrapper.find('[data-testid="audit-loading"]').exists()).toBe(false)
    expect(wrapper.text()).toContain(zh.orgSettings.auditEmpty)
    wrapper.unmount()
  })

  it('切换组织清空旧审计：旧组织晚到的响应不得覆盖新组织', async () => {
    let resolveOrg7Audit!: (value: unknown) => void
    mockedAudit.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveOrg7Audit = resolve
      }) as never,
    )
    const wrapper = await mountPage() // 组织 7 的审计保持挂起

    mockedAudit.mockResolvedValueOnce({
      data: { items: [{ id: 2, user_id: 8, action: 'settings.update', detail: 'org-8-audit' }] },
    } as never)
    useUserStore().setCurrentOrganization(8)
    await flushPromises()
    expect(wrapper.text()).toContain('org-8-audit')
    expect(wrapper.text()).not.toContain('org-7-audit')

    resolveOrg7Audit({
      data: { items: [{ id: 1, user_id: 7, action: 'settings.update', detail: 'org-7-audit' }] },
    })
    await flushPromises()
    expect(wrapper.text()).toContain('org-8-audit')
    expect(wrapper.text()).not.toContain('org-7-audit')
    wrapper.unmount()
  })
})
