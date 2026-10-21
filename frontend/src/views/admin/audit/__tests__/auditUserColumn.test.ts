/**
 * 操作审计页「用户」列判别用例（UI 验收 round2 · A_sweep A3）。
 *
 * 真 UI 现场：/audit-logs「用户」列绑定 key:"user_id" → 直出数字 1/3；
 * 而同一 API 响应里每条都带 username（users.py:328
 * `"username": log.user.username if log.user else "未知"`）—— 本页漏用。
 *
 * 变异点：把列改回 key:'user_id'（无 render）→ 第 1 条用例精确红
 * （单元格出 "3" 而非 "tester"）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createI18n } from 'vue-i18n'
import { NMessageProvider } from 'naive-ui'

import AuditView from '../index.vue'
import { getAuditLogs } from '@/api/user'
import zh from '@/locales/zh'

vi.mock('@/api/user', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/user')>()
  return { ...actual, getAuditLogs: vi.fn() }
})
vi.mock('@/api/audit', () => ({ downloadAuditLogs: vi.fn() }))

const mockedGetAuditLogs = vi.mocked(getAuditLogs)

// 真实响应形状（users.py:322-335）：{success, data:{items, total}}
const ITEM_WITH_USERNAME = {
  id: 1,
  user_id: 3,
  username: 'tester',
  action: 'login',
  target_type: 'auth',
  target_id: null,
  detail: '用户登录成功',
  ip_address: '127.0.0.1',
  created_at: '2026-10-10T04:44:58',
}

// username 缺失时的防御（用户被删等边界）
const ITEM_WITHOUT_USERNAME = {
  ...ITEM_WITH_USERNAME,
  id: 2,
  user_id: 42,
  username: undefined,
}

const DataTableStub = defineComponent({
  name: 'DataTable',
  props: {
    data: { type: Array, default: () => [] },
    columns: { type: Array, default: () => [] },
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
            (props.columns as Array<Record<string, unknown>>).map((col) => {
              if (!col) return h('span', { class: 'dt-cell' }, '')
              const cell = typeof col.render === 'function' ? (col.render as (r: unknown) => unknown)(row) : (row[col.key as string] ?? '')
              return h('span', { class: 'dt-cell' }, [cell as never])
            })
          )
        )
      )
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
      // n-data-table 在生产由 app.use(naive) 全局注册；测试里注册轻量桩
      components: { NDataTable: DataTableStub },
      stubs: {
        Tag: SlotStub('Tag'),
        Button: true,
        Icon: true,
        Input: true,
        Select: true,
        DatePicker: true,
      },
    },
  })
}

beforeEach(() => {
  mockedGetAuditLogs.mockReset()
})

describe('AuditView · 用户列（username 而非 user_id）', () => {
  it('真实响应含 username → 用户列出 "tester"（不再直出数字 user_id）', async () => {
    mockedGetAuditLogs.mockResolvedValue({
      data: { success: true, data: { items: [ITEM_WITH_USERNAME], total: 1 } },
    } as never)

    const wrapper = mountView()
    await flushPromises()

    const cells = wrapper.findAll('.dt-cell')
    // 列序：时间 | 用户 | 动作 | 目标 | 详情 | IP —— 第 2 格是用户列
    const userCell = cells[1].text()
    expect(userCell).toBe('tester')
    expect(userCell).not.toBe('3')
  })

  it('防御：username 缺失 → 回退显示 user_id，不出现 undefined', async () => {
    mockedGetAuditLogs.mockResolvedValue({
      data: { success: true, data: { items: [ITEM_WITHOUT_USERNAME], total: 1 } },
    } as never)

    const wrapper = mountView()
    await flushPromises()

    const userCell = wrapper.findAll('.dt-cell')[1].text()
    expect(userCell).toBe('42')
    expect(userCell).not.toContain('undefined')
  })
})
