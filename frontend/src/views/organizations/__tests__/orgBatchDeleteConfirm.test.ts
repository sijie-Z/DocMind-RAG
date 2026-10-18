/**
 * E2 · 组织批量删除二次确认判别用例。
 *
 * 验收口径（E2 五处破坏性操作之一）：
 *  - 未确认（取消/关闭）→ batchDeleteOrganizations 不得被调用，勾选状态不变；
 *  - 确认后 → 调用接口并清空勾选；
 *  - 确认弹窗必须经 Naive UI dialog.warning 弹出（与 profile API Key 确认同法）。
 *
 * 变异点：去掉 dialog.warning 或不等确认直接删除 → 负相用例红（接口被调用）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import { NMessageProvider } from 'naive-ui'

import OrganizationsView from '../index.vue'
import {
  getOrganizations,
  getOrganizationTree,
  batchDeleteOrganizations,
} from '@/api/organization'
import zh from '@/locales/zh'

interface DialogOptions {
  title?: string
  content?: string
  onPositiveClick?: () => unknown
  onNegativeClick?: () => unknown
  onClose?: () => unknown
}

const { dialogState, dialogWarning } = vi.hoisted(() => {
  const state = {
    decision: 'cancel' as 'cancel' | 'confirm',
    calls: [] as DialogOptions[],
  }
  const warning = vi.fn((opts: DialogOptions) => {
    state.calls.push(opts)
    if (state.decision === 'confirm') opts.onPositiveClick?.()
    else opts.onNegativeClick?.()
  })
  return { dialogState: state, dialogWarning: warning }
})

vi.mock('naive-ui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('naive-ui')>()
  return {
    ...actual,
    useDialog: () => ({ warning: dialogWarning, info: vi.fn() }),
  }
})

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

const mockedGetOrganizations = vi.mocked(getOrganizations)
const mockedGetTree = vi.mocked(getOrganizationTree)
const mockedBatchDelete = vi.mocked(batchDeleteOrganizations)

const ORG = {
  id: 3, name: 'Tester Org', color: '#18a058', description: '', parent_id: null,
  level: 1, sort_order: 0, created_at: '2026-10-08T05:40:55', updated_at: '2026-10-08T05:40:55',
}

const DataTableStub = defineComponent({
  name: 'DataTable',
  props: {
    data: { type: Array, default: () => [] },
    columns: { type: Array, default: () => [] },
    rowProps: { type: Function, default: null },
  },
  setup(props) {
    return () =>
      h(
        'div',
        { class: 'dt-stub' },
        (props.data as Record<string, unknown>[]).map((row, ri) =>
          h(
            'div',
            { class: 'dt-row', key: ri },
            (props.columns as Array<Record<string, unknown>>).map((col, ci) => {
              if (!col || col.type === 'selection') return h('span', { class: 'dt-cell', key: ci }, '')
              const cell = typeof col.render === 'function'
                ? (col.render as (r: unknown) => unknown)(row)
                : (row[col.key as string] ?? '')
              return h('span', { class: 'dt-cell', key: ci }, [cell as never])
            }),
          ),
        ),
      )
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
      stubs: { DataTable: DataTableStub, teleport: true },
    },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  dialogState.decision = 'cancel'
  dialogState.calls.length = 0
  dialogWarning.mockClear()
  mockedGetOrganizations.mockResolvedValue({ data: { data: { data: [ORG], total: 1 } } } as never)
  mockedGetTree.mockResolvedValue({ data: { data: [ORG] } } as never)
  mockedBatchDelete.mockResolvedValue({ data: { success: true } } as never)
})

describe('组织批量删除 · 二次确认（E2）', () => {
  it('负相（取消）：不调批量删除接口，勾选与列表不变', async () => {
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.findComponent(OrganizationsView).vm as unknown as {
      selectedIds: number[]
      handleBatchDelete: () => Promise<void>
    }
    vm.selectedIds = [ORG.id]
    await nextTick()

    dialogState.decision = 'cancel'
    await vm.handleBatchDelete()

    expect(dialogWarning).toHaveBeenCalledTimes(1)
    expect(mockedBatchDelete).not.toHaveBeenCalled()
    expect(vm.selectedIds).toEqual([ORG.id])
  })

  it('正相（确认）：调用批量删除接口并清空勾选', async () => {
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.findComponent(OrganizationsView).vm as unknown as {
      selectedIds: number[]
      handleBatchDelete: () => Promise<void>
    }
    vm.selectedIds = [ORG.id]
    await nextTick()

    dialogState.decision = 'confirm'
    await vm.handleBatchDelete()
    await flushPromises()

    expect(dialogWarning).toHaveBeenCalledTimes(1)
    expect(String(dialogState.calls[0]?.content)).toContain('1')
    expect(mockedBatchDelete).toHaveBeenCalledWith([ORG.id])
    expect(vm.selectedIds).toEqual([])
  })
})
