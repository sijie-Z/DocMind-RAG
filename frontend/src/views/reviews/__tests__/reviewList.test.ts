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
      components: { NButton, NDataTable, NEmpty, NPagination, NSelect, NSpin, NTag }
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

    expect(api.list).toHaveBeenCalledWith({ status: undefined, page: 1, page_size: 20 })
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
    expect(api.list).toHaveBeenLastCalledWith({ status: 'ready', page: 1, page_size: 20 })
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
