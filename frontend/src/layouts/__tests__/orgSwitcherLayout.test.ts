/**
 * B1：顶栏组织切换器（先红 —— 当前布局只有侧栏一枚不可点击的 "组织 {id}" 标签，
 * 没有任何切换器，store 也没有服务端切换动作）。
 *
 * 契约：
 *  - 布局顶栏渲染组织切换器，显示当前组织名称（来自 GET /users/me/organizations）；
 *  - 下拉选择其它组织 → 调用切换接口，当前组织名称随之更新；
 *  - 组织上下文变化后当前页面重挂载（probe setup 计数 +1），
 *    文档/会话等 onMounted 拉取因此按新组织重新发起。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createI18n } from 'vue-i18n'
import { defineComponent, h } from 'vue'
import naive, { NDropdown } from 'naive-ui'
import zh from '@/locales/zh'

vi.mock('@/utils/message', () => ({
  useDedupedMessage: () => ({ error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn(), loading: vi.fn() }),
}))

vi.mock('@/utils/notificationSocket', () => ({
  notificationSocket: {
    connect: vi.fn(),
    disconnect: vi.fn(),
    onNotification: vi.fn(),
    offNotification: vi.fn(),
  },
}))

vi.mock('@/api/notification', () => ({
  getNotifications: vi.fn().mockResolvedValue({ data: { items: [] } }),
  getUnreadCount: vi.fn().mockResolvedValue({ data: { count: 0 } }),
  getNotificationSummary: vi.fn().mockResolvedValue({ data: { total: 0, unread_count: 0, by_type: {} } }),
  markAsRead: vi.fn().mockResolvedValue(undefined),
  markAllAsRead: vi.fn().mockResolvedValue(undefined),
  deleteNotification: vi.fn().mockResolvedValue(undefined),
}))

vi.mock('@/api/user', () => ({
  getUserSettings: vi.fn().mockResolvedValue({ data: { data: {} } }),
  updateUserSettings: vi.fn().mockResolvedValue({ data: {} }),
  updateUserProfile: vi.fn().mockResolvedValue({ data: {} }),
  getMyOrganizations: vi.fn().mockResolvedValue({
    data: {
      success: true,
      data: {
        organizations: [
          { id: 1, name: 'Alpha' },
          { id: 2, name: 'Beta' },
        ],
        current_organization_id: 1,
      },
    },
  }),
  switchOrganization: vi.fn().mockResolvedValue({
    data: { success: true, data: { organization_id: 2 } },
  }),
}))

import Layouts from '../index.vue'
import { useUserStore } from '@/stores/user'
import { switchOrganization as switchOrganizationApi, getMyOrganizations } from '@/api/user'

const mockedSwitch = vi.mocked(switchOrganizationApi)
const mockedList = vi.mocked(getMyOrganizations)

const i18n = createI18n({
  legacy: false,
  locale: 'zh',
  missingWarn: false,
  fallbackWarn: false,
  messages: { zh },
})

let probeSetupCount = 0
const Probe = defineComponent({
  name: 'OrgSwitchProbe',
  setup() {
    probeSetupCount++
    return () => h('div', { class: 'org-switch-probe' }, `setup:${probeSetupCount}`)
  },
})

let currentWrapper: { unmount: () => void } | null = null

async function mountLayouts() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: '/dashboard',
        name: 'Dashboard',
        component: Probe,
        meta: { title: 'menu.home' },
      },
    ],
  })
  await router.push('/dashboard')
  await router.isReady()
  const pinia = createPinia()
  setActivePinia(pinia)
  const userStore = useUserStore()
  userStore.token = 'test-token'
  userStore.userInfo = {
    id: 7,
    username: 'org_probe',
    email: 'org_probe@example.com',
    isAdmin: false,
    createdAt: '',
    organizations: [],
    organization_id: 1,
  }
  userStore.currentOrganizationId = 1
  const wrapper = mount(Layouts, {
    attachTo: document.body,
    global: { plugins: [naive, pinia, router, i18n] },
  })
  currentWrapper = wrapper
  await flushPromises()
  return { wrapper, router, userStore }
}

beforeEach(() => {
  localStorage.clear()
  probeSetupCount = 0
  vi.clearAllMocks()
  mockedList.mockResolvedValue({
    data: {
      success: true,
      data: {
        organizations: [
          { id: 1, name: 'Alpha' },
          { id: 2, name: 'Beta' },
        ],
        current_organization_id: 1,
      },
    },
  } as never)
  mockedSwitch.mockResolvedValue({
    data: { success: true, data: { organization_id: 2 } },
  } as never)
})

afterEach(async () => {
  currentWrapper?.unmount()
  currentWrapper = null
  document.body.innerHTML = ''
  await flushPromises()
})

describe('B1 layout organization switcher', () => {
  it('shows the current organization in the header', async () => {
    const { wrapper } = await mountLayouts()
    const trigger = wrapper.find('[data-testid="org-switcher"]')
    expect(trigger.exists(), 'header must expose an organization switcher').toBe(true)
    expect(trigger.text()).toContain('Alpha')
  })

  it('selecting another organization calls the switch API', async () => {
    const { wrapper } = await mountLayouts()
    const switcher = wrapper.find('[data-testid="org-switcher"]')
    const dropdown = switcher.findComponent(NDropdown)
    expect(dropdown.exists(), 'organization switcher must render a dropdown').toBe(true)

    dropdown.vm.$emit('select', 2)
    await flushPromises()

    expect(mockedSwitch).toHaveBeenCalledWith(2)
  })

  it('re-mounts the current page after the organization changes (context refresh)', async () => {
    const { wrapper, userStore } = await mountLayouts()
    expect(wrapper.find('.org-switch-probe').exists()).toBe(true)
    expect(probeSetupCount).toBe(1)

    await userStore.switchOrganization(2)
    await flushPromises()

    expect(wrapper.find('[data-testid="org-switcher"]').text()).toContain('Beta')
    await vi.waitFor(() => expect(probeSetupCount).toBe(2))
  })
})
