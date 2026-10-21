/**
 * E14/D2 判别用例：搜索失败必须是独立可见的错误态（含重试），
 * 且不得把失败伪装成「无结果」、不得把上一次的旧结果当新结果显示。
 *
 * 修前现场：handleSearch 的 catch 只弹一条 toast；searchResults 保留上一次
 * 结果（或为空），模板把 hasSearched && !loading 统一直落「未找到相关结果」
 * ——用户无法区分「真的没搜到」与「请求失败」，也没有任何重试入口。
 *
 * 变异点：
 * - catch 不清空旧结果 → 「旧结果不得残留」红；
 * - 失败态与无结果态不分离（仍渲染 search.noResults）→ 「失败≠空结果」红；
 * - 不渲染重试按钮 / 重试不重新发起请求 → 「重试」用例红；
 * - 失败时仍弹 toast（与内联错误态双表面）→ 「单一表面」用例红。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, enableAutoUnmount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createI18n } from 'vue-i18n'

import SearchView from '../../search.vue'
import { searchKnowledge } from '@/api/search'
import { useUserStore } from '@/stores/user'
import zh from '@/locales/zh'

// 每个用例后自动卸载，防止悬挂的响应式副作用在 jsdom 拆除后触碰 document
// （CI 上出现过 Unhandled ReferenceError: document is not defined 的时序性假红）。
enableAutoUnmount(afterEach)

const { messageSpies } = vi.hoisted(() => ({
  messageSpies: { error: vi.fn(), info: vi.fn(), success: vi.fn(), warning: vi.fn(), loading: vi.fn() },
}))

vi.mock('@/utils/message', () => ({ useDedupedMessage: () => messageSpies }))

vi.mock('@/api/search', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/search')>()
  return {
    ...actual,
    searchKnowledge: vi.fn(),
    getSearchSuggestions: vi.fn().mockResolvedValue([]),
    getSearchStats: vi.fn().mockResolvedValue({ total_files: 0, file_types: {}, recent_searches: [] }),
  }
})

const mockedSearch = vi.mocked(searchKnowledge)

/** axios 传输层错误（真实上屏黑话的来源） */
function axiosError(status?: number, detail?: string) {
  const err = new Error(status ? `Request failed with status code ${status}` : 'Network Error')
  return Object.assign(err, {
    isAxiosError: true,
    config: {},
    ...(status ? { response: { status, data: detail ? { detail } : {} } } : {}),
  })
}

const result = (id: string, filename: string) => ({
  id,
  text: `正文-${id}`,
  score: 0.92,
  source: { filename },
})

const i18n = createI18n({ legacy: false, locale: 'zh', missingWarn: false, fallbackWarn: false, messages: { zh } })

async function mountSearch(orgId: number | null = 7) {
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
  const userStore = useUserStore()
  userStore.userInfo = { id: 1, username: 'u', organization_id: orgId } as never
  const wrapper = mount(SearchView, { global: { plugins: [i18n, pinia, router] } })
  await flushPromises()
  return wrapper
}

async function searchFor(wrapper: Awaited<ReturnType<typeof mountSearch>>, query: string) {
  await wrapper.find('input[type="text"]').setValue(query)
  await wrapper.find('.search-submit').trigger('click')
  await flushPromises()
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
})

