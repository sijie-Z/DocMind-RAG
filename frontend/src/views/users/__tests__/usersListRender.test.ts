/**
 * 用户管理页数据契约判别用例（UI 验收 round2 · A1/B18/B19/B21）。
 *
 * 真 UI 现场（_uisweep/A_sweep.md admin 段 A2）：/users 表格恒空 ——
 * 后端 GET /api/v1/users/ 真实响应是**裸扁平** {total, skip, limit, users}
 * （无 success/data 信封，users.py:792-794），前端却按 res.data.data.data 解包
 * → undefined → 恒走 else 清空；分页参数发 page/page_size/search 均被 FastAPI
 * 静默忽略（只有 skip/limit）；行内 status 列读不存在的 row.status（真键是布尔
 * is_active）；搜索过滤 u.nickname.toLowerCase() 在 full_name=null 的真实用户
 * （如 admin/tester）上必炸（A1 潜伏崩溃）。
 *
 * 变异点：
 * - 还原三重解包（res.data.data.data）→ 「扁平形状」用例精确红（表格恒空）；
 * - 改回 page/page_size/search 参数 → 「分页参数」用例精确红；
 * - 还原 u.nickname.toLowerCase() → 「搜索防御」用例红（TypeError）；
 * - 状态列改回 row.status === 'active' → 「is_active」用例红（全员"禁用"）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import { NMessageProvider, NDialogProvider, NEmpty } from 'naive-ui'

import UsersView from '../index.vue'
import { getUsers } from '@/api/user'
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

const mockedGetUsers = vi.mocked(getUsers)

// ── Fixtures（真实响应字段逐字：A_sweep.md:222 curl 转录）──────────────────
const ADMIN = {
  id: 1,
  username: 'admin',
  email: 'admin@example.com',
  full_name: null, // 真实用户 full_name 可为 null（A1 崩溃的触发条件）
  organization_id: null,
  role: 'admin',
  is_active: true,
  created_at: '2026-10-07T11:15:48',
  last_login_at: null,
}

const BOB = {
  id: 2,
  username: 'bob',
  email: 'bob@example.com',
  full_name: 'Bob Li',
  organization_id: 3,
  role: 'user',
  is_active: false,
  created_at: '2026-10-08T05:39:53',
  last_login_at: null,
}

const flatBody = (users: unknown[], total?: number) => ({
  data: { total: total ?? users.length, skip: 0, limit: 100, users },
})

// naive-ui 组件内部 name 无 N 前缀；DataTable 以轻量桩渲染行/列文本（含表头）
const DataTableStub = defineComponent({
  name: 'DataTable',
  props: {
    data: { type: Array, default: () => [] },
    columns: { type: Array, default: () => [] },
    pagination: { type: Object, default: () => ({}) },
  },
  setup(props) {
    return () =>
      h('div', { class: 'dt-stub' }, [
        h(
          'div',
          { class: 'dt-head' },
          (props.columns as Array<Record<string, unknown>>).map((col, ci) =>
            h('span', { class: 'dt-hcell', key: ci }, typeof col?.title === 'string' ? (col.title as string) : '')
          )
        ),
        ...(props.data as Record<string, unknown>[]).map((row, ri) =>
          h(
            'div',
            { class: 'dt-row', key: ri },
            (props.columns as Array<Record<string, unknown> & { key?: string; type?: string; render?: (r: unknown) => unknown }>).map(
              (col, ci) => {
                if (!col || col.type === 'selection') return h('span', { class: 'dt-cell' }, '')
                const cell = typeof col.render === 'function' ? col.render(row) : (row[col.key as string] ?? '')
                return h('span', { class: 'dt-cell' }, [cell as never])
              }
            )
          )
        ),
      ])
  },
})

const SlotStub = (name: string) =>
  defineComponent({ name, setup(_, { slots }) { return () => h('div', slots.default?.()) } })

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

const Host = defineComponent({
  render: () => h(NDialogProvider, null, { default: () => h(NMessageProvider, null, { default: () => h(UsersView) }) }),
})

function mountView() {
  return mount(Host, {
    global: {
      plugins: [i18n],
      // n-empty 在生产由 app.use(naive) 全局注册；测试里显式注册真组件
      components: { NEmpty },
      stubs: { DataTable: DataTableStub, Tag: SlotStub('Tag'), Button: true, Icon: true },
    },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  mockedGetUsers.mockReset()
})

describe('UsersView · 列表契约（A1/B18/B19/B21）', () => {
  it('真实扁平形状 {total,skip,limit,users}：表格出数据（不再恒空）', async () => {
    mockedGetUsers.mockResolvedValue(flatBody([ADMIN]) as never)
    const wrapper = mountView()
    await flushPromises()
    await nextTick()

    expect(wrapper.text()).toContain('admin')
    expect(wrapper.text()).toContain('admin@example.com')
    expect(wrapper.text()).not.toContain('暂无用户数据')
    // 表格真的渲染出一行
    expect(wrapper.findAll('.dt-row')).toHaveLength(1)
  })

  it('分页参数对齐后端：只发 skip/limit，不再发送被静默忽略的 page/page_size/search', async () => {
    mockedGetUsers.mockResolvedValue(flatBody([ADMIN]) as never)
    const wrapper = mountView()
    await flushPromises()

    expect(mockedGetUsers).toHaveBeenCalledTimes(1)
    const arg = mockedGetUsers.mock.calls[0][0] as Record<string, unknown>
    expect(arg).toEqual({ skip: 0, limit: 10 })
    expect(arg).not.toHaveProperty('page')
    expect(arg).not.toHaveProperty('page_size')
    expect(arg).not.toHaveProperty('search')

    // 翻页 → skip 偏移正确（第 2 页 10/页 → skip=10）
    const dt = wrapper.findComponent(DataTableStub)
    ;(dt.props('pagination') as { onChange: (p: number) => void }).onChange(2)
    await flushPromises()
    // E14/D1：第二个实参为 silent 请求配置（X-Silent-Error），分页参数仍只含 skip/limit
    const lastCall = mockedGetUsers.mock.calls[mockedGetUsers.mock.calls.length - 1]
    expect(lastCall[0] as Record<string, unknown>).toEqual({ skip: 10, limit: 10 })
  })

  it('状态列绑定真实 is_active 布尔：false → "禁用"，true → "激活"', async () => {
    mockedGetUsers.mockResolvedValue(flatBody([ADMIN, BOB]) as never)
    const wrapper = mountView()
    await flushPromises()

    const rows = wrapper.findAll('.dt-row')
    expect(rows).toHaveLength(2)
    // 第 1 行 admin（is_active=true）→ 激活；第 2 行 bob（false）→ 禁用
    expect(rows[0].text()).toContain('激活')
    expect(rows[0].text()).not.toContain('禁用')
    expect(rows[1].text()).toContain('禁用')
    // 显示名 ← full_name（Bob Li 可见；admin 的 null → 占位符不减分）
    expect(rows[1].text()).toContain('Bob Li')
  })

  it('搜索防御：full_name=null 的用户不崩（原 u.nickname.toLowerCase() 崩溃点）', async () => {
    mockedGetUsers.mockResolvedValue(flatBody([ADMIN, BOB]) as never)
    const wrapper = mountView()
    await flushPromises()

    const input = wrapper.find('input')
    await input.setValue('adm')
    await nextTick()
    // 无 TypeError；admin 命中（username），bob 被过滤
    expect(wrapper.findAll('.dt-row')).toHaveLength(1)
    expect(wrapper.text()).toContain('admin')

    await input.setValue('bob@')
    await nextTick()
    // email 命中 bob；admin 被过滤
    expect(wrapper.findAll('.dt-row')).toHaveLength(1)
    expect(wrapper.text()).toContain('bob')

    await input.setValue('zzz-no-match')
    await nextTick()
    expect(wrapper.text()).toContain('暂无用户数据')
  })

  it('负例：畸形响应（无 users 键）不崩、诚实空态', async () => {
    mockedGetUsers.mockResolvedValue({ data: null } as never)
    const wrapper = mountView()
    await flushPromises()
    await nextTick()

    expect(wrapper.findAll('.dt-row')).toHaveLength(0)
    expect(wrapper.text()).toContain('暂无用户数据')
  })

  it('表头渲染中文标签而非裸 i18n 键（K2：profile.email / profile.status 曾直出）', async () => {
    mockedGetUsers.mockResolvedValue(flatBody([ADMIN]) as never)
    const wrapper = mountView()
    await flushPromises()

    const head = wrapper.find('.dt-head').text()
    expect(head).toContain('用户名')
    expect(head).toContain('邮箱')
    expect(head).toContain('状态')
    expect(head).toContain('操作')
    // 判别：任何裸键名不得上屏（含表单/弹窗引用族）
    expect(wrapper.text()).not.toMatch(/profile\.[a-z]/)
    expect(wrapper.text()).not.toMatch(/users\.[a-z]/)
    expect(wrapper.text()).not.toMatch(/common\.[a-z]/)
    expect(wrapper.text()).not.toMatch(/validation\.[a-z]/)
  })
})
