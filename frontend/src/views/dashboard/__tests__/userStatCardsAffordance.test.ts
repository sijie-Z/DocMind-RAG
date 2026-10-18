/**
 * C6 契约回归：用户仪表盘统计卡「行为与样式一致」。
 *
 * 背景：四张统计卡（对话 / 消息 / 文档 / 存储）此前带 cursor-pointer、
 * hover:shadow / hover:border / group-hover 缩放变色等「可点击」样式，
 * 但没有任何 @click——用户点了没反应，是典型的假按钮。
 *
 * 修复口径（本文件钉住）：去掉可点击样式（cursor-pointer / hover 态 /
 * group-hover 装饰），统计卡回归纯展示；点击不产生导航。
 *
 * 变异点（任一发生 → 本文件红）：给统计卡加回 cursor-pointer 或 hover: 类，
 * 或把它们重新做成可点击入口而未同步行为。
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import { NMessageProvider } from 'naive-ui'

import UserDashboard from '../user.vue'
import userDashboardRaw from '../user.vue?raw'
import { getUserStats } from '@/api/user'
import { getConversations } from '@/api/conversation'
import { getRAGStats } from '@/api/monitoring'
import { seedDemoData } from '@/api/demo'
import zh from '@/locales/zh'

vi.mock('@/api/user', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/user')>()
  return { ...actual, getUserStats: vi.fn() }
})
vi.mock('@/api/conversation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/conversation')>()
  return { ...actual, getConversations: vi.fn() }
})
vi.mock('@/api/monitoring', () => ({ getRAGStats: vi.fn() }))
vi.mock('@/api/demo', () => ({ seedDemoData: vi.fn() }))
vi.mock('@/utils/echarts', () => ({
  default: {
    init: vi.fn(() => ({ setOption: vi.fn(), dispose: vi.fn(), resize: vi.fn() })),
    graphic: { LinearGradient: class LinearGradient {} },
  },
}))

const mockedGetUserStats = vi.mocked(getUserStats)
const mockedGetConversations = vi.mocked(getConversations)
const mockedGetRAGStats = vi.mocked(getRAGStats)
const mockedSeedDemoData = vi.mocked(seedDemoData)

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

const push = vi.fn()

function mountView() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const Host = defineComponent({
    render: () => h(NMessageProvider, null, { default: () => h(UserDashboard) }),
  })
  return mount(Host, {
    global: {
      plugins: [pinia, i18n],
      mocks: { $router: { push } },
    },
  })
}

mockedGetUserStats.mockImplementation(async () => ({
  data: {
    conversation_count: 3,
    message_count: 7,
    file_count: 2,
    knowledge_count: 1,
    storage_used: 1024,
    activity_trend: [0, 1, 2, 3, 4, 5, 6],
  },
}) as never)
mockedGetConversations.mockImplementation(async () => ({
  data: { success: true, data: { data: [], total: 0, page: 1, page_size: 10 } },
}) as never)
mockedGetRAGStats.mockImplementation(async () => ({
  data: { hit_rate: 50, total_queries_7d: 10, hits_with_documents: 5, avg_documents_retrieved: 2, hit_rate_trend_7d: [] },
}) as never)
mockedSeedDemoData.mockResolvedValue({ data: { success: true } } as never)

afterEach(() => {
  push.mockReset()
})

describe('UserDashboard · 统计卡行为与样式一致（C6）', () => {
  it('四张统计卡不带 cursor-pointer / hover 态（不再伪装成按钮）', async () => {
    const wrapper = mountView()
    await flushPromises()
    await nextTick()

    const statNumbers = wrapper.findAll('p.text-3xl.font-bold.text-gray-900')
    expect(statNumbers.length, '四张统计卡数字应在屏').toBe(4)

    const cards = statNumbers.map((n) => n.element.parentElement as HTMLElement)
    for (const card of cards) {
      expect(card, '统计卡容器应在 DOM').toBeTruthy()
      const cls = card!.className
      expect(cls, `统计卡不应带 cursor-pointer：${cls}`).not.toContain('cursor-pointer')
      expect(cls, `统计卡不应带 hover: 态：${cls}`).not.toMatch(/(^|\s)hover:/)
      expect(cls, `统计卡不应带 group-hover 装饰：${cls}`).not.toContain('group-hover:')
    }

    wrapper.unmount()
  })

  it('点击统计卡不产生导航（样式与行为一致：纯展示）', async () => {
    const wrapper = mountView()
    await flushPromises()
    await nextTick()

    const statNumber = wrapper.find('p.text-3xl.font-bold.text-gray-900')
    await statNumber.trigger('click')
    expect(push).not.toHaveBeenCalled()

    wrapper.unmount()
  })

  it('防回退：模板中统计卡区域不再带可点击工具类', () => {
    const templateText = userDashboardRaw.split('<script')[0]
    // 模板里唯一允许保留的 cursor-pointer 是「最近会话」行（带 @click 跳转）；
    // 统计卡四张全部去掉后，模板只剩这一处。
    const occurrences = templateText.match(/cursor-pointer/g)?.length ?? 0
    expect(occurrences).toBe(1)
  })
})
