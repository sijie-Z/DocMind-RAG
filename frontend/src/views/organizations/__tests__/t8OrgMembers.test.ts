/**
 * T8 组织页「成员管理最简 UI」判别用例。
 *
 * 复核证据（15f681b 实测）：成员 tab 只有只读列表（成员 (1) / tester / 普通
 * 用户），无任何邀请/移除/改角色控件；drawerButtons=[]、hasInviteUI=false。
 * 后端成员端点已就绪（POST/PATCH role/DELETE，openapi 实测）。
 * 修法：成员 tab 补「添加成员」入口 + 行内改角色 + 移除。
 * 变异点：删「添加成员」按钮 → 用例红；删行内控件 → 用例红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import { NMessageProvider, NDialogProvider } from 'naive-ui'

import OrganizationsView from '../index.vue'
import { getUsers } from '@/api/user'
import {
  getOrganizations,
  getOrganizationTree,
  getOrganizationMembers,
  getOrganizationDocuments,
} from '@/api/organization'
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

const mockedGetOrganizations = vi.mocked(getOrganizations)
const mockedGetTree = vi.mocked(getOrganizationTree)
const mockedGetMembers = vi.mocked(getOrganizationMembers)
const mockedGetDocs = vi.mocked(getOrganizationDocuments)
const mockedGetUsers = vi.mocked(getUsers)

const ORG = {
  id: 3, name: 'Tester Org', color: '#18a058', description: '', parent_id: null,
  level: 1, sort_order: 0, created_at: '2026-10-08T05:40:55', updated_at: '2026-10-08T05:40:55',
}
const MEMBER = {
  id: 9, username: 'tester', nickname: 'tester', email: 'tester@example.com',
  role: 'user', is_active: true, last_login_at: null,
}
const OUTSIDER = {
  id: 10, username: 'outsider', nickname: 'outsider', email: 'outsider@example.com',
  role: 'user', is_active: true, last_login_at: null,
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
            {
              class: 'dt-row',
              key: ri,
              onClick: props.rowProps ? (props.rowProps as (r: unknown) => { onClick?: () => void })(row).onClick : undefined,
            },
            (props.columns as Array<Record<string, unknown>>).map((col, ci) => {
              if (!col || col.type === 'selection') return h('span', { class: 'dt-cell', key: ci }, '')
              const cell = typeof col.render === 'function' ? (col.render as (r: unknown) => unknown)(row) : (row[col.key as string] ?? '')
              return h('span', { class: 'dt-cell', key: ci }, [cell as never])
            })
          )
        )
      )
  },
})

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

const Host = defineComponent({
  render: () => h(NMessageProvider, null, { default: () => h(NDialogProvider, null, { default: () => h(OrganizationsView) }) }),
})

function mountView() {
  return mount(Host, {
    global: {
      plugins: [i18n],
      // DataTable 以组件名（'DataTable'）打桩——与 usersListRender 用例同法；
      // drawer/modal 内容 teleport 到 body；内联后 wrapper.find 可见
      stubs: { DataTable: DataTableStub, teleport: true },
    },
  })
}

async function openOrgDrawer(wrapper: ReturnType<typeof mountView>) {
  await flushPromises()
  const row = wrapper.find('.dt-row')
  expect(row.exists(), '组织列表应有行').toBe(true)
  await row.trigger('click')
  await flushPromises()
  await nextTick()
}

beforeEach(() => {
  vi.clearAllMocks()
  mockedGetOrganizations.mockResolvedValue({ data: { data: { data: [ORG], total: 1 } } } as never)
  mockedGetTree.mockResolvedValue({ data: { data: [ORG] } } as never)
  mockedGetMembers.mockResolvedValue({ data: { data: [MEMBER] } } as never)
  mockedGetDocs.mockResolvedValue({ data: { data: [] } } as never)
  mockedGetUsers.mockResolvedValue({ data: { total: 2, users: [MEMBER, OUTSIDER] } } as never)
})

describe('OrganizationsView · T8 成员管理 UI', () => {
  it('成员 tab 含「添加成员」入口与行内「移除」控件（此前只有只读列表）', async () => {
    const wrapper = mountView()
    await openOrgDrawer(wrapper)

    // 切到成员 tab
    const memberTab = wrapper.findAll('.n-tabs-tab').find((t) => t.text().includes('成员'))
    expect(memberTab, '未找到成员 tab').toBeTruthy()
    await memberTab!.trigger('click')
    await flushPromises()
    await nextTick()

    const text = wrapper.text()
    expect(text).toContain('tester')
    expect(text).toContain('添加成员')
    expect(text).toContain('移除')
  })

  it('「添加成员」打开选人弹窗并拉取用户列表', async () => {
    const wrapper = mountView()
    await openOrgDrawer(wrapper)
    const memberTab = wrapper.findAll('.n-tabs-tab').find((t) => t.text().includes('成员'))
    await memberTab!.trigger('click')
    await flushPromises()

    const addBtn = wrapper.findAll('button').find((b) => b.text().includes('添加成员'))
    expect(addBtn, '未找到 添加成员 按钮').toBeTruthy()
    await addBtn!.trigger('click')
    await flushPromises()
    await nextTick()

    expect(mockedGetUsers).toHaveBeenCalled()
    expect(wrapper.text()).toContain('选择用户')
  })
})
