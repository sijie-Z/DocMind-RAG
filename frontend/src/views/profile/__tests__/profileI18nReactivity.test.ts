/**
 * Profile 页 i18n 真·响应式判别用例。
 *
 * 修前：设备/审计表格列、主题选项、快捷入口文案是 setup 期一次性求值的
 * 普通数组/常量，切换语言后页面残留中文（列头、tab、卡片说明不变）。
 * 修复：这些常量改为 computed（依赖 t），ECharts 在 locale 变化时重建。
 *
 * 变异点：
 *  - 把 sessionColumns/auditColumns/themeOptions 退回非 computed（或写死中文）→ 红；
 *  - tab/系统活跃度/快捷入口文案退回硬编码 → 红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import naive, { NMessageProvider, NDialogProvider } from 'naive-ui'

import ProfilePage from '../index.vue'
import zh from '@/locales/zh'
import en from '@/locales/en'

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
      data: { conversation_count: 1, message_count: 2, file_count: 3, knowledge_count: 4, storage_used: 0 },
    }),
    getUserActivities: vi.fn().mockResolvedValue({ data: [] }),
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
    getUserSettingsProviders: vi.fn().mockResolvedValue({
      data: {
        success: true,
        providers: [
          { id: 'deepseek', name: 'DeepSeek', base_url: 'https://api.deepseek.com/v1', models: ['deepseek-chat'], requires_key: true },
        ],
        current: { provider: 'deepseek', model: 'deepseek-chat', api_key: 'sk-a...zzzz', base_url: '' },
      },
    }),
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

const i18n = createI18n({
  legacy: false,
  locale: 'zh',
  fallbackLocale: 'en',
  messages: { zh, en },
})

function mountPage() {  const pinia = createPinia()
  setActivePinia(pinia)
  const Host = defineComponent({
    render: () =>
      h(NDialogProvider, null, { default: () => h(NMessageProvider, null, { default: () => h(ProfilePage) }) }),
  })
  return mount(Host, { global: { plugins: [pinia, i18n, naive] } })
}

async function switchLanguage(code: 'zh' | 'en') {
  i18n.global.locale.value = code
  await nextTick()
  await flushPromises()
}

async function openTab(wrapper: ReturnType<typeof mountPage>, label: string) {
  const tab = wrapper.findAll('.n-tabs-tab').find(t => t.text().includes(label))
  expect(tab, `「${label}」tab 应在屏`).toBeTruthy()
  await tab!.trigger('click')
  await flushPromises()
  await flushPromises()
}

beforeEach(() => {
  localStorage.clear()
  // 全局 composer 的 locale 在文件内共享：每个用例从 zh 起步
  i18n.global.locale.value = 'zh'
})

describe('Profile · 语言切换后文案实时更新', () => {
  it('tab、快捷入口、系统活跃度卡片随 locale 切换', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.text()).toContain('设备管理')
    expect(wrapper.text()).toContain('操作审计')
    expect(wrapper.text()).toContain('系统活跃度')
    expect(wrapper.text()).toContain('进入智能对话')
    expect(wrapper.text()).toContain('继续当前任务并复用历史上下文')

    await switchLanguage('en')

    const text = wrapper.text()
    expect(text, 'tab 标签未跟随语言').toContain('Devices')
    expect(text).toContain('Audit log')
    expect(text, '系统活跃度卡片未跟随语言').toContain('System activity')
    expect(text, '快捷入口标题未跟随语言').toContain('Open chat')
    expect(text).toContain('Continue your current task with full context')
    expect(text, '切换后仍残留中文快捷入口').not.toContain('进入智能对话')
  })

  it('设备表格列头与枚举文案随 locale 切换（computed 列）', async () => {
    const wrapper = mountPage()
    await flushPromises()
    await openTab(wrapper, '设备管理')
    expect(wrapper.text()).toContain('在线')
    expect(wrapper.text()).toContain('IP地址')

    await switchLanguage('en')

    // 已打开的表格不重挂载：列定义必须自身响应 locale
    const text = wrapper.text()
    expect(text).toContain('Device')
    expect(text).toContain('IP address')
    expect(text).toContain('Last active')
    expect(text).toContain('Online')
    expect(text, '切换后列头残留中文').not.toContain('设备信息')
  })

  it('主题下拉选项随 locale 切换（computed 选项）', async () => {
    localStorage.setItem('theme', 'auto')
    const wrapper = mountPage()
    await flushPromises()
    await openTab(wrapper, '偏好设置')
    expect(wrapper.text()).toContain('跟随系统')

    await switchLanguage('en')

    // n-select 的当前值文案取自 options；无需展开下拉即可观察到已选中的「Follow system」
    expect(wrapper.text()).toContain('Follow system')
    expect(wrapper.text()).not.toContain('跟随系统')
  })
})
