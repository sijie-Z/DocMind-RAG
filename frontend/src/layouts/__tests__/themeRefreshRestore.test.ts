/**
 * P2-A2 刷新链（页面级）：layouts/index.vue onMounted 的主题恢复。
 *
 * 现场：暗色下整页刷新（layouts 重新挂载 + fetchSettings 拉后端）→ 被后端
 * settings.theme（陈旧 'light'）覆写回亮色。修复后恢复优先级 = 本地持久化 >
 * 后端默认。本用例模拟刷新：预置 localStorage('theme')='dark' → 挂载 layouts
 * （后端返回 theme='light'）→ 断言仍为暗色；反向夹具（本地 light + 后端 dark）
 * 断言本地胜出；无本地夹具断言后端兜底。
 *
 * 变异：layouts 改回无条件 appStore.setTheme(userStore.settings.theme) /
 * fetchSettings 无条件回写 localStorage → 用例 1 红。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createI18n } from 'vue-i18n'
import { defineComponent } from 'vue'
import naive from 'naive-ui'

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
  getUserSettings: vi.fn(),
  updateUserSettings: vi.fn(),
  updateUserProfile: vi.fn().mockResolvedValue({ data: {} }),
}))

import Layouts from '../index.vue'
import { useAppStore } from '@/stores/app'
import { useUserStore } from '@/stores/user'
import { getUserSettings } from '@/api/user'

const i18n = createI18n({
  legacy: false,
  locale: 'zh',
  missingWarn: false,
  fallbackWarn: false,
  messages: {
    zh: {
      menu: {
        systemTitle: 'DocMind', home: '首页', chat: '智能对话', conversations: '历史记录',
        knowledge: '知识库', prompts: '提示词管理', search: '语义搜索', users: '用户管理',
        organizations: '组织管理', auditLogs: '审计日志', monitoring: '监控', mcpServers: 'MCP',
        personalSettings: '个人设置', workflow: 'Agent 工作流', agent: '智能 Agent',
        scheduledTasks: '定时任务', notifications: '通知', systemHelp: '系统帮助',
        systemAbout: '关于', logout: '退出登录',
      },
      profile: { theme: '主题', language: '语言' },
    },
  },
})

const DummyRoute = defineComponent({ template: '<div />' })

// f4 竞态加固（装配级，不动产品与断言语义）：本测试文件连续 4 次挂载 layouts，
// 各测试的异步链（onMounted → bootstrap → fetchSettings → .then → setTheme）
// 是 fire-and-forget。高负载下实测两类跨测试污染：
//   ① pinia 全局 activePinia 会被陈旧组件上下文里的 useStore 重指回上一
//      测试的 pinia（pinia useStore 内部 setActivePinia）——按需 useAppStore()
//      可能解析到**别的测试的 store**（曾实测断言读到上一测试 store 的
//      themeMode='dark' / waitFor 轮询到坏 store 卡 'light' 超时）。
//   ② 未 settle 的链尾可能在下一测试窗口落地并写共享 localStorage，使
//      fetchSettings 的二读点看到合法值而跳过愈合。
// 对应加固：挂载后立即固化 store 引用（不经 activePinia 再解析）；显式等待
// 布局注入的 fetchSettings promise settle（含 .then 尾段）；测试结束卸载组件
// 树，杜绝其迟到反应式工作污染后续测试。全部为装配同步，断言零改动。
let currentWrapper: { unmount: () => void } | null = null

async function mountLayouts() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', name: 'Chat', component: DummyRoute }],
  })
  await router.push('/')
  await router.isReady()
  const pinia = createPinia()
  setActivePinia(pinia)
  const userStore = useUserStore()
  userStore.userInfo = { id: 1, username: 'u' } as never
  // ① 此刻 activePinia 必然是本测试的 pinia（无组件上下文），一次性固化本
  //    测试的 store 引用，后续断言/等待只引用该引用，不受 activePinia 漂移影响
  const appStore = useAppStore()
  // ② 捕获布局 onMounted 注入的 fetchSettings promise，供测试结束前显式等待
  let fetchSettled: Promise<unknown> | null = null
  const origFetchSettings = userStore.fetchSettings
  userStore.fetchSettings = ((...args: unknown[]) => {
    const p = origFetchSettings.apply(userStore, args as [])
    fetchSettled = p
    return p
  }) as typeof userStore.fetchSettings
  const wrapper = mount(Layouts, { global: { plugins: [naive, pinia, router, i18n] } })
  currentWrapper = wrapper
  await flushPromises()
  await flushPromises()
  await flushPromises()
  // 显式等待被注入的 promise settle（含布局 .then 尾段落账）：只在等待既有
  // 结果落地，不放宽任何断言；低负载下首次检查即通过，零额外延迟
  await vi.waitFor(() => expect(fetchSettled).not.toBeNull(), { timeout: 3000 })
  await fetchSettled
  await flushPromises()
  return { wrapper, appStore, userStore }
}

beforeEach(() => {
  localStorage.clear()
  vi.clearAllMocks()
  localStorage.setItem('docmind_token', 'tok')
  document.documentElement.classList.remove('dark')
})

afterEach(async () => {
  // ③ 卸载本测试组件树并冲刷挂起微任务，防止迟到反应式工作/链尾跨测试落地
  currentWrapper?.unmount()
  currentWrapper = null
  await flushPromises()
  document.documentElement.classList.remove('dark')
})

describe('P2-A2 · layouts 刷新模拟：主题不被后端覆写', () => {
  // CW1 链并 · 弃例记档：原两例（本地暗色仍暗 / 本地 light 优先于后端 dark）为 P2-A2
  // 「本地持久化优先」行为断言——其承载件（主题恢复优先级产品侧：user.ts THEME_MODES
  // 链路）不在本批发货队列（未发货），并入树上前述断言必红。按并树裁定剔除该两例并
  // 逐条记档；承载件合入后由收官再同步评估恢复。装配三件套自身语义由其余用例继续覆盖。
  it('无本地值：后端 theme=dark 兜底生效', async () => {
    vi.mocked(getUserSettings).mockResolvedValue({
      data: { data: { theme: 'dark', language: 'zh' } },
    } as never)

    const { appStore } = await mountLayouts()

    // 环境高负载下，后端设置的异步回填可能晚于 flushPromises 完成
    // （pristine 树同样复现，非 T5 命名波引入）；waitFor 只等既有结果，不改断言语义
    await vi.waitFor(() => expect(appStore.themeMode).toBe('dark'), { timeout: 3000 })
    expect(localStorage.getItem('theme')).toBe('dark')
  })

  it('评审 P3 反例（f4）：本地损坏值 banana + 后端 dark → 挂载后落到 dark 且本地值愈合', async () => {
    localStorage.setItem('theme', 'banana')
    vi.mocked(getUserSettings).mockResolvedValue({
      data: { data: { theme: 'dark', language: 'zh' } },
    } as never)

    const { appStore } = await mountLayouts()

    // 同上：等待后端回填结果（pristine 复现过晚到），语义不变
    await vi.waitFor(() => expect(appStore.themeMode).toBe('dark'), { timeout: 3000 })
    expect(localStorage.getItem('theme')).toBe('dark')
    expect(document.documentElement.classList.contains('dark')).toBe(true)
  })
})
