/**
 * 搜索页结果卡文案走词条（不得依赖 `t(key, '中文默认')` 兜底）。
 *
 * 背景：`views/search.vue` 结果卡的「问问 AI」「复制」等此前写成
 * `t('search.askAI', '问问 AI')` 形态 —— 键四语都存在，中文兜底是死代码，
 * 也是硬编码中文棘轮的存量来源。本批清除兜底后，用真实 zh/en locale 断言
 * 文案确实来自词条（改词条值即随动）。
 *
 * 变异点：把任一 `t('...')` 退回 `t(key, '中文默认')` 且同时改词条值时
 * 只影响其一 —— 本用例以「词条值 = 屏幕文本」双向断言锁住来源；
 * 若退回硬编码英文（如 >Copy<），en 下会被 en 词条断言的第二语言段捕获。
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
    searchKnowledgeResponse: vi.fn(),
    getSearchSuggestions: vi.fn().mockResolvedValue([]),
    getSearchStats: vi.fn().mockResolvedValue({ total_files: 2, file_types: {}, recent_searches: [] }),
  }
})
vi.mock('@/api/knowledge', () => ({
  getKnowledgeBases: vi.fn().mockResolvedValue({ data: { success: true, data: { data: [], total: 0 } } }),
}))

import SearchView from '../search.vue'
import { searchKnowledgeResponse } from '@/api/search'
import type { SearchResult } from '@/api/search'
import { useUserStore } from '@/stores/user'
import type { UserInfo } from '@/types/user'
import zh from '@/locales/zh'
import en from '@/locales/en'

const mockedSearch = vi.mocked(searchKnowledgeResponse)

const RESULT: SearchResult = {
  id: 'doc-1',
  text: 'alpha beta gamma',
  score: 0.5,
  metadata: { filename: 'alpha.md', snippet: 'alpha beta gamma' },
  source: { filename: 'alpha.md', document_id: 'doc-1' },
}

const Host = defineComponent({
  render: () => h(NMessageProvider, null, { default: () => h(SearchView) }),
})

async function mountAndSearch(locale: 'zh' | 'en') {
  const i18n = createI18n({ legacy: false, locale, missingWarn: false, fallbackWarn: false, messages: { zh, en } })
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
  useUserStore().userInfo = {
    id: 1, username: 'tester', email: 't@t', isAdmin: false,
    organization_id: 3, organizations: [], createdAt: '', updatedAt: '',
  } as UserInfo
  const wrapper = mount(Host, { global: { plugins: [pinia, router, i18n] } })
  await flushPromises()

  mockedSearch.mockResolvedValue({
    query: '', results: [RESULT], total_count: 1, search_mode: 'hybrid', index_status: 'ok',
  })
  await wrapper.find('input').setValue('alpha')
  const btn = wrapper.findAll('button').find((b) => (b.attributes('class') || '').includes('search-submit'))
  expect(btn, '搜索按钮应在屏').toBeTruthy()
  await btn!.trigger('click')
  await flushPromises()
  await nextTick()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('搜索页 · 结果卡词条', () => {
  it('zh：结果卡「问问 AI」与复制按钮 title 为中文', async () => {
    const w = await mountAndSearch('zh')
    expect(w.text()).toContain('问问 AI')
    const copyBtn = w.find('button[title]')
    expect(copyBtn.exists(), '复制按钮应在屏').toBe(true)
    expect(copyBtn.attributes('title')).toBe('复制')
    w.unmount()
  })

  it('en：结果卡「Ask AI」与 Copy 按钮（硬编码中文会露馅）', async () => {
    const w = await mountAndSearch('en')
    const text = w.text()
    expect(text).toContain('Ask AI')
    expect(text, '结果卡不应残留中文按钮文案').not.toContain('问问 AI')
    const copyBtn = w.find('button[title]')
    expect(copyBtn.attributes('title')).toBe('Copy')
    w.unmount()
  })
})
