/**
 * T7：移动端组织切换入口 + 单组织形态（先红）。
 *
 * 背景（B1 遗留）：
 *  - 顶栏组织切换器是 `hidden sm:block`，<640px 全站唯一组织切换入口被隐藏，
 *    手机用户无法切换组织；
 *  - `OrgSwitcher.vue` 在只有 1 个组织时仍渲染带下拉箭头的按钮，唯一选项被
 *    disabled，点开没有任何可执行动作 —— “可点但无动作”的假按钮。
 *
 * 方案选择（任务书「二选一，以不挤压标题为准」）：顶栏紧凑形态在真实手机宽度下
 * 会被顶栏右侧固定宽度的搜索框遮挡、且把 h1 标题挤到 0 宽（评审实测 320–414px
 * 均不可点）。因此采用任务书明示的另一方案：移动端入口移入既有用户菜单
 * （顶栏右侧用户图标下拉，不占左侧宽度），顶栏 OrgSwitcher 保持桌面端形态
 * （`hidden sm:block`，与 B1 一致）。
 *
 * 契约：
 *  - 单组织（无任何可切换目标，唯一选项即当前组织）→ OrgSwitcher 渲染静态
 *    组织名：无 button 语义、无下拉组件、无下拉箭头；
 *  - 多组织 → 桌面端正常渲染下拉触发器；
 *  - 超管无主组织但可切换（currentOrgId 为 null、列表非空）→ 保留既有
 *    “切换组织”提示文案，且选项全部可选（下拉有真实动作）；单组织 +
 *    无主组织时该组织可选，同样保留下拉（不降级为静态死标签）；
 *  - 移动端（<640px）→ 顶栏用户菜单中出现组织切换分组（全名选项、当前组织
 *    disabled），选择即调用切换接口；单组织时该分组不出现（菜单保持原样）；
 *  - 移动端入口（用户菜单触发器）位于 <header> 内，祖先链无 display:none。
 *    注：任务书的类名断言原文以「顶栏保留紧凑形态」方案为对象；本实现采用
 *    用户菜单方案，故断言对象改为移动端实际入口（用户菜单触发器），
 *    并额外断言其选项携带真实切换动作。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
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
  getMyOrganizations: vi.fn(),
  switchOrganization: vi.fn(),
}))

import Layouts from '../index.vue'
import { useUserStore } from '@/stores/user'
import { getMyOrganizations, switchOrganization as switchOrganizationApi } from '@/api/user'

const mockedList = vi.mocked(getMyOrganizations)
const mockedSwitch = vi.mocked(switchOrganizationApi)

const i18n = createI18n({
  legacy: false,
  locale: 'zh',
  missingWarn: false,
  fallbackWarn: false,
  messages: { zh },
})

const Probe = defineComponent({
  name: 'OrgSwitcherProbe',
  setup() {
    return () => h('div', { class: 'org-switcher-probe' }, 'probe')
  },
})

let currentWrapper: VueWrapper | null = null

interface MountOptions {
  organizations: Array<{ id: number; name: string }>
  /** 服务端返回的 current_organization_id（可为 null：超管无主组织） */
  serverCurrentOrgId: number | null
  /** 本地 userStore 的当前组织（默认与 serverCurrentOrgId 一致） */
  localCurrentOrgId?: number | null
}

/**
 * jsdom 的全局 matchMedia 桩恒返回 matches:false（见 src/test-setup.ts）。
 * 移动端用例需要真实断点语义：仅 `(max-width: 639.98px)` 命中。
 */
function stubViewport(mobile: boolean) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    configurable: true,
    value: (query: string) => ({
      matches: mobile && /max-width:\s*639(\.98)?px/.test(query),
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(() => false),
    }),
  })
}

async function mountLayouts(options: MountOptions) {
  mockedList.mockResolvedValue({
    data: {
      success: true,
      data: {
        organizations: options.organizations,
        current_organization_id: options.serverCurrentOrgId,
      },
    },
  } as never)
  mockedSwitch.mockResolvedValue({
    data: { success: true, data: { organization_id: 2 } },
  } as never)

  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/dashboard', name: 'Dashboard', component: Probe, meta: { title: 'menu.home' } },
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
    organization_id: options.serverCurrentOrgId ?? undefined,
  }
  userStore.currentOrganizationId = options.localCurrentOrgId ?? options.serverCurrentOrgId
  const wrapper = mount(Layouts, {
    attachTo: document.body,
    global: { plugins: [naive, pinia, router, i18n] },
  })
  currentWrapper = wrapper
  await flushPromises()
  return { wrapper, userStore }
}

