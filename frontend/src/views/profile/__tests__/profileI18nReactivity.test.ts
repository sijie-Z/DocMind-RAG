/**
 * Profile 页 i18n 真·响应式判别用例。
 *
 * 修前：设备/审计表格列、主题选项、快捷入口文案是 setup 期一次性求值的
 * 普通数组/常量，切换语言后页面残留中文（列头、tab、卡片说明不变）。
 * 修复：这些常量改为 computed（依赖 t），ECharts 在 locale 变化时重建。
 *
 * 变异点：
 *  - 把 sessionColumns/auditColumns/themeOptions 退回非 computed（或写死中文）→ 红；
 *  - tab/系统活跃度/快捷入口文案退回硬编码 → 红；
 *  - 删除 watch(locale) 的图表重建（或只 mock 哑 ECharts 实例）→ 红。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import naive, { NMessageProvider, NDialogProvider } from 'naive-ui'

import ProfilePage from '../index.vue'
import { useProfilePage } from '../composables/useProfilePage'
import { useUserStore } from '@/stores/user'
import zh from '@/locales/zh'
import en from '@/locales/en'
import fr from '@/locales/fr'

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

// ECharts 实例工厂：记录每次 setOption 的配置，供“语言切换后重建”断言。
// 注意不能只 mock 成哑对象——那样删掉 watch(locale) 重建逻辑测试仍会绿（评审 B）。
const { echartsInit, setOptionCalls } = vi.hoisted(() => {
  const calls: Array<Record<string, any>> = []
  const init = vi.fn(() => ({
    setOption: vi.fn((option: Record<string, any>) => { calls.push(option) }),
    resize: vi.fn(),
    dispose: vi.fn(),
  }))
  return { echartsInit: init, setOptionCalls: calls }
})

vi.mock('@/utils/echarts', () => ({
  default: {
    init: echartsInit,
    graphic: { LinearGradient: class {} },
  },
}))

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

const i18n = createI18n({
  legacy: false,
  locale: 'zh',
  fallbackLocale: 'en',
  messages: { zh, en, fr },
})

const mountedWrappers: Array<{ unmount: () => void }> = []

function track<T extends { unmount: () => void }>(wrapper: T): T {
  mountedWrappers.push(wrapper)
  return wrapper
}

function mountPage() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const Host = defineComponent({
    render: () =>
      h(NDialogProvider, null, { default: () => h(NMessageProvider, null, { default: () => h(ProfilePage) }) }),
  })
  return track(mount(Host, { global: { plugins: [pinia, i18n, naive] } }))
}

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
  return track(mount(Host, { global: { plugins: [pinia, i18n, naive] } }))
}

async function switchLanguage(code: 'zh' | 'en' | 'fr') {
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

afterEach(() => {
  // 卸掉页面/watcher/resize 监听，并清空图表调用记录，避免跨用例互相兜底
  while (mountedWrappers.length > 0) mountedWrappers.pop()!.unmount()
  setOptionCalls.length = 0
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

    // 已打开的表格不重挂载：列定义必须自身响应 locale。
    // 用表头单元格精确断言，避免 'Devices' 包含子串 'Device' 的空转（评审 B）。
    const headers = wrapper.findAll('th').map((th) => th.text())
    expect(headers).toContain('Device')
    expect(headers).toContain('IP address')
    expect(headers).toContain('Last active')
    expect(headers).toContain('Actions')
    expect(wrapper.text()).toContain('Online')
    expect(headers, '切换后列头残留中文').not.toContain('设备信息')
  })

  it('审计表格列头随 locale 切换（computed 列）', async () => {
    const wrapper = mountPage()
    await flushPromises()
    await openTab(wrapper, '操作审计')
    expect(wrapper.findAll('th').map((th) => th.text())).toContain('详情')

    await switchLanguage('en')

    const headers = wrapper.findAll('th').map((th) => th.text())
    expect(headers).toContain('Actions')
    expect(headers).toContain('Details')
    expect(headers).toContain('IP')
    expect(headers).toContain('Time')
    expect(headers, '切换后审计列头残留中文').not.toContain('详情')
  })

  it('表单校验文案随 locale 更新（rules 为 computed，不是 setup 常量）', async () => {
    mountHarness()
    await flushPromises()
    expect(capturedApi.editFormRules.value.nickname[0].message).toBe('请输入昵称')

    await switchLanguage('en')

    expect(capturedApi.editFormRules.value.nickname[0].message).toBe('Please enter nickname')
    expect(capturedApi.passwordFormRules.value.newPassword[1].message).toBe(
      'Password must be between 8-20 characters',
    )
  })

  it('tab 往返后图表重建（非激活 pane 卸载 DOM）', async () => {
    const wrapper = mountPage()
    await flushPromises()
    await wait(300)
    const before = setOptionCalls.length

    await openTab(wrapper, '偏好设置')
    await openTab(wrapper, '概览')
    await wait(100)

    expect(setOptionCalls.length, '回到概览后应重新 setOption').toBeGreaterThan(before)
  })

  it('图表内嵌文案随 locale 重建（ECharts 不响应 i18n）', async () => {
    mountPage()
    await flushPromises()
    // 挂载后的 initCharts 走 100ms 定时器
    await wait(300)

    const before = setOptionCalls.length
    expect(before, '挂载后应至少绘制存储/活跃度两张图').toBeGreaterThanOrEqual(2)
    expect(setOptionCalls.some((o) => o?.series?.[0]?.name === '存储分布')).toBe(true)
    expect(setOptionCalls.some((o) => o?.series?.[0]?.name === '活跃度')).toBe(true)

    await switchLanguage('en')
    await wait(300)

    const rebuilt = setOptionCalls.slice(before)
    expect(rebuilt.some((o) => o?.series?.[0]?.name === 'Storage distribution')).toBe(true)
    expect(rebuilt.some((o) => o?.series?.[0]?.name === 'Activity')).toBe(true)
    expect(rebuilt.some((o) => o?.series?.[0]?.name === '存储分布'), '重建后仍是中文系列名').toBe(false)

    // 日期轴必须跟随 DATE_LOCALE_MAP（固定 en-US/zh-CN 会让下面的 fr 断言红）
    const afterEn = setOptionCalls.length
    await switchLanguage('fr')
    await wait(300)
    const frActivity = setOptionCalls
      .slice(afterEn)
      .find((o) => o?.series?.[0]?.name === 'Activité')
    expect(frActivity, 'fr 下活跃度图表应重建').toBeTruthy()
    expect(frActivity?.xAxis?.data?.[0], 'fr 日期轴格式不对').toMatch(/\d{1,2}\s+\S+\./)
    expect(frActivity?.xAxis?.data?.[0]).not.toMatch(/^[A-Z][a-z]{2} \d/)
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

describe('Profile · 管理员入口按权限隐藏', () => {
  it('普通用户无「系统监控」快捷入口，管理员有', async () => {
    mountHarness()
    await flushPromises()
    const routes = () => capturedApi.quickActions.value.map((a) => a.route)
    expect(routes()).not.toContain('Monitoring')

    const store = useUserStore()
    store.token = 'tok'
    store.userInfo = { id: 1, username: 'admin', is_superuser: true } as never
    await nextTick()
    expect(routes()).toContain('Monitoring')
  })
})
