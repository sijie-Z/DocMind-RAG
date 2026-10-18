/**
 * E11-① 跨页上下文（搜索 → 聊天）：点击结果卡「问问 AI」时保留用户意图。
 *
 * 修前：askAI 只 push `{ q: '关于文件 "x"...', context_id }`——q 是带省略号的
 * 占位串（不含用户当前搜索词），所点结果的正文片段完全没有传递（context_id
 * 是 ES 内部 id，聊天页无法消费）；聊天页也从不读 q/context——跨页动作的
 * 「问哪个文件、问什么」全部丢失。
 * 修复：q = 文件名 + 当前搜索词组句（四语 i18n），context = 所点结果正文片段
 * （截断），聊天页读取后预填输入框。
 *
 * 变异点：
 * - askAI 回退旧占位串 / 不带 context → 本文件用例红；
 * - q 组句丢失搜索词 → 「q 含搜索词」断言红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import { NMessageProvider } from 'naive-ui'
import { createRouter, createMemoryHistory } from 'vue-router'

// 视图依赖的 api 全部打桩；importOriginal 保住 buildSearchPayload 等纯函数。
vi.mock('@/api/search', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/search')>()
  return {
    ...actual,
    searchKnowledge: vi.fn(),
    getSearchSuggestions: vi.fn().mockResolvedValue([]),
    getSearchStats: vi.fn().mockResolvedValue({ total_files: 2, file_types: {}, recent_searches: [] }),
  }
})

import SearchView from '../search.vue'
import { searchKnowledge } from '@/api/search'
import type { SearchResult } from '@/api/search'
import { useUserStore } from '@/stores/user'
import type { UserInfo } from '@/types/user'
import zh from '@/locales/zh'

const mockedSearch = vi.mocked(searchKnowledge)

// 真实响应结果项（抓包原文结构；text 截断）
const REAL_RESULT: SearchResult = {
  id: '7af44457-b0d1-419c-b5f8-182ef87383a5',
  text: '# DocMind 系统架构设计本文档描述了 DocMind 企业级 RAG 知识库系统的整体架构设计。## 系统分层架构',
  score: 0.618,
  metadata: {
    filename: 'architecture.md',
    snippet: '# DocMind 系统架构设计本文档描述了 DocMind 企业级 RAG 知识库系统的整体架构设计',
  },
  source: { filename: 'architecture.md', document_id: '7af44457-b0d1-419c-b5f8-182ef87383a5' },
}

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

const Host = defineComponent({
  render: () => h(NMessageProvider, null, { default: () => h(SearchView) }),
})

async function mountAt() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/search', name: 'Search', component: { template: '<div />' } },
      { path: '/chat', name: 'Chat', component: { template: '<div />' } },
    ],
  })
  await router.push('/search')
  await router.isReady()
  const pinia = createPinia()
  setActivePinia(pinia)
  const user = useUserStore()
  user.userInfo = {
    id: 1, username: 'tester', email: 't@t', isAdmin: false,
    organization_id: 3, organizations: [], createdAt: '', updatedAt: '',
  } as UserInfo
  const wrapper = mount(Host, {
    global: { plugins: [pinia, router, i18n] },
  })
  await flushPromises()
  return { wrapper, router }
}

async function searchAndOpenFirstCard(wrapper: Awaited<ReturnType<typeof mountAt>>['wrapper']) {
  mockedSearch.mockResolvedValue([REAL_RESULT])
  await wrapper.find('input').setValue('架构设计')
  const searchBtn = wrapper.findAll('button').find((b) => b.text().includes('搜索'))
  expect(searchBtn, '搜索按钮应在屏').toBeTruthy()
  await searchBtn!.trigger('click')
  await flushPromises()
  await nextTick()
  const card = wrapper.findAll('.group').find((w) => w.text().includes('architecture.md'))
  expect(card, '结果卡应在屏').toBeTruthy()
  return card!
}

beforeEach(() => {
  mockedSearch.mockReset()
})

describe('E11-① 搜索页「问问 AI」跨页带参', () => {
  it('点击结果卡「问问 AI」：跳转 Chat，q 含文件名与当前搜索词，context 携带所点结果正文片段', async () => {
    const { wrapper, router } = await mountAt()
    const card = await searchAndOpenFirstCard(wrapper)

    const askBtn = card.findAll('button').find((b) => b.text().includes('问问 AI'))
    expect(askBtn, '「问问 AI」按钮应在屏').toBeTruthy()
    await askBtn!.trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.name).toBe('Chat')
    const query = router.currentRoute.value.query as Record<string, string>
    // 修前 q 是占位串「关于文件 "architecture.md"...」：不含用户搜索词
    expect(String(query.q)).toContain('architecture.md')
    expect(String(query.q)).toContain('架构设计')
    // 修前只传 context_id（ES 内部 id，聊天页无法消费）；现在正文片段走 context
    expect(String(query.context)).toContain('DocMind 系统架构设计')
    expect(query.context_id).toBeUndefined()
  })
})
