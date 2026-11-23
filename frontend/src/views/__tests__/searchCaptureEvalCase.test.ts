/**
 * 搜索页「保存为评测用例」（bad-case → eval case）。
 *
 * 行为契约：
 * - 入口只在有结果时出现（空态不出现，避免把「没结果」误读成可保存动作）；
 * - 弹窗预填本次 query，期望文档从「本次结果涉及的文档」勾选，另可远程补充；
 * - 一个期望文档都没选 → 提交按钮禁用/拦截，不发请求；
 * - 成功（created=true）→ 成功提示「已保存为评测用例」，弹窗关闭；
 * - 幂等命中（created=false）→ 提示「已存在」而非失败，弹窗同样关闭；
 * - 后端 400/409 等 → 弹窗内展示后端语义文案，弹窗保持打开（可改后重试）。
 *
 * 变异点（已实测）：
 * - 把 created=false 也按成功提示（去掉「已存在」分支）→ 幂等用例红
 *   （body 不含「已存在」）；
 * - 同时去掉提交按钮的 disabled 绑定与 submit 的空金标 guard → 「未选期望文档
 *   时提交被拦截」用例红（按钮不再禁用）；
 * - 提交漏传 query / expected_document_ids → api 调用参数断言红；
 * - 错误不显示在弹窗里 → 错误文案断言红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import naive, { NMessageProvider } from 'naive-ui'

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
  getKnowledgeBases: vi.fn().mockResolvedValue({
    data: {
      success: true,
      data: { data: [{ id: 'doc-extra', title: 'Extra Doc', file_name: 'extra.md', status: 'indexed' }], total: 1 },
    },
  }),
}))
vi.mock('@/api/retrievalEval', () => ({
  captureEvalCase: vi.fn(),
}))

import SearchView from '../search.vue'
import { searchKnowledgeResponse } from '@/api/search'
import type { SearchResult } from '@/api/search'
import { captureEvalCase } from '@/api/retrievalEval'
import { useUserStore } from '@/stores/user'
import type { UserInfo } from '@/types/user'
import zh from '@/locales/zh'
import en from '@/locales/en'

const mockedSearch = vi.mocked(searchKnowledgeResponse)
const mockedCapture = vi.mocked(captureEvalCase)

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

async function mountAndSearch(locale: 'zh' | 'en' = 'zh', results: SearchResult[] = [RESULT]) {
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
  const wrapper = mount(Host, { global: { plugins: [pinia, router, i18n, naive] }, attachTo: document.body })
  await flushPromises()

  mockedSearch.mockResolvedValue({
    query: 'alpha', results, total_count: results.length, search_mode: 'hybrid', index_status: 'ok',
  })
  await wrapper.find('input').setValue('alpha')
  const btn = wrapper.findAll('button').find((b) => (b.attributes('class') || '').includes('search-submit'))
  expect(btn, '搜索按钮应在屏').toBeTruthy()
  await btn!.trigger('click')
  await flushPromises()
  await nextTick()
  return wrapper
}

function bodyText(): string {
  return document.body.textContent || ''
}

async function openCapture(wrapper: Awaited<ReturnType<typeof mountAndSearch>>) {
  const trigger = wrapper.find('[data-testid="search-capture-eval-case"]')
  expect(trigger.exists(), '保存为评测用例入口应在结果状态栏出现').toBe(true)
  await trigger.trigger('click')
  await flushPromises()
  await nextTick()
}

beforeEach(() => {
  vi.clearAllMocks()
  mockedCapture.mockReset()
  document.body.innerHTML = ''
})

describe('搜索页 · 保存为评测用例', () => {
  it('空结果不出现入口；有结果时出现且文案走词条（zh/en 双向）', async () => {
    const emptyWrapper = await mountAndSearch('zh', [])
    expect(emptyWrapper.find('[data-testid="search-capture-eval-case"]').exists()).toBe(false)
    emptyWrapper.unmount()

    const zhWrapper = await mountAndSearch('zh')
    const zhTrigger = zhWrapper.find('[data-testid="search-capture-eval-case"]')
    expect(zhTrigger.text()).toContain('保存为评测用例')
    zhWrapper.unmount()

    const enWrapper = await mountAndSearch('en')
    const enTrigger = enWrapper.find('[data-testid="search-capture-eval-case"]')
    expect(enTrigger.text()).toContain('Save as eval case')
    expect(document.body.textContent || '').not.toContain('保存为评测用例')
    enWrapper.unmount()
  })

  it('弹窗预填 query 与结果文档；未选期望文档时提交被拦截（不发请求）', async () => {
    const wrapper = await mountAndSearch('zh')
    await openCapture(wrapper)

    // 弹窗查询回显
    const queryEcho = document.querySelector('[data-testid="search-capture-query"]')
    expect(queryEcho?.textContent?.trim()).toBe('alpha')
    // 未勾选任何文档 → 警示文案 + 提交禁用
    expect(document.querySelector('[data-testid="search-capture-empty-warning"]')).toBeTruthy()
    const submit = document.querySelector('[data-testid="search-capture-submit"]') as HTMLButtonElement
    expect(submit.disabled).toBe(true)
    await submit.click()
    await flushPromises()
    expect(mockedCapture).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('勾选结果文档并提交：传 query + 金标，成功提示并关闭弹窗', async () => {
    mockedCapture.mockResolvedValue({
      created: true,
      case: {
        id: 'case-1', organization_id: 3, name: 'alpha', query: 'alpha',
        expected_document_ids: ['doc-1'], tags: [], created_by: 1,
        created_at: null, updated_at: null,
      },
    })
    const wrapper = await mountAndSearch('zh')
    await openCapture(wrapper)

    // naive-ui 的复选框是 role="checkbox" 的 div（不是原生 input），只能按角色取。
    const checkbox = document.querySelector('[data-testid="search-capture-results"] [role="checkbox"]') as HTMLElement
    expect(checkbox, '结果文档勾选框应在屏').toBeTruthy()
    checkbox.click()
    await flushPromises()
    await nextTick()
    const submit = document.querySelector('[data-testid="search-capture-submit"]') as HTMLButtonElement
    expect(submit.disabled).toBe(false)
    submit.click()
    await flushPromises()
    await nextTick()

    expect(mockedCapture).toHaveBeenCalledTimes(1)
    const payload = mockedCapture.mock.calls[0][0]
    expect(payload.query).toBe('alpha')
    expect(payload.expected_document_ids).toEqual(['doc-1'])
    expect(payload.name).toBeUndefined() // 未填名称 → 交给后端缺省

    await flushPromises()
    expect(bodyText()).toContain('已保存为评测用例')
    // 弹窗关闭：naive-ui 的离场过渡结束后把卡片置为 display:none（jsdom 不派发
    // transitionend，组件在过渡超时后才落地，故等一拍定时器）。
    await new Promise((resolve) => setTimeout(resolve, 500))
    await nextTick()
    const closedCard = document.querySelector('.search-capture-modal') as HTMLElement | null
    expect(closedCard === null || closedCard.style.display === 'none', '保存成功后弹窗应关闭').toBe(true)
    wrapper.unmount()
  })

  it('输入框在出结果后被改动/清空：保存的仍是产生结果的 query', async () => {
    mockedCapture.mockResolvedValue({
      created: true,
      case: {
        id: 'case-2', organization_id: 3, name: 'alpha', query: 'alpha',
        expected_document_ids: ['doc-1'], tags: [], created_by: 1,
        created_at: null, updated_at: null,
      },
    })
    const wrapper = await mountAndSearch('zh')
    // 模拟用户看到结果后修改搜索框但未重新搜索。
    await wrapper.find('input').setValue('未提交的另一个查询')
    await openCapture(wrapper)
    expect(document.querySelector('[data-testid="search-capture-query"]')?.textContent?.trim()).toBe('alpha')
    ;(document.querySelector('[data-testid="search-capture-results"] [role="checkbox"]') as HTMLElement).click()
    await flushPromises()
    ;(document.querySelector('[data-testid="search-capture-submit"]') as HTMLButtonElement).click()
    await flushPromises()
    expect(mockedCapture).toHaveBeenCalledTimes(1)
    expect(mockedCapture.mock.calls[0][0].query).toBe('alpha')
    wrapper.unmount()
  })

  it('幂等命中既有用例（created=false）提示已存在而不是失败', async () => {
    mockedCapture.mockResolvedValue({
      created: false,
      case: {
        id: 'case-1', organization_id: 3, name: '既有用例', query: 'alpha',
        expected_document_ids: ['doc-1'], tags: [], created_by: 1,
        created_at: null, updated_at: null,
      },
    })
    const wrapper = await mountAndSearch('zh')
    await openCapture(wrapper)
    ;(document.querySelector('[data-testid="search-capture-results"] [role="checkbox"]') as HTMLElement).click()
    await flushPromises()
    ;(document.querySelector('[data-testid="search-capture-submit"]') as HTMLButtonElement).click()
    await flushPromises()
    await nextTick()

    expect(bodyText()).toContain('已存在')
    expect(bodyText()).toContain('既有用例')
    expect(document.querySelector('[data-testid="search-capture-error"]')).toBeNull()
    wrapper.unmount()
  })

  it('后端拒绝（400 跨组织/ACL）在弹窗内展示语义文案并保持打开', async () => {
    mockedCapture.mockRejectedValue({
      isAxiosError: true,
      response: {
        status: 400,
        data: { message: 'expected_document_ids 含不属于当前组织或不可检索的文档 id' },
      },
    })
    const wrapper = await mountAndSearch('zh')
    await openCapture(wrapper)
    ;(document.querySelector('[data-testid="search-capture-results"] [role="checkbox"]') as HTMLElement).click()
    await flushPromises()
    ;(document.querySelector('[data-testid="search-capture-submit"]') as HTMLButtonElement).click()
    await flushPromises()
    await nextTick()

    const error = document.querySelector('[data-testid="search-capture-error"]')
    expect(error, '错误必须显示在弹窗内').toBeTruthy()
    expect(error!.textContent).toContain('不属于当前组织')
    // 弹窗保持打开：提交按钮仍在
    expect(document.querySelector('[data-testid="search-capture-submit"]')).toBeTruthy()
    wrapper.unmount()
  })

  it('期望文档超过 50 个：明确提示上限并禁用提交，不发请求（不静默截断）', async () => {
    const manyDocs = Array.from({ length: 51 }, (_, i) => ({
      ...RESULT,
      id: `doc-${i}`,
      source: { filename: `doc-${i}.md`, document_id: `doc-${i}` },
    })) as SearchResult[]
    const wrapper = await mountAndSearch('zh', manyDocs)
    await openCapture(wrapper)
    const checkboxes = document.querySelectorAll('[data-testid="search-capture-results"] [role="checkbox"]')
    expect(checkboxes.length).toBe(51)
    // 逐个勾选并等一拍：一次性连点 51 个会让同一 tick 内的状态更新互相覆盖。
    for (const box of Array.from(checkboxes)) {
      ;(box as HTMLElement).click()
      await nextTick()
    }
    await flushPromises()
    await nextTick()

    const warning = document.querySelector('[data-testid="search-capture-too-many"]')
    expect(warning, '超限提示应在屏').toBeTruthy()
    expect(warning!.textContent).toContain('50')
    const submit = document.querySelector('[data-testid="search-capture-submit"]') as HTMLButtonElement
    expect(submit.disabled, '超限时提交必须禁用').toBe(true)
    await submit.click()
    await flushPromises()
    expect(mockedCapture).not.toHaveBeenCalled()
    wrapper.unmount()
  }, 20000)

  it('自定义名称随请求发送（去首尾空白）', async () => {
    mockedCapture.mockResolvedValue({
      created: true,
      case: {
        id: 'case-1', organization_id: 3, name: '退款时效', query: 'alpha',
        expected_document_ids: ['doc-1'], tags: [], created_by: 1,
        created_at: null, updated_at: null,
      },
    })
    const wrapper = await mountAndSearch('zh')
    await openCapture(wrapper)
    const nameInput = document.querySelector('[data-testid="search-capture-name"] input') as HTMLInputElement
    expect(nameInput, '名称输入框应在屏').toBeTruthy()
    nameInput.value = '  退款时效  '
    nameInput.dispatchEvent(new Event('input'))
    await flushPromises()
    ;(document.querySelector('[data-testid="search-capture-results"] [role="checkbox"]') as HTMLElement).click()
    await flushPromises()
    ;(document.querySelector('[data-testid="search-capture-submit"]') as HTMLButtonElement).click()
    await flushPromises()
    await nextTick()

    expect(mockedCapture.mock.calls[0][0].name).toBe('退款时效')
    wrapper.unmount()
  })
})