describe('SearchView · 失败态（E14/D2）', () => {
  it('第二次搜索失败：清空上一次旧结果，显示独立错误态 + 重试按钮，不伪装成「无结果」', async () => {
    mockedSearch.mockResolvedValueOnce([result('1', '旧结果.pdf')] as never)
    const wrapper = await mountSearch()

    await searchFor(wrapper, 'alpha')
    expect(wrapper.text()).toContain('旧结果.pdf')

    // 第二次搜索：后端 500
    mockedSearch.mockRejectedValueOnce(axiosError(500))
    await searchFor(wrapper, 'beta')

    // 旧结果不得冒充新结果残留
    expect(wrapper.text()).not.toContain('旧结果.pdf')
    // 独立错误态：标题 + 描述 + 重试入口
    expect(wrapper.text()).toContain('搜索失败')
    expect(wrapper.text()).toContain('请检查网络或稍后重试')
    expect(wrapper.find('.search-retry').exists()).toBe(true)
    // 失败 ≠ 空结果
    expect(wrapper.text()).not.toContain('未找到相关结果')
    // 传输层黑话不得上屏
    expect(wrapper.text()).not.toContain('Request failed')
    // 单一表面：内联错误态已可见，不再叠加 toast
    expect(messageSpies.error).not.toHaveBeenCalled()
  })

  it('失败发生在首次搜索（此前无结果）：同样走错误态而不是「无结果」空态', async () => {
    mockedSearch.mockRejectedValueOnce(axiosError())
    const wrapper = await mountSearch()

    await searchFor(wrapper, 'alpha')

    expect(wrapper.text()).toContain('搜索失败')
    expect(wrapper.find('.search-retry').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('未找到相关结果')
    expect(wrapper.text()).not.toContain('Network Error')
  })

  it('点击重试：重新发起搜索，成功后错误态消失、结果恢复展示', async () => {
    mockedSearch.mockRejectedValueOnce(axiosError(500))
    const wrapper = await mountSearch()
    await searchFor(wrapper, 'alpha')
    expect(wrapper.text()).toContain('搜索失败')

    mockedSearch.mockResolvedValueOnce([result('2', '新结果.pdf')] as never)
    await wrapper.find('.search-retry').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('新结果.pdf')
    expect(wrapper.text()).not.toContain('搜索失败')
    expect(mockedSearch).toHaveBeenCalledTimes(2)
  })

  it('并发竞态：先发旧请求迟到返回，不得覆盖后发失败的错误态（旧结果不冒充新结果）', async () => {
    let resolveFirst!: (value: unknown) => void
    mockedSearch.mockImplementationOnce(
      () => new Promise((resolve) => { resolveFirst = resolve }) as never,
    )
    const wrapper = await mountSearch()

    const input = wrapper.find('input[type="text"]')
    // 第一次搜索：请求悬挂（慢响应）
    await input.setValue('alpha')
    await input.trigger('keyup.enter')
    await flushPromises()

    // 第二次搜索：快速失败（loading 期间按钮禁用，用回车触发）
    mockedSearch.mockRejectedValueOnce(axiosError(500))
    await input.setValue('beta')
    await input.trigger('keyup.enter')
    await flushPromises()
    expect(wrapper.text()).toContain('搜索失败')

    // 旧请求此时才返回：不得把旧结果当成本次结果展示，也不得吞掉错误态
    resolveFirst([result('1', '旧结果.pdf')])
    await flushPromises()

    expect(wrapper.text()).not.toContain('旧结果.pdf')
    expect(wrapper.text()).toContain('搜索失败')
  })

  it('失败后重试成功但结果为空：显示「未找到相关结果」，不得残留错误态', async () => {
    mockedSearch.mockRejectedValueOnce(axiosError(500))
    const wrapper = await mountSearch()
    await searchFor(wrapper, 'alpha')
    expect(wrapper.text()).toContain('搜索失败')

    mockedSearch.mockResolvedValueOnce([] as never)
    await wrapper.find('.search-retry').trigger('click')
    await flushPromises()

    expect(wrapper.text()).not.toContain('搜索失败')
    expect(wrapper.text()).toContain('未找到相关结果')
  })

  it('无当前组织：给出组织上下文专用文案（不冒充网络失败），不发请求且不显示无效重试', async () => {
    const wrapper = await mountSearch(null)
    await searchFor(wrapper, 'alpha')

    expect(mockedSearch).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('尚未加入任何组织')
    expect(wrapper.text()).not.toContain('请检查网络')
    expect(wrapper.text()).not.toContain('未找到相关结果')
    // 重试在当前状态下必然继续失败（需先切换/加入组织）——不提供无效入口
    expect(wrapper.find('.search-retry').exists()).toBe(false)
  })
})
