/**
 * T8 审计页「筛选项恒空 / 翻页失效」判别用例（15f681b 实测复核）。
 *
 * 复核证据：下拉恒 5 项硬编码，其中「删除操作」发 action=delete → 后端 exact
 * match 恒 0（库内真串 delete_document ×9）；pager 恒单页（itemCount 被非 remote
 * 表无视），点 next 零请求且表格清空（_uisweep/logs/w2t8_recheck_before*.json）。
 *
 * 修法判别：
 * ① 选项改由 GET /users/audit-logs/actions 动态生成（真实值 + 计数），
 *    不再出现库内不存在的 value（如 'delete'/'upload_document'）；
 * ② 表格加 remote，pagination.onChange / onUpdatePageSize 回写页码后请求。
 * 变异点：删 getAuditActions 接线 → 用例 1 红；删 remote/onChange → 用例 2 红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createI18n } from 'vue-i18n'
import { NMessageProvider } from 'naive-ui'

import AuditView from '../index.vue'
import { getAuditLogs } from '@/api/user'
import { getAuditActions } from '@/api/audit'
import zh from '@/locales/zh'

vi.mock('@/api/user', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/user')>()
  return { ...actual, getAuditLogs: vi.fn() }
})
vi.mock('@/api/audit', () => ({ downloadAuditLogs: vi.fn(), getAuditActions: vi.fn() }))

const mockedGetAuditLogs = vi.mocked(getAuditLogs)
const mockedGetAuditActions = vi.mocked(getAuditActions)

const ITEM = {
  id: 1,
  user_id: 3,
  username: 'tester2',
  action: 'delete_document',
  target_type: 'document',
  target_id: null,
  detail: '删除知识库文档',
  ip_address: '127.0.0.1',
  created_at: '2026-10-14T12:00:00',
}

const DataTableStub = defineComponent({
  name: 'DataTable',
  props: {
    data: { type: Array, default: () => [] },
    columns: { type: Array, default: () => [] },
    pagination: { type: Object, default: () => ({}) },
    remote: { type: Boolean, default: false },
  },
  setup(props) {
    return () => h('div', { class: 'dt-stub', 'data-remote': String(props.remote) })
  },
})

const SelectStub = defineComponent({
  // eslint-disable-next-line vue/no-reserved-component-names -- 必须与 naive-ui NSelect 的组件 name（'Select'）一致，VTU 才能以桩替换真实组件
  name: 'Select',
  props: {
    options: { type: Array, default: () => [] },
    value: { type: [String, Number, Array], default: null },
    placeholder: { type: String, default: '' },
  },
  setup(props) {
    return () => h('div', { class: 'sel-stub', 'data-ph': props.placeholder }, JSON.stringify(props.options))
  },
})

const SlotStub = (name: string) =>
  defineComponent({ name, setup(_, { slots }) { return () => h('div', slots.default?.()) } })

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

const Host = defineComponent({
  render: () => h(NMessageProvider, null, { default: () => h(AuditView) }),
})

function mountView() {
  return mount(Host, {
    global: {
      plugins: [i18n],
      components: { NDataTable: DataTableStub, NSelect: SelectStub },
      stubs: {
        Tag: SlotStub('Tag'),
        Button: true,
        Icon: true,
        Input: true,
        DatePicker: true,
      },
    },
  })
}

beforeEach(() => {
  mockedGetAuditLogs.mockReset()
  mockedGetAuditActions.mockReset()
})

describe('AuditView · T8 筛选项数据源 + 翻页', () => {
  it('操作类型选项来自 /audit-logs/actions：含真实值 delete_document，无消失值 delete/upload_document', async () => {
    mockedGetAuditLogs.mockResolvedValue({
      data: { success: true, data: { items: [ITEM], total: 41 } },
    } as never)
    mockedGetAuditActions.mockResolvedValue({
      data: {
        success: true,
        data: [
          { action: 'login', count: 28 },
          { action: 'delete_document', count: 9 },
          { action: 'profile_updated', count: 4 },
        ],
      },
    } as never)

    const wrapper = mountView()
    await flushPromises()

    expect(mockedGetAuditActions).toHaveBeenCalledTimes(1)
    const sel = wrapper.find('.sel-stub')
    const options = JSON.parse(sel.text()) as Array<{ label: string; value: string }>
    const values = options.map((o) => o.value)
    expect(values).toContain('delete_document')
    expect(values).toContain('login')
    // 库内不存在的旧硬编码值必须不再出现
    expect(values).not.toContain('delete')
    expect(values).not.toContain('upload_document')
    expect(values).not.toContain('update_role')
    // 已知动作映射中文标签并带计数；未知动作回退原始串（诚实）
    const labelOf = (v: string) => options.find((o) => o.value === v)?.label ?? ''
    expect(labelOf('delete_document')).toContain('删除操作')
    expect(labelOf('delete_document')).toContain('9')
    expect(labelOf('profile_updated')).toContain('profile_updated')
  })

  it('翻页：remote 表 + pagination.onChange 回写页码后按 skip 请求（旧版零请求且表清空）', async () => {
    mockedGetAuditLogs.mockResolvedValue({
      data: { success: true, data: { items: [ITEM], total: 41 } },
    } as never)
    mockedGetAuditActions.mockResolvedValue({ data: { success: true, data: [] } } as never)

    const wrapper = mountView()
    await flushPromises()

    const dt = wrapper.findComponent(DataTableStub)
    expect(dt.props('remote')).toBe(true)
    expect(mockedGetAuditLogs).toHaveBeenCalledWith(
      expect.objectContaining({ skip: 0, limit: 20 })
    )

    const pagination = dt.props('pagination') as {
      onChange: (p: number) => void
      onUpdatePageSize: (s: number) => void
      itemCount: number
    }
    expect(pagination.itemCount).toBe(41)

    pagination.onChange(2)
    await flushPromises()
    expect(mockedGetAuditLogs).toHaveBeenLastCalledWith(
      expect.objectContaining({ skip: 20, limit: 20 })
    )

    pagination.onUpdatePageSize(50)
    await flushPromises()
    expect(mockedGetAuditLogs).toHaveBeenLastCalledWith(
      expect.objectContaining({ skip: 0, limit: 50 })
    )
  })
})
