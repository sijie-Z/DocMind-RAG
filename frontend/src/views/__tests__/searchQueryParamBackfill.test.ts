/**
 * W2-T7 项 3：顶栏搜索 `?q=` 进搜索页即回填并发起搜索（第三轮终检 §2.4）。
 *
 * 现状（修前）：布局 handleGlobalSearch push `{ name: 'Search', query: { q } }`，
 * 而搜索页 onMounted 只拉统计，不读 route.query.q —— 顶栏搜索的词**直接丢**，
 * 用户落在空搜索页。修复：挂载时读 q 回填并自动发起搜索；同页再次提交（q 变化）
 * 经 watch 重新搜索。
 *
 * 变异点：
 * - 删 onMounted 的 q 读取 → 「挂载即回填并发起搜索」用例红；
 * - 删 watch → 「同页再次提交自动重搜」用例红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import { NMessageProvider } from 'naive-ui'
import { createRouter, createMemoryHistory } from 'vue-router'

vi.mock('@/api/search', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/search')>()
  return {
    ...actual,
    searchKnowledge: vi.fn().mockResolvedValue([]),
    getSearchSuggestions: vi.fn().mockResolvedValue([]),
    getSearchStats: vi.fn().mockResolvedValue({ total_files: 2, file_types: {}, recent_searches: [] }),
  }
})

import SearchView from '../search.vue'
import { searchKnowledge } from '@/api/search'
import { useUserStore } from '@/stores/user'
import zh from '@/locales/zh'

const mockedSearch = vi.mocked(searchKnowledge)

// ── Mount（先例照 searchResultsRender.test.ts；本文件用真实 router 提供 route.query）──
const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

const Host = defineComponent({
  render: () => h(NMessageProvider, null, { default: () => h(SearchView) }),
})

const SlotStub = (name: string) =>
  defineComponent({ name, setup(_, { slots }) { return () => h('div', slots.default?.()) } })
const ModalStub = defineComponent({
  name: 'Modal',
  setup(_, { slots }) {
    return () => h('div', { class: 'n-modal-stub' }, slots.default?.())
  },
})

const stubs = {
  Icon: SlotStub('Icon'),
  Modal: ModalStub,
  Tag: SlotStub('Tag'),
  Button: true,
  Spin: true,
  RadioGroup: true,
  Radio: true,
  CheckboxGroup: true,
  Checkbox: true,
  Select: true,
  Space: true,
}

async function mountAt(query: string) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/search', name: 'Search', component: { template: '<div />' } },
      { path: '/chat', name: 'Chat', component: { template: '<div />' } },
    ],
  })
  await router.push(query ? `/search?${query}` : '/search')
  await router.isReady()
  const pinia = createPinia()
  setActivePinia(pinia)
  const user = useUserStore()
  user.userInfo = {
    id: 1, username: 'tester', email: 't@t', isAdmin: false,
    organization_id: 3, organizations: [], createdAt: '', updatedAt: '',
  } as never
  const wrapper = mount(Host, { global: { plugins: [pinia, router, i18n], stubs } })
  await flushPromises()
  await nextTick()
  return { wrapper, router }
}

beforeEach(() => {
  mockedSearch.mockClear()
})

describe('W2-T7 · 顶栏搜索词进搜索页（?q= 回填 + 自动搜索）', () => {
  it('带 ?q= 进入：输入框回填关键词并自动发起搜索（修前词直接丢）', async () => {
    const { wrapper } = await mountAt(`q=${encodeURIComponent('架构设计')}`)
    await flushPromises()

    const input = wrapper.find('input')
    expect((input.element as HTMLInputElement).value).toBe('架构设计')
    expect(mockedSearch).toHaveBeenCalledTimes(1)
    expect(mockedSearch.mock.calls[0][0]).toMatchObject({ query: '架构设计' })
  })

  it('不带 ?q=：不自动搜索（保持冷启动空态）', async () => {
    await mountAt('')
    await flushPromises()
    expect(mockedSearch).not.toHaveBeenCalled()
  })

  it('同页再次提交（q 变化）：watch 重新回填并发起搜索', async () => {
    const { wrapper, router } = await mountAt(`q=${encodeURIComponent('甲主题')}`)
    await flushPromises()
    expect(mockedSearch).toHaveBeenCalledTimes(1)

    await router.push(`/search?q=${encodeURIComponent('乙主题')}`)
    await flushPromises()
    await nextTick()

    const input = wrapper.find('input')
    expect((input.element as HTMLInputElement).value).toBe('乙主题')
    expect(mockedSearch).toHaveBeenCalledTimes(2)
    expect(mockedSearch.mock.calls[1][0]).toMatchObject({ query: '乙主题' })
  })
})
