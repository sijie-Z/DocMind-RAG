/**
 * J2 · 组织架构表列宽与行高统一。
 *
 * 现场（截图 r4-admin-orgs）：表头被挤成「组织…」、长组织名换行撑高单行，
 * 行高参差；描述列无宽度约束，窄屏下列相互挤压。
 * 修法：表格 single-line（行高统一 + 单元格省略）、横向 scroll-x 兜底，
 * 所有数据列显式宽度/最小宽度，名称与描述列 ellipsis 悬浮全称。
 *
 * 变异点：去掉 single-line / scroll-x，或描述列不设宽度 → 对应用例红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createI18n } from 'vue-i18n'
import { NMessageProvider } from 'naive-ui'

import OrganizationsView from '../index.vue'
import { getOrganizations, getOrganizationTree } from '@/api/organization'
import zh from '@/locales/zh'

vi.mock('@/api/organization', () => ({
  getOrganizations: vi.fn(),
  getOrganizationTree: vi.fn(),
  getOrganizationMembers: vi.fn(),
  getOrganizationDocuments: vi.fn(),
  createOrganization: vi.fn(),
  updateOrganization: vi.fn(),
  deleteOrganization: vi.fn(),
  batchDeleteOrganizations: vi.fn(),
  addOrganizationMember: vi.fn(),
  updateOrganizationMemberRole: vi.fn(),
  removeOrganizationMember: vi.fn(),
}))
vi.mock('@/api/user', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/user')>()
  return { ...actual, getUsers: vi.fn() }
})

const ORG = {
  id: 3,
  name: '一个很长很长的组织名称用于验证单行省略与行高统一',
  color: '#18a058',
  description: '描述文本',
  parent_id: null,
  level: 1,
  sort_order: 0,
  created_at: '2026-10-08T05:40:55',
  updated_at: '2026-10-08T05:40:55',
}

const DataTableCapture = defineComponent({
  name: 'DataTable',
  props: {
    data: { type: Array, default: () => [] },
    columns: { type: Array, default: () => [] },
    rowProps: { type: Function, default: null },
    singleLine: { type: Boolean, default: false },
    scrollX: { type: [Number, String], default: undefined },
  },
  setup() {
    return () => h('div', { class: 'dt-stub' })
  },
})

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

const Host = defineComponent({
  render: () => h(NMessageProvider, null, { default: () => h(OrganizationsView) }),
})

function mountView() {
  return mount(Host, {
    global: {
      plugins: [i18n],
      stubs: { DataTable: DataTableCapture, teleport: true },
    },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getOrganizations).mockResolvedValue({ data: { data: { data: [ORG], total: 1 } } } as never)
  vi.mocked(getOrganizationTree).mockResolvedValue({ data: { data: [ORG] } } as never)
})

describe('J2 · 组织表列宽与行高统一', () => {
  it('行高统一：表格 single-line 开启，横向溢出以 scroll-x 兜底', async () => {
    const wrapper = mountView()
    await flushPromises()

    const table = wrapper.findComponent(DataTableCapture)
    expect(table.exists(), '应渲染组织列表表格').toBe(true)
    expect(table.props('singleLine'), 'single-line 保证单元格单行、行高一致').toBe(true)

    const columns = table.props('columns') as Array<Record<string, unknown>>
    const declaredWidth = columns.reduce((sum, col) => {
      if (!col || col.type === 'selection') return sum
      return sum + Number(col.width ?? col.minWidth ?? 0)
    }, 50)
    expect(Number(table.props('scrollX')), 'scroll-x 应覆盖列宽总和，避免挤压表头').toBeGreaterThanOrEqual(declaredWidth)
  })

  it('列宽收敛：每个数据列都有显式宽度/最小宽度，长文本列省略并悬浮全称', async () => {
    const wrapper = mountView()
    await flushPromises()

    const table = wrapper.findComponent(DataTableCapture)
    const columns = table.props('columns') as Array<Record<string, unknown>>

    for (const col of columns) {
      if (!col || col.type === 'selection') continue
      const sized = typeof col.width === 'number' || typeof col.minWidth === 'number'
      expect(sized, `列「${String(col.title ?? col.key)}」缺少显式宽度`).toBe(true)
    }

    const nameCol = columns.find((col) => col.key === 'name')
    expect(nameCol?.ellipsis, '组织名称列应省略并悬浮全称').toBeTruthy()

    const descCol = columns.find((col) => col.key === 'description')
    expect(descCol?.ellipsis, '描述列应省略并悬浮全称').toBeTruthy()
  })
})