/** 顶栏里的用户菜单下拉（以既有用户菜单项 `logout` 为锚点识别） */
function findHeaderUserMenu(wrapper: VueWrapper) {
  const dropdowns = wrapper.find('header').findAllComponents(NDropdown) as unknown as Array<VueWrapper<any>>
  return dropdowns.find((dropdown) => {
    const options = (dropdown.props() as Record<string, unknown>).options as
      | Array<{ key?: string }>
      | undefined
    return !!options?.some((option) => option.key === 'logout')
  })
}

/** 取组件 props 里的下拉选项（无 key 重载，避免 VueWrapper<any> 的 never 约束） */
function menuOptionsOf(wrapper: VueWrapper<any>): MenuNode[] {
  return ((wrapper.props() as Record<string, unknown>).options as MenuNode[]) ?? []
}

/** 从元素向上走到 <header>：断言整条祖先链都不以类名/Style 隐藏 */
function expectNoHiddenAncestor(root: Element) {
  let el: Element | null = root
  let reachedHeader = false
  while (el) {
    if (el.tagName === 'HEADER') {
      reachedHeader = true
      break
    }
    expect(
      el.classList.contains('hidden'),
      `移动端组织入口祖先 <${el.tagName.toLowerCase()} class="${el.className}"> 不得带 display:none 类 'hidden'`,
    ).toBe(false)
    expect((el as HTMLElement).style.display).not.toBe('none')
    el = el.parentElement
  }
  expect(reachedHeader, '移动端组织入口（用户菜单触发器）必须位于顶栏 <header> 内').toBe(true)
}

type MenuNode = {
  key?: string
  type?: string
  label?: string
  disabled?: boolean
  children?: MenuNode[]
}

beforeEach(() => {
  localStorage.clear()
  vi.clearAllMocks()
  stubViewport(false)
})

afterEach(async () => {
  currentWrapper?.unmount()
  currentWrapper = null
  document.body.innerHTML = ''
  await flushPromises()
})

