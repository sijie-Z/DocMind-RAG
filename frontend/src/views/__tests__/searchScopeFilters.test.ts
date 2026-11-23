/**
 * 竞品批次六 feature A · 搜索页范围筛选接线（判别性测试）。
 *
 * 钉住：KB/tags/file_type 选择器可达、请求体精确、切组织清空 KB、
 * clearFilters 重置三项、带筛选空态与「只发一次搜索」（绝不自动放宽重试）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import { NMessageProvider } from 'naive-ui'

const mocks = vi.hoisted(() => ({
  search: vi.fn(),
  suggestions: vi.fn(),
  stats: vi.fn(),
  docs: vi.fn(),
  listKBs: vi.fn(),
}))

vi.mock('@/api/search', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/search')>()
  return {
    ...actual,
    searchKnowledgeResponse: mocks.search,
    getSearchSuggestions: mocks.suggestions,
    getSearchStats: mocks.stats,
  }
})
vi.mock('@/api/knowledge', () => ({ getKnowledgeBases: mocks.docs }))
vi.mock('@/api/knowledgeBases', () => ({ listKnowledgeBases: mocks.listKBs }))
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ query: {} }),
}))

import SearchView from '../search.vue'
import { useUserStore } from '@/stores/user'
import type { UserInfo } from '@/types/user'
import zh from '@/locales/zh'

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
const ButtonStub = defineComponent({
  name: 'Button',
  setup(_, { attrs, slots }) { return () => h('button', attrs, slots.default?.()) },
})
const stubs = {
  Icon: SlotStub('Icon'), Modal: ModalStub, Tag: SlotStub('Tag'),
  Button: ButtonStub, Spin: true, RadioGroup: true, Radio: true,
  Select: true, Space: true,
}

const emptyResponse = {
  query: '架构',
  results: [],
  total_count: 0,
  search_mode: 'hybrid',
  index_status: 'ok' as const,
  index_doc_count: 100,
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
  vi.clearAllMocks()
  mocks.search.mockResolvedValue(emptyResponse)
  mocks.suggestions.mockResolvedValue([])
  mocks.stats.mockResolvedValue({ total_files: 2, file_types: {}, recent_searches: [] })
  mocks.docs.mockResolvedValue({ data: { success: true, data: { data: [{ tags: ['合同'] }], total: 1 } } })
  mocks.listKBs.mockResolvedValue({
    data: [{ id: 'kb-1', organization_id: 3, name: '合同库' }],
    total: 1, page: 1, page_size: 100,
  })
})

describe('搜索页 KB / 标签 / 文件类型筛选', () => {
  it('三个控件存在；一次搜索只发一次请求，请求体精确包含筛选字段', async () => {
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('[data-testid="search-knowledge-base"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="search-tags"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="search-file-types"]').exists()).toBe(true)
    expect(mocks.listKBs).toHaveBeenCalledTimes(1)
    expect(mocks.listKBs.mock.calls[0][0]).toEqual({ silent: true })

    // naive Select 被 stub：v-model 通过 emitted update:value 驱动，等价于用户选择。
    const selects = wrapper.findAllComponents({ name: 'Select' })
    const kbSelect = wrapper.find('[data-testid="search-knowledge-base"]')
    expect(kbSelect.exists()).toBe(true)
    // 通过组件实例直接写入绑定值（stub 的 attrs 含 modelValue 与 onUpdate:value）
    const kbComponent = wrapper.findAllComponents({ name: 'Select' })
      .find((c) => c.attributes('data-testid') === 'search-knowledge-base')
    const tagsComponent = selects.find((c) => c.attributes('data-testid') === 'search-tags')
    const fileTypeComponent = selects.find((c) => c.attributes('data-testid') === 'search-file-types')
    expect(kbComponent && tagsComponent && fileTypeComponent).toBeTruthy()
    kbComponent!.vm.$emit('update:value', 'kb-1')
    tagsComponent!.vm.$emit('update:value', ['合同'])
    fileTypeComponent!.vm.$emit('update:value', ['docx'])
    await nextTick()

    await wrapper.find('input').setValue('架构')
    await wrapper.find('button').trigger('click')
    await flushPromises()
    await nextTick()

    expect(mocks.search).toHaveBeenCalledTimes(1)
    expect(mocks.search.mock.calls[0][0]).toEqual({
      query: '架构',
      organization_id: 3,
      metadata_filters: { tags: ['合同'], file_type: ['word'] },
      knowledge_base_id: 'kb-1',
    })
  })

  it('KB 加载失败降级为空选项，不阻断搜索', async () => {
    mocks.listKBs.mockRejectedValueOnce(new Error('KB down'))
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain(zh.search.knowledgeBaseLoadFailed)
    await wrapper.find('input').setValue('架构')
    await wrapper.find('button').trigger('click')
    await flushPromises()
    expect(mocks.search).toHaveBeenCalledTimes(1)
  })

  it('切组织清空 KB 选择并重载 KB 候选', async () => {
    const wrapper = mountView()
    await flushPromises()
    const kb = wrapper.findAllComponents({ name: 'Select' })
      .find((c) => c.attributes('data-testid') === 'search-knowledge-base')
    kb!.vm.$emit('update:value', 'kb-1')
    await nextTick()

    const user = useUserStore()
    // 直接改当前组织字段以触发 computed/watch（无公开 setter）
    const store = user as unknown as { currentOrganizationId: number | null }
    store.currentOrganizationId = 4
    await flushPromises()
    await nextTick()

    expect(mocks.listKBs).toHaveBeenCalledTimes(2)
    await wrapper.find('input').setValue('架构')
    await wrapper.find('button').trigger('click')
    await flushPromises()
    const payload = mocks.search.mock.calls[0][0]
    expect(payload.organization_id).toBe(4)
    expect(payload.knowledge_base_id).toBeUndefined()
  })

  it('clearFilters 重置 KB/tags/file_type；带筛选零命中给专属空态且不自动放宽重试', async () => {
    const wrapper = mountView()
    await flushPromises()

    const selects = wrapper.findAllComponents({ name: 'Select' })
    const kb = selects.find((c) => c.attributes('data-testid') === 'search-knowledge-base')
    const tags = selects.find((c) => c.attributes('data-testid') === 'search-tags')
    const types = selects.find((c) => c.attributes('data-testid') === 'search-file-types')
    kb!.vm.$emit('update:value', 'kb-1')
    tags!.vm.$emit('update:value', ['合同'])
    types!.vm.$emit('update:value', ['pdf'])
    await nextTick()

    await wrapper.find('input').setValue('架构')
    await wrapper.find('button').trigger('click')
    await flushPromises()
    await nextTick()

    expect(wrapper.find('[data-testid="search-empty-state"]').text()).toContain(zh.search.filteredEmpty)
    expect(mocks.search).toHaveBeenCalledTimes(1)

    const reset = wrapper.find('[data-testid="search-clear-filters"]')
    expect(reset.exists()).toBe(true)
    await reset.trigger('click')
    await nextTick()

    // 再搜一次：第二次请求不再带任何筛选，直接证明 clearFilters 已重置三项。
    await wrapper.find('button').trigger('click')
    await flushPromises()
    expect(mocks.search).toHaveBeenCalledTimes(2)
    expect(mocks.search.mock.calls[1][0]).toEqual({
      query: '架构',
      organization_id: 3,
    })
  })
})
