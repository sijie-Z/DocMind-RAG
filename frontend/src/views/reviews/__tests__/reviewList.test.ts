/**
 * 合同核验列表页契约：筛选参数、空态、行点击跳转、分页参数。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { NButton, NDataTable, NEmpty, NPagination, NSpin, NTag, NSelect } from 'naive-ui'

import ReviewsIndex from '../index.vue'
import { reviewsApi } from '@/api/reviews'
import zh from '@/locales/zh'

const push = vi.fn()
const routerState = vi.hoisted(() => ({
  query: {} as Record<string, string>,
  replace: vi.fn()
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push, replace: routerState.replace }),
  useRoute: () => ({ params: {}, query: routerState.query })
}))

vi.mock('@/api/reviews', () => ({
  reviewsApi: { list: vi.fn() }
}))

const api = vi.mocked(reviewsApi)

const ROW = {
  id: 'case-abc12345',
  title: '采购合同 V1/V2 核验',
  status: 'blocked',
  checklist_version: 'v1',
  policy_document_id: 'p1',
  base_document_id: 'b1',
  revised_document_id: 'r1',
  base_version_label: 'V1',
  revised_version_label: 'V2',
  blocking_reasons: ['base:legacy_unverified'],
  created_by: 1,
  created_at: '2026-10-30T00:00:00Z'
}

function mountList() {
  const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })
  return mount(ReviewsIndex, {
    global: {
      plugins: [i18n],
      components: { NButton, NDataTable, NEmpty, NPagination, NSelect, NSpin, NTag },
      // 列表页用例不测建案弹窗；桩掉以免依赖 NMessageProvider 与候选接口。
      stubs: { CreateCaseModal: true }
    }
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  routerState.query = {}
  // 列表在 <1200 时渲染卡片；jsdom 默认 1024，会让表格用例找不到 tbody。
  Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: 1440 })
  api.list.mockResolvedValue({ data: { data: { items: [ROW], total: 1 } } } as never)
})

describe('合同核验列表页', () => {
  it('渲染案件与阻断原因，默认按第 1 页取数', async () => {
    const wrapper = mountList()
    await flushPromises()

    expect(api.list).toHaveBeenCalledWith({
      status: undefined,
      archived: 'active',
      page: 1,
      page_size: 20
    })
    expect(wrapper.text()).toContain('采购合同 V1/V2 核验')
    // 列表把原因码本地化展示；原始码保留在 title 属性里供排障。
    expect(wrapper.text()).toContain('合同基准版：旧版分块未迁移')
    expect(wrapper.text()).not.toContain('base:legacy_unverified')
    // 原始码保留在 title 属性里供排障
    expect(wrapper.find('span[title*="base:legacy_unverified"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('已阻断')
  })

  it('空列表显示空态', async () => {
    api.list.mockResolvedValue({ data: { data: { items: [], total: 0 } } } as never)
    const wrapper = mountList()
    await flushPromises()
    expect(wrapper.text()).toContain('还没有核验案件')
    expect(wrapper.text()).toContain('从知识库选择制度/模板与合同 V1/V2')
    expect(wrapper.find('[data-testid="reviews-empty-state"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="create-case-empty"]').exists()).toBe(true)
  })

  it('加载失败时不显示空态引导，只保留错误与重试', async () => {
    api.list.mockRejectedValue(new Error('boom'))
    const wrapper = mountList()
    await flushPromises()
    expect(wrapper.text()).toContain('加载核验案件失败')
    expect(wrapper.text()).toContain('重试')
    expect(wrapper.find('[data-testid="reviews-empty-state"]').exists()).toBe(false)
  })

  it('筛选状态回到第一页并带 status 参数', async () => {
    const wrapper = mountList()
    await flushPromises()
    const vm = wrapper.vm as unknown as { statusFilter: string | null }
    vm.statusFilter = 'ready'
    await (wrapper.vm as unknown as { applyFilter: () => void }).applyFilter()
    await flushPromises()
    expect(api.list).toHaveBeenLastCalledWith({
      status: 'ready',
      archived: 'active',
      page: 1,
      page_size: 20
    })
  })

  it('翻页时按新页码取数', async () => {
    const wrapper = mountList()
    await flushPromises()
    const vm = wrapper.vm as unknown as { page: number; load: () => Promise<void> }
    vm.page = 2
    await vm.load()
    await flushPromises()
    expect(api.list).toHaveBeenLastCalledWith({
      status: undefined,
      archived: 'active',
      page: 2,
      page_size: 20
    })
  })

  it('点击行跳转到详情页', async () => {
    const wrapper = mountList()
    await flushPromises()
    const row = wrapper.find('tbody tr')
    expect(row.exists()).toBe(true)
    await row.trigger('click')
    expect(push).toHaveBeenCalledWith('/reviews/case-abc12345')
  })
})

describe('归档过滤与标记（Batch 7）', () => {
  it('切换到已归档/全部时按 archived 参数取数并回到第一页', async () => {
    const wrapper = mountList()
    await flushPromises()
    const vm = wrapper.vm as unknown as {
      archivedFilter: string
      page: number
      applyFilter: () => void
    }
    vm.page = 3
    vm.archivedFilter = 'archived'
    vm.applyFilter()
    await flushPromises()
    expect(api.list).toHaveBeenLastCalledWith({
      status: undefined,
      archived: 'archived',
      page: 1,
      page_size: 20
    })

    vm.archivedFilter = 'all'
    vm.applyFilter()
    await flushPromises()
    expect(api.list).toHaveBeenLastCalledWith({
      status: undefined,
      archived: 'all',
      page: 1,
      page_size: 20
    })
  })

  it('已归档行渲染行内标记（data-testid 区分筛选文案）', async () => {
    api.list.mockResolvedValue({
      data: { data: { items: [{ ...ROW, archived_at: '2026-10-30T00:00:00Z' }], total: 1 } }
    } as never)
    const wrapper = mountList()
    await flushPromises()
    expect(wrapper.find('[data-testid="row-archived-tag"]').exists()).toBe(true)
  })

  it('已归档视图空态使用独立文案', async () => {
    api.list.mockResolvedValue({ data: { data: { items: [], total: 0 } } } as never)
    const wrapper = mountList()
    await flushPromises()
    const vm = wrapper.vm as unknown as { archivedFilter: string; applyFilter: () => void }
    vm.archivedFilter = 'archived'
    vm.applyFilter()
    await flushPromises()
    expect(wrapper.text()).toContain('还没有已归档案件')
    expect(wrapper.text()).toContain('已归档案件会保留在这里')
    expect(wrapper.find('[data-testid="create-case-empty"]').exists()).toBe(false)
  })
})

describe('归档筛选的事件接线', () => {
  it('NSelect update:value 触发 applyFilter 并按新参数取数', async () => {
    const wrapper = mountList()
    await flushPromises()
    const selects = wrapper.findAllComponents(NSelect)
    // 头部两个下拉：0=状态筛选，1=归档筛选
    selects[1].vm.$emit('update:value', 'archived')
    await flushPromises()
    expect(api.list).toHaveBeenLastCalledWith({
      status: undefined,
      archived: 'archived',
      page: 1,
      page_size: 20
    })
  })
})


describe('URL 状态与筛选空态（UI-6）', () => {
  it('从 URL 恢复状态/归档/页码并按其取数', async () => {
    routerState.query = { status: 'ready', archived: 'archived', page: '2' }
    mountList()
    await flushPromises()
    expect(api.list).toHaveBeenCalledWith({
      status: 'ready',
      archived: 'archived',
      page: 2,
      page_size: 20
    })
  })

  it('筛选无结果显示清除入口，清除后回到默认并同步 URL', async () => {
    api.list.mockResolvedValue({ data: { data: { items: [], total: 0 } } } as never)
    routerState.query = { status: 'blocked' }
    const wrapper = mountList()
    await flushPromises()
    expect(wrapper.text()).toContain('没有符合条件的核验案件')
    const clear = wrapper.find('[data-testid="reviews-clear-filters"]')
    expect(clear.exists()).toBe(true)
    await clear.trigger('click')
    await flushPromises()
    expect(api.list).toHaveBeenLastCalledWith({
      status: undefined,
      archived: 'active',
      page: 1,
      page_size: 20
    })
    expect(routerState.replace).toHaveBeenCalled()
  })
})

describe('服务端搜索与进度列（功能批次）', () => {
  it('搜索按 q 取数、回到第一页并同步 URL', async () => {
    const wrapper = mountList()
    await flushPromises()
    const vm = wrapper.vm as unknown as {
      page: number
      searchInput: string
      runSearchNow: () => void
    }
    vm.page = 3
    vm.searchInput = '  采购  '
    vm.runSearchNow()
    await flushPromises()
    expect(api.list).toHaveBeenLastCalledWith({
      status: undefined,
      archived: 'active',
      q: '采购',
      page: 1,
      page_size: 20
    })
    expect(routerState.replace).toHaveBeenCalledWith({ query: { q: '采购' } })
  })

  it('从 URL 恢复 q 并按其取数', async () => {
    routerState.query = { q: '合同' }
    mountList()
    await flushPromises()
    expect(api.list).toHaveBeenCalledWith({
      status: undefined,
      archived: 'active',
      q: '合同',
      page: 1,
      page_size: 20
    })
  })

  it('搜索无结果时显示筛选空态，清除筛选会去掉 q', async () => {
    api.list.mockResolvedValue({ data: { data: { items: [], total: 0 } } } as never)
    routerState.query = { q: '不存在' }
    const wrapper = mountList()
    await flushPromises()
    expect(wrapper.text()).toContain('没有符合条件的核验案件')
    await wrapper.find('[data-testid="reviews-clear-filters"]').trigger('click')
    await flushPromises()
    expect(api.list).toHaveBeenLastCalledWith({
      status: undefined,
      archived: 'active',
      page: 1,
      page_size: 20
    })
    expect(routerState.replace).toHaveBeenLastCalledWith({ query: {} })
  })

  it('渲染服务端下发的进度/差异计数与状态计数标签', async () => {
    api.list.mockResolvedValue({
      data: {
        data: {
          items: [
            {
              ...ROW,
              status: 'ready',
              item_count: 4,
              decided_count: 2,
              changed_count: 3,
              unidentified_count: 1,
              needs_human_count: 0,
              analysis_pending_count: 0,
              all_analyzed: true
            }
          ],
          total: 1,
          status_counts: { blocked: 0, ready: 1, in_review: 0, completed: 0 }
        }
      }
    } as never)
    const wrapper = mountList()
    await flushPromises()
    expect(wrapper.text()).toContain('2/4 已决策')
    expect(wrapper.text()).toContain('3 处差异 · 1 项未识别')
    const vm = wrapper.vm as unknown as {
      statusOptions: Array<{ value: string; label: string }>
    }
    expect(vm.statusOptions.find((option) => option.value === 'ready')?.label).toBe(
      '可核验 (1)'
    )
  })

  it('未分析的案件不报告差异数', async () => {
    api.list.mockResolvedValue({
      data: {
        data: {
          items: [
            {
              ...ROW,
              item_count: 4,
              decided_count: 0,
              changed_count: 0,
              all_analyzed: false
            }
          ],
          total: 1
        }
      }
    } as never)
    const wrapper = mountList()
    await flushPromises()
    expect(wrapper.text()).toContain('0/4 已决策')
    expect(wrapper.text()).toContain('未分析')
    expect(wrapper.text()).not.toContain('0 处差异')
  })

  it('输入经过防抖窗口后才发起搜索', async () => {
    const wrapper = mountList()
    await flushPromises()
    const callsAfterMount = api.list.mock.calls.length
    const vm = wrapper.vm as unknown as {
      searchInput: string
      scheduleSearch: () => void
    }
    vm.searchInput = '采购'
    vm.scheduleSearch()
    expect(api.list.mock.calls.length).toBe(callsAfterMount)
    await new Promise((resolve) => setTimeout(resolve, 350))
    await flushPromises()
    expect(api.list).toHaveBeenLastCalledWith(
      expect.objectContaining({ q: '采购', page: 1 })
    )
  })

  it('乱序响应只允许最新请求写入列表', async () => {
    let resolveFirst!: (value: unknown) => void
    let resolveSecond!: (value: unknown) => void
    const first = new Promise((resolve) => {
      resolveFirst = resolve
    })
    const second = new Promise((resolve) => {
      resolveSecond = resolve
    })
    api.list.mockImplementationOnce(() => first as never)
    api.list.mockImplementationOnce(() => second as never)

    const wrapper = mountList()
    await flushPromises()
    const vm = wrapper.vm as unknown as {
      searchInput: string
      runSearchNow: () => void
    }
    vm.searchInput = '新版'
    vm.runSearchNow()
    await flushPromises()

    resolveSecond({
      data: { data: { items: [{ ...ROW, id: 'case-new', title: '新版结果' }], total: 1 } }
    })
    await flushPromises()
    resolveFirst({
      data: { data: { items: [{ ...ROW, id: 'case-old', title: '旧版结果' }], total: 1 } }
    })
    await flushPromises()

    expect(wrapper.text()).toContain('新版结果')
    expect(wrapper.text()).not.toContain('旧版结果')
  })
})
