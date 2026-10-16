/**
 * T8 用户页「组织分配写死两部门」判别用例（15f681b 实测复核）。
 *
 * 复核证据：新建用户弹窗组织下拉实测选项 = ["技术部", "产品部"]（硬编码
 * organizationOptions 常量）；库内真实组织 = Default / Tester Org /
 * tester2 的空间 / issuehunt2026 的空间（GET /organizations 实测 4 个）。
 * 修法：选项改由 getOrganizations 动态拉取；并按单组织数据模型（User.
 * organization_id 单外键）把多选改单选。
 * 变异点：还原硬编码数组 → 用例 1 红；还原 multiple → 用例 2 红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import { NMessageProvider, NDialogProvider } from 'naive-ui'

import UsersView from '../index.vue'
import { getUsers } from '@/api/user'
import { getOrganizations } from '@/api/organization'
import zh from '@/locales/zh'

vi.mock('@/api/user', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/user')>()
  return {
    ...actual,
    getUsers: vi.fn(),
    createUser: vi.fn(),
    updateUser: vi.fn(),
    deleteUser: vi.fn(),
    resetUserPassword: vi.fn(),
  }
})
vi.mock('@/api/organization', () => ({ getOrganizations: vi.fn() }))

const mockedGetUsers = vi.mocked(getUsers)
const mockedGetOrganizations = vi.mocked(getOrganizations)

const flatBody = (users: unknown[]) => ({
  data: { total: users.length, skip: 0, limit: 10, users },
})

// 真实组织（GET /organizations 实测转录）
const REAL_ORGS = [
  { id: 2, name: 'Default' },
  { id: 3, name: 'Tester Org' },
  { id: 4, name: 'tester2 的空间' },
  { id: 5, name: 'issuehunt2026 的空间' },
]

const SelectStub = defineComponent({
  // eslint-disable-next-line vue/no-reserved-component-names -- 必须与 naive-ui NSelect 的组件 name（'Select'）一致，VTU 才能以桩替换真实组件
  name: 'Select',
  props: {
    options: { type: Array, default: () => [] },
    value: { type: [String, Number, Array], default: null },
    multiple: { type: Boolean, default: false },
    placeholder: { type: String, default: '' },
  },
  setup(props) {
    return () =>
      h(
        'div',
        { class: 'sel-stub', 'data-ph': props.placeholder, 'data-multiple': String(props.multiple) },
        JSON.stringify(props.options)
      )
  },
})

const DataTableStub = defineComponent({
  name: 'DataTable',
  props: { data: { type: Array, default: () => [] }, columns: { type: Array, default: () => [] } },
  setup() {
    return () => h('div', { class: 'dt-stub' })
  },
})

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

const Host = defineComponent({
  render: () =>
    h(NDialogProvider, null, { default: () => h(NMessageProvider, null, { default: () => h(UsersView) }) }),
})

function mountView() {
  return mount(Host, {
    global: {
      plugins: [i18n],
      components: { NSelect: SelectStub, NDataTable: DataTableStub },
      // n-modal 内容 teleport 到 body；内联后 wrapper.find 可见
      stubs: { teleport: true },
    },
  })
}

/** 打开「新建用户」弹窗（组织下拉在弹窗内，初始不挂载） */
async function openCreateModal(wrapper: ReturnType<typeof mountView>) {
  const btn = wrapper.findAll('button').find((b) => b.text().includes('新建用户'))
  expect(btn, '未找到 新建用户 按钮').toBeTruthy()
  await btn!.trigger('click')
  await flushPromises()
  await nextTick()
}

/** 找到组织下拉（选项里含真实组织名的那个 SelectStub） */
function findOrgSelect(wrapper: ReturnType<typeof mountView>) {
  return wrapper.findAllComponents(SelectStub).find((c) => {
    const opts = c.props('options') as Array<{ label: string }>
    return opts.some((o) => typeof o.label === 'string' && o.label.includes('Tester Org'))
  })
}

beforeEach(() => {
  mockedGetUsers.mockReset()
  mockedGetOrganizations.mockReset()
})

describe('UsersView · T8 组织选项动态化 + 单选', () => {
  it('组织选项来自 getOrganizations：不再出现硬编码「技术部/产品部」', async () => {
    mockedGetUsers.mockResolvedValue(flatBody([]) as never)
    mockedGetOrganizations.mockResolvedValue({
      data: { success: true, data: { data: REAL_ORGS, total: 4 } },
    } as never)

    const wrapper = mountView()
    await flushPromises()
    await openCreateModal(wrapper)

    expect(mockedGetOrganizations).toHaveBeenCalledTimes(1)
    const orgSel = findOrgSelect(wrapper)
    expect(orgSel, '未找到动态组织下拉（仍是硬编码选项？）').toBeTruthy()
    const options = orgSel!.props('options') as Array<{ label: string; value: number }>
    expect(options.map((o) => o.value)).toEqual([2, 3, 4, 5])
    const labels = options.map((o) => o.label)
    expect(labels).toContain('Tester Org')
    expect(labels).toContain('tester2 的空间')
    expect(labels).not.toContain('技术部')
    expect(labels).not.toContain('产品部')
  })

  it('组织选择为单选（单组织数据模型）', async () => {
    mockedGetUsers.mockResolvedValue(flatBody([]) as never)
    mockedGetOrganizations.mockResolvedValue({
      data: { success: true, data: { data: REAL_ORGS, total: 4 } },
    } as never)

    const wrapper = mountView()
    await flushPromises()
    await openCreateModal(wrapper)

    const orgSel = findOrgSelect(wrapper)
    expect(orgSel).toBeTruthy()
    expect(orgSel!.props('multiple')).toBe(false)
  })
})
