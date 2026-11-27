/**
 * 公开知识库只读页（批次十三）判别用例。
 *
 * 变异点：
 * - 失效 token 不显示不可用提示 → 失效态用例红；
 * - 搜索结果渲染内部字段 → 内部字段用例红；
 * - 空结果显示为错误（或反之）→ 空态用例红；
 * - 搜索不传 token/top_k → 调用断言红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { createMemoryHistory, createRouter } from 'vue-router'
import en from '@/locales/en'

const mocks = vi.hoisted(() => ({ get: vi.fn(), search: vi.fn() }))

vi.mock('@/api/publicKb', () => ({
  publicKbPublicApi: { get: mocks.get, search: mocks.search },
  publicKbApi: {},
}))

import PublicKbPage from '../kb.vue'

const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })

async function mountPage(token = 'token-1') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/public/kb/:token', component: { template: '<div />' } }],
  })
  await router.push(`/public/kb/${token}`)
  await router.isReady()
  const wrapper = mount(PublicKbPage, { global: { plugins: [i18n, router] } })
  return { wrapper, router }
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.get.mockResolvedValue({
    title: 'Public policy KB',
    description: 'Read-only policies',
    expires_at: null,
  })
  mocks.search.mockResolvedValue({
    results: [
      { rank: 1, title: 'Policy A', snippet: 'First clause text', score: 0.912 },
      { rank: 2, title: 'Policy B', snippet: 'Second clause text', score: 0.5 },
    ],
    total: 2,
  })
})

describe('public KB page', () => {
  it('匿名按 token 拉取元数据并展示标题/描述', async () => {
    const { wrapper } = await mountPage()
    await flushPromises()
    expect(mocks.get).toHaveBeenCalledWith('token-1')
    expect(wrapper.text()).toContain('Public policy KB')
    expect(wrapper.text()).toContain('Read-only policies')
    expect(wrapper.find('[data-testid="public-kb-unavailable"]').exists()).toBe(false)
  })

  it('搜索：提交 query/top_k，渲染 rank/title/snippet/score', async () => {
    const { wrapper } = await mountPage()
    await flushPromises()
    await wrapper.find('[data-testid="public-kb-search-input"]').setValue('policy')
    await wrapper.find('form.public-kb-search').trigger('submit')
    await flushPromises()

    expect(mocks.search).toHaveBeenCalledWith('token-1', 'policy', 10)
    expect(wrapper.find('[data-testid="public-kb-result-1"]').text()).toContain('Policy A')
    expect(wrapper.find('[data-testid="public-kb-result-1"]').text()).toContain('First clause text')
    expect(wrapper.find('[data-testid="public-kb-result-1"]').text()).toContain('0.912')
    expect(wrapper.text()).toContain('2 result(s)')
  })

  it('空结果：显示空态而不是错误', async () => {
    mocks.search.mockResolvedValue({ results: [], total: 0 })
    const { wrapper } = await mountPage()
    await flushPromises()
    await wrapper.find('[data-testid="public-kb-search-input"]').setValue('nothing')
    await wrapper.find('form.public-kb-search').trigger('submit')
    await flushPromises()
    expect(wrapper.find('[data-testid="public-kb-empty"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="public-kb-result-1"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="public-kb-unavailable"]').exists()).toBe(false)
  })

  it('失效/撤销/过期 token：元数据 404 → 统一不可用提示', async () => {
    mocks.get.mockRejectedValue({ response: { status: 404 } })
    const { wrapper } = await mountPage()
    await flushPromises()
    expect(wrapper.find('[data-testid="public-kb-unavailable"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('does not exist or has expired')
    expect(wrapper.find('[data-testid="public-kb-search-input"]').exists()).toBe(false)
  })

  it('检索期间链接被撤销（search 404）也按不可用处理', async () => {
    mocks.search.mockRejectedValue({ response: { status: 404 } })
    const { wrapper } = await mountPage()
    await flushPromises()
    await wrapper.find('[data-testid="public-kb-search-input"]').setValue('policy')
    await wrapper.find('form.public-kb-search').trigger('submit')
    await flushPromises()
    expect(wrapper.find('[data-testid="public-kb-unavailable"]').exists()).toBe(true)
  })

  it('元数据 5xx/网络错误 → 可重试错误态（不谎称链接失效），重试可恢复', async () => {
    mocks.get.mockRejectedValueOnce({ response: { status: 502 } })
    const { wrapper } = await mountPage()
    await flushPromises()
    expect(wrapper.find('[data-testid="public-kb-load-error"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="public-kb-unavailable"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('Search failed, please retry later')

    await wrapper.find('[data-testid="public-kb-retry"]').trigger('click')
    await flushPromises()
    expect(mocks.get).toHaveBeenCalledTimes(2)
    expect(wrapper.find('[data-testid="public-kb-load-error"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('Public policy KB')
  })

  it('检索 5xx/429/网络错误 → 保留搜索框并显示可重试错误，不切到失效态', async () => {
    mocks.search.mockRejectedValue({ response: { status: 502 } })
    const { wrapper } = await mountPage()
    await flushPromises()
    await wrapper.find('[data-testid="public-kb-search-input"]').setValue('policy')
    await wrapper.find('form.public-kb-search').trigger('submit')
    await flushPromises()
    expect(wrapper.find('[data-testid="public-kb-search-error"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="public-kb-unavailable"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="public-kb-search-input"]').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('does not exist or has expired')
  })

  it('只渲染公开字段：内部 id/路径/组织即使出现在响应里也不展示', async () => {
    mocks.get.mockResolvedValue({
      title: 'Public policy KB',
      description: 'Read-only policies',
      expires_at: null,
      organization_id: 991,
      knowledge_base_id: 'kb-internal-1',
      document_id: 'doc-internal-2',
    })
    mocks.search.mockResolvedValue({
      results: [
        {
          rank: 1,
          title: 'Policy A',
          snippet: 'First clause text',
          score: 0.9,
          document_id: 'doc-internal-2',
          chunk_id: 'chunk-internal-3',
          file_path: '/secret/path.pdf',
          uploaded_by: 77,
        },
      ],
      total: 1,
    })
    const { wrapper } = await mountPage()
    await flushPromises()
    await wrapper.find('[data-testid="public-kb-search-input"]').setValue('policy')
    await wrapper.find('form.public-kb-search').trigger('submit')
    await flushPromises()
    const text = wrapper.text()
    for (const forbidden of [
      'kb-internal-1',
      'doc-internal-2',
      'chunk-internal-3',
      '/secret/path.pdf',
      '991',
      '77',
    ]) {
      expect(text).not.toContain(forbidden)
    }
  })
})
