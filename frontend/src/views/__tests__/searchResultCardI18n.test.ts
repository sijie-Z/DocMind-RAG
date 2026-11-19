/**
 * 搜索页结果卡文案走词条（不得依赖 `t(key, '中文默认')` 兜底）。
 *
 * 背景：`views/search.vue` 结果卡的「问问 AI」「复制」等此前写成
 * `t('search.askAI', '问问 AI')` 形态 —— 键四语都存在，中文兜底是死代码，
 * 也是硬编码中文棘轮的存量来源。本批清除兜底后，用真实 zh/en locale 断言
 * 文案确实来自词条（改词条值即随动）。
 *
 * 变异点：把任一 `t('...')` 退回硬编码中文 → en 用例红（全等文本匹配）；
 * 退回硬编码英文 → zh 用例红（两语言互补）；
 * 改词条值 → 对应用例红（断言用字面值，不 import 词条）。
 * 注意：退回 `t(key, '中文默认')` 形态本身不会让本文件变红 —— 键存在时
 * 默认值从不生效，守它的是 hardcodedCjk 棘轮（已实测会红）。
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

/** 结果卡动作按钮：按可见文本**全等**匹配（拒绝子串蒙混，评审 B M3/M10）。 */
function findResultAction(w: { findAll: (sel: string) => Array<{ text: () => string }> }, label: string) {
  return w.findAll('button').find((b) => (b.text() || '').trim() === label)
}

/** 复制按钮：data-testid 优先（避免「首个带 title 的按钮」的实现细节依赖）。 */
function findCopyButton(w: { find: (sel: string) => { exists: () => boolean; attributes: (n: string) => string | undefined } }) {
  return w.find('[data-testid="search-result-copy"]')
}

const Host = defineComponent({
  render: () => h(NMessageProvider, null, { default: () => h(SearchView) }),
})

async function mountAndSearch(locale: 'zh' | 'en', results: SearchResult[] = [RESULT]) {
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
    query: '', results, total_count: results.length, search_mode: 'hybrid', index_status: 'ok',
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
  it('结果项无文件名 → 显示 unknownFile 词条（en 下为 Unknown file）', async () => {
    const w = await mountAndSearch('en', [{ ...RESULT, metadata: { snippet: 'alpha' }, source: { document_id: 'doc-1' } } as SearchResult])
    expect(w.text()).toContain(en.search.unknownFile)
    expect(w.text()).not.toContain('未知文件')
    w.unmount()
  })

  it('zh：结果卡「问问 AI」按钮全等中文，复制按钮 title/aria-label 全等', async () => {
    const w = await mountAndSearch('zh')
    const askBtn = findResultAction(w, '问问 AI')
    expect(askBtn, '「问问 AI」按钮应按全等文本命中').toBeTruthy()
    const copyBtn = findCopyButton(w)
    expect(copyBtn.exists(), '复制按钮应在屏').toBe(true)
    expect(copyBtn.attributes('title')).toBe('复制')
    expect(copyBtn.attributes('aria-label')).toBe('复制')
    w.unmount()
  })

  it('en：结果卡「Ask AI」全等英文，Copy 按钮两属性全等（硬编码中文/英文均会露馅）', async () => {
    const w = await mountAndSearch('en')
    expect(findResultAction(w, 'Ask AI'), '「Ask AI」按钮应按全等文本命中').toBeTruthy()
    // 反向：不存在任何「问问 AI」按钮（硬编码中文）
    expect(findResultAction(w, '问问 AI'), '不应残留中文按钮').toBeFalsy()
    const copyBtn = findCopyButton(w)
    expect(copyBtn.attributes('title')).toBe('Copy')
    expect(copyBtn.attributes('aria-label')).toBe('Copy')
    w.unmount()
  })
})
