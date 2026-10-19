/**
 * T1（第十批）· 首页「最近会话」跳转参数名修正（先红）。
 *
 * 判别点：
 * 1. 点击最近会话行必须跳到 `/chat?conversation_id=<id>`——聊天页只消费
 *    `route.query.conversation_id`（views/chat/index.vue 的 watch + onMounted）。
 *    修前 push 的是 `/chat?conversation=<id>`：聊天页读不到会话 id，点进去
 *    是空聊天页（「点了没反应」）。
 * 2. 回归护栏：src 生产代码不得再出现 `?conversation=`（不带 `_id`）形态。
 *    测试夹具里的字符串样例（utils/__tests__/postAuthRedirect.test.ts 的
 *    `/chat?conversation=42`）不属于生产代码，不计入。
 *
 * 变异点：把跳转改回 `?conversation=` → 两条断言同时红。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { resolve } from 'node:path'
import { mount, flushPromises, enableAutoUnmount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'

import UserDashboard from '../user.vue'
import { getUserStats } from '@/api/user'
import { getConversations } from '@/api/conversation'
import { getRAGStats, getLLMStatus } from '@/api/monitoring'
import { seedDemoData } from '@/api/demo'
import zh from '@/locales/zh'

vi.mock('@/utils/echarts', () => ({
  default: {
    init: vi.fn(() => ({ setOption: vi.fn(), dispose: vi.fn(), resize: vi.fn() })),
    graphic: { LinearGradient: class LinearGradient {} },
  },
}))
vi.mock('@/api/monitoring', () => ({
  getRAGStats: vi.fn(),
  getLLMStatus: vi.fn(),
}))
vi.mock('@/api/user', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/user')>()
  return { ...actual, getUserStats: vi.fn() }
})
vi.mock('@/api/conversation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/conversation')>()
  return { ...actual, getConversations: vi.fn() }
})
vi.mock('@/api/demo', () => ({ seedDemoData: vi.fn() }))
vi.mock('naive-ui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('naive-ui')>()
  return {
    ...actual,
    useMessage: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }),
  }
})

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

const statsFixture = () => ({
  data: {
    conversation_count: 1,
    message_count: 2,
    file_count: 0,
    knowledge_count: 0,
    storage_used: 0,
    activity_trend: [0, 0, 0, 0, 0, 0, 0],
  },
})

const conversationsFixture = () => ({
  data: {
    success: true,
    data: {
      data: [
        {
          id: 'c1',
          title: '会话一',
          created_at: '',
          updated_at: new Date().toISOString(),
          message_count: 2,
        },
      ],
      total: 1,
      page: 1,
      page_size: 20,
    },
  },
})

/** 用户身份由 store 从 localStorage 恢复；写入后模拟已登录。 */
function mountView() {
  localStorage.setItem(
    'user_info',
    JSON.stringify({
      id: 101,
      username: 'tester-101',
      email: 'tester-101@example.com',
      isAdmin: false,
      role: 'user',
      createdAt: '',
      organizations: [],
    }),
  )
  const pinia = createPinia()
  setActivePinia(pinia)
  const push = vi.fn()
  const wrapper = mount(UserDashboard, {
    global: {
      plugins: [pinia, i18n],
      mocks: { $router: { push } },
    },
  })
  return { wrapper, push }
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  vi.mocked(getUserStats).mockResolvedValue(statsFixture() as never)
  vi.mocked(getConversations).mockResolvedValue(conversationsFixture() as never)
  vi.mocked(getRAGStats).mockResolvedValue({
    data: {
      hit_rate: 0,
      total_queries_7d: 0,
      hits_with_documents: 0,
      avg_documents_retrieved: 0,
      hit_rate_trend_7d: [],
    },
  } as never)
  vi.mocked(getLLMStatus).mockResolvedValue({
    data: { success: true, data: { configured: false } },
  } as never)
  vi.mocked(seedDemoData).mockResolvedValue({ data: { success: false } } as never)
})

enableAutoUnmount(afterEach)

describe('T1 · 首页最近会话跳转参数名', () => {
  it('点击最近会话行跳 /chat?conversation_id=<id>（聊天页唯一消费的参数名）', async () => {
    const { wrapper, push } = mountView()
    await flushPromises()

    const recent = wrapper.find('[data-testid="home-recent-conversations"]')
    expect(recent.exists(), '缺少最近会话区块').toBe(true)
    expect(recent.text()).toContain('会话一')

    const row = recent.find('.cursor-pointer')
    expect(row.exists(), '最近会话行应可点击').toBe(true)
    await row.trigger('click')

    expect(push).toHaveBeenCalledTimes(1)
    expect(push).toHaveBeenCalledWith('/chat?conversation_id=c1')
    // 防御：不得携带聊天页不消费的旧参数名
    expect(push.mock.calls[0][0]).not.toMatch(/[?&]conversation=/)
  })

  it('回归：src 生产代码不再出现 `?conversation=`（不带 _id）形态', () => {
    // 直接读源码文件（与 locales/__tests__/completeness 等同类扫描一致），
    // 只扫生产代码：跳过 __tests__ / *.test.* / *.spec.* / *.d.ts / test-setup。
    const srcRoot = resolve(process.cwd(), 'src')
    const walk = (dir: string): string[] =>
      readdirSync(dir, { withFileTypes: true }).flatMap((ent) => {
        const abs = resolve(dir, ent.name)
        if (ent.isDirectory()) return ent.name === '__tests__' ? [] : walk(abs)
        if (!/\.(vue|ts)$/.test(ent.name)) return []
        if (/\.d\.ts$|\.(test|spec)\./.test(ent.name) || ent.name === 'test-setup.ts') return []
        return [abs]
      })

    const offenders = walk(srcRoot)
      .filter((abs) => /[?&]conversation=/.test(readFileSync(abs, 'utf-8')))
      .map((abs) => abs.slice(srcRoot.length + 1).replace(/\\/g, '/'))

    expect(
      offenders,
      `生产代码出现旧参数名 ?conversation=（聊天页只消费 conversation_id）：${offenders.join(', ')}`,
    ).toEqual([])
  })
})