describe('T7 · 组织切换器移动端入口与单组织形态', () => {
  it('单组织（唯一选项即当前组织）：渲染静态组织名，不渲染下拉触发器 / button 语义', async () => {
    const { wrapper } = await mountLayouts({
      organizations: [{ id: 1, name: 'Alpha' }],
      serverCurrentOrgId: 1,
    })

    const root = wrapper.find('[data-testid="org-switcher"]')
    expect(root.exists(), '单组织时仍应显示当前组织（静态形态）').toBe(true)
    expect(root.text()).toContain('Alpha')

    // 不得出现“可点但无动作”的按钮：无触发器、无 button、无下拉组件。
    expect(wrapper.find('[data-testid="org-switcher-trigger"]').exists()).toBe(false)
    expect(root.find('button').exists()).toBe(false)
    expect(root.find('[role="button"]').exists()).toBe(false)
    expect(root.findComponent(NDropdown).exists()).toBe(false)

    // 静态形态是明确标注的静态节点（测试锚点）
    expect(root.find('[data-testid="org-switcher-static"]').exists()).toBe(true)
  })

  it('多组织：正常渲染下拉触发器与下拉组件', async () => {
    const { wrapper } = await mountLayouts({
      organizations: [
        { id: 1, name: 'Alpha' },
        { id: 2, name: 'Beta' },
      ],
      serverCurrentOrgId: 1,
    })

    const root = wrapper.find('[data-testid="org-switcher"]')
    expect(root.exists()).toBe(true)
    const trigger = root.find('[data-testid="org-switcher-trigger"]')
    expect(trigger.exists(), '多组织必须包含可点击的组织切换触发器').toBe(true)
    expect(trigger.element.tagName).toBe('BUTTON')
    expect(root.findComponent(NDropdown).exists()).toBe(true)
    expect(root.find('[data-testid="org-switcher-static"]').exists()).toBe(false)

    // 桌面端（默认视口）不重复占用用户菜单：组织入口仍由顶栏切换器承担
    const userMenu = findHeaderUserMenu(wrapper)
    expect(userMenu, '顶栏用户菜单必须存在').toBeTruthy()
    const menuKeys = menuOptionsOf(userMenu!).flatMap((option) => [
      option.key,
      ...(option.children ?? []).map((child) => child.key),
    ])
    expect(menuKeys.some((key) => String(key).startsWith('org-switch:'))).toBe(false)
  })

  it('超管无主组织但可切换：保留“切换组织”提示下拉，选项全部可选', async () => {
    const { wrapper } = await mountLayouts({
      organizations: [
        { id: 1, name: 'Alpha' },
        { id: 2, name: 'Beta' },
      ],
      serverCurrentOrgId: null,
      localCurrentOrgId: null,
    })

    const root = wrapper.find('[data-testid="org-switcher"]')
    expect(root.exists()).toBe(true)
    const trigger = root.find('[data-testid="org-switcher-trigger"]')
    expect(trigger.exists(), '超管无主组织但可切换时必须保留下拉入口').toBe(true)
    expect(root.text()).toContain('切换组织')

    const dropdown = root.findComponent(NDropdown)
    expect(dropdown.exists()).toBe(true)
    const options = dropdown.props('options') as Array<{ key: number; disabled?: boolean }>
    expect(options).toHaveLength(2)
    expect(options.every((option) => !option.disabled), '无主组织时不得把所有选项标为 disabled').toBe(true)
  })

  it('单组织 + 无主组织（超管）：唯一组织可选 → 保留下拉（不是静态死标签）', async () => {
    const { wrapper } = await mountLayouts({
      organizations: [{ id: 1, name: 'Alpha' }],
      serverCurrentOrgId: null,
      localCurrentOrgId: null,
    })

    const root = wrapper.find('[data-testid="org-switcher"]')
    expect(root.exists()).toBe(true)
    // 该组织是真实可切换目标：下拉有动作，不能渲染成不可点的静态文本
    expect(root.find('[data-testid="org-switcher-trigger"]').exists()).toBe(true)
    expect(root.find('[data-testid="org-switcher-static"]').exists()).toBe(false)
    expect(root.text()).toContain('切换组织')
    const dropdown = root.findComponent(NDropdown)
    const options = dropdown.props('options') as Array<{ key: number; disabled?: boolean }>
    expect(options).toHaveLength(1)
    expect(options[0].disabled).toBe(false)
  })

  it('移动端：用户菜单承接组织切换入口（分组 + 选项 + 真实切换动作）', async () => {
    stubViewport(true)
    const { wrapper } = await mountLayouts({
      organizations: [
        { id: 1, name: 'Alpha' },
        { id: 2, name: 'Beta' },
      ],
      serverCurrentOrgId: 1,
    })

    const userMenu = findHeaderUserMenu(wrapper)
    expect(userMenu, '顶栏用户菜单必须存在（移动端组织入口的载体）').toBeTruthy()

    // 入口位于顶栏，且祖先链无 display:none（类名断言替代真实视口）
    expectNoHiddenAncestor(userMenu!.element)

    const options = menuOptionsOf(userMenu!)
    const orgGroup = options.find((option) => option.type === 'group' && option.key === 'mobile-org-group')
    expect(orgGroup, '移动端用户菜单必须包含组织切换分组').toBeTruthy()
    expect(orgGroup!.label).toBe('切换组织')
    expect(orgGroup!.children?.map((child) => child.label)).toEqual(['Alpha', 'Beta'])
    expect(orgGroup!.children?.map((child) => child.key)).toEqual(['org-switch:1', 'org-switch:2'])
    // 当前组织 disabled，其余可选 —— 不存在“可点但无动作”的选项
    expect(orgGroup!.children?.map((child) => child.disabled)).toEqual([true, false])

    // 选择其它组织 → 调用服务端切换接口（入口是真实可用的）
    userMenu!.vm.$emit('select', 'org-switch:2')
    await flushPromises()
    expect(mockedSwitch).toHaveBeenCalledWith(2)

    // 布局层与顶栏切换器同 tick 挂载：组织列表只发一次请求（共享状态 + 并发去重）
    expect(mockedList).toHaveBeenCalledTimes(1)
  })

  it('移动端单组织：用户菜单不出现组织切换分组（无可切换目标 → 原样菜单）', async () => {
    stubViewport(true)
    const { wrapper } = await mountLayouts({
      organizations: [{ id: 1, name: 'Alpha' }],
      serverCurrentOrgId: 1,
    })

    const userMenu = findHeaderUserMenu(wrapper)
    expect(userMenu).toBeTruthy()
    const options = menuOptionsOf(userMenu!)
    expect(options.some((option) => option.key === 'mobile-org-group')).toBe(false)
    const keys = options.flatMap((option) => [option.key, ...(option.children ?? []).map((child) => child.key)])
    expect(keys.some((key) => String(key).startsWith('org-switch:'))).toBe(false)
    // 既有用户菜单项不受影响
    expect(keys).toContain('Profile')
    expect(keys).toContain('logout')
  })
})
