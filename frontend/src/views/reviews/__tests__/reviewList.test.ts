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

vi.mock('vue-router', () => ({
  useRouter: () => ({ push }),
  useRoute: () => ({ params: {} })
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
    expect(wrapper.text()).toContain('base:legacy_unverified')
    expect(wrapper.text()).toContain('已阻断')
  })

  it('空列表显示空态', async () => {
    api.list.mockResolvedValue({ data: { data: { items: [], total: 0 } } } as never)
    const wrapper = mountList()
    await flushPromises()
    expect(wrapper.text()).toContain('还没有核验案件')
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
  })
})
