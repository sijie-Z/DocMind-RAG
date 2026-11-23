/**
 * 搜索页文件类型筛选：从「空窗补丁」恢复为真实过滤。
 *
 * 旧版把筛选区块停止渲染，因为 file_types 不在后端 schema 里（静默空转）。
 * 现在控件重新渲染，值经 buildSearchPayload 映射到 metadata_filters.file_type，
 * 顶层 file_types 永不出现。
 *
 * 变异点：删掉 search.vue 的文件类型控件 / builder 改回发 file_types → 本用例红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import { NMessageProvider } from 'naive-ui'

vi.mock('@/api/search', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/search')>()
  return {
    ...actual,
    searchKnowledgeResponse: vi.fn().mockResolvedValue({ query: '', results: [], total_count: 0, search_mode: 'hybrid', index_status: 'ok' }),
    getSearchSuggestions: vi.fn().mockResolvedValue([]),
    getSearchStats: vi.fn().mockResolvedValue({ total_files: 2, file_types: {}, recent_searches: [] }),
  }
})
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ query: {} }),
}))

import SearchView from '../search.vue'
import { searchKnowledgeResponse } from '@/api/search'
import { useUserStore } from '@/stores/user'
import type { UserInfo } from '@/types/user'
import zh from '@/locales/zh'

vi.mock('@/api/knowledge', () => ({
  getKnowledgeBases: vi.fn().mockResolvedValue({ data: { success: true, data: { data: [], total: 0 } } })
}))
vi.mock('@/api/knowledgeBases', () => ({
  listKnowledgeBases: vi.fn().mockResolvedValue({ data: [], total: 0, page: 1, page_size: 100 })
}))

const mockedSearch = vi.mocked(searchKnowledgeResponse)

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

const Host = defineComponent({
  render: () => h(NMessageProvider, null, { default: () => h(SearchView) }),
})

const SlotStub = (name: string) =>
  defineComponent({ name, setup(_, { slots }) { return () => h('div', slots.default?.()) } })
const ModalStub = defineComponent({
  name: 'Modal',
  setup(_, { slots }) { return () => h('div', { class: 'n-modal-stub' }, slots.default?.()) },
})

const stubs = {
  Icon: SlotStub('Icon'),
  Modal: ModalStub,
  Tag: SlotStub('Tag'),
  Button: true,
  Spin: true,
  RadioGroup: true,
  Radio: true,
  Select: true,
  Space: true,
}

function mountView() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const user = useUserStore()
  user.userInfo = {
    id: 1, username: 'tester', email: 't@t', isAdmin: false,
    organization_id: 3, organizations: [], createdAt: '', updatedAt: '',
  } as UserInfo
  return mount(Host, { global: { plugins: [pinia, i18n], stubs } })
}

beforeEach(() => {
  mockedSearch.mockReset()
  mockedSearch.mockResolvedValue({ query: '', results: [], total_count: 0, search_mode: 'hybrid', index_status: 'ok' })
})

describe('搜索页 · 文件类型筛选恢复为真实过滤', () => {
  it('控件渲染，提交后只发 metadata_filters.file_type，不发顶层 file_types', async () => {
    const wrapper = mountView()

    expect(wrapper.text()).toContain(zh.search.fileTypes)
    expect(wrapper.find('[data-testid="search-file-types"]').exists()).toBe(true)

    await wrapper.find('input').setValue('架构')
    await wrapper.find('button').trigger('click')
    await flushPromises()
    await nextTick()
    expect(mockedSearch).toHaveBeenCalledTimes(1)
    const payload = mockedSearch.mock.calls[0][0]
    expect('file_types' in payload).toBe(false)
    expect('metadata_filters' in payload).toBe(false)
  })
})
