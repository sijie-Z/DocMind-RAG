/**
 * 操作审计页与管理员仪表盘：表头随语言切换实时更新。
 *
 * 背景：两页的 DataTable 列定义（audit 的 `columns`、dashboard 的
 * `auditColumns`）在 setup 期一次性求值 `t()`，页内其它文案走模板实时
 * 更新，切语言后表头停留旧语言（中英混杂）。
 *
 * 变异点：把任一列定义退回普通数组常量 → 切 en 后表头仍显示中文，
 * 对应用例红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import { NMessageProvider } from 'naive-ui'
import { createRouter, createMemoryHistory } from 'vue-router'

import AuditView from '../index.vue'
import zh from '@/locales/zh'
import en from '@/locales/en'

vi.mock('@/api/user', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/user')>()
  return { ...actual, getAuditLogs: vi.fn().mockResolvedValue({ data: { success: true, data: { items: [], total: 0 } } }) }
})
vi.mock('@/api/audit', () => ({ downloadAuditLogs: vi.fn() }))

const DataTableStub = defineComponent({
  name: 'DataTable',
  props: {
    data: { type: Array, default: () => [] },
    columns: { type: Array, default: () => [] },
  },
  setup(props) {
    return () =>
      h('div', { class: 'dt-stub' }, [
        h('div', { class: 'dt-head' },
          (props.columns as Array<Record<string, unknown>>).map((col, ci) =>
            h('span', { class: 'dt-hcell', key: ci }, typeof col?.title === 'string' ? (col.title as string) : '')
          )
        ),
      ])
  },
})

const SlotStub = (name: string) =>
  defineComponent({ name, setup(_, { slots }) { return () => h('div', slots.default?.()) } })

const Host = defineComponent({
  render: () => h(NMessageProvider, null, { default: () => h(AuditView) }),
})

async function mountAt(locale: 'zh' | 'en') {
  const i18n = createI18n({ legacy: false, locale, missingWarn: false, fallbackWarn: false, messages: { zh, en } })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: defineComponent({ template: '<div />' }) }],
  })
  await router.push('/')
  await router.isReady()
  const pinia = createPinia()
  setActivePinia(pinia)
  const w = mount(Host, {
    global: {
      plugins: [i18n, router, pinia],
      components: { NDataTable: DataTableStub },
      stubs: { Tag: SlotStub('Tag'), Button: true, Icon: true, Input: true, Select: true, DatePicker: true, Pagination: true },
    },
  })
  await flushPromises()
  return { w, i18n }
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('操作审计页 · 表头随语言切换', () => {
  it('zh 挂载 → 中文表头', async () => {
    const { w } = await mountAt('zh')
    const head = w.find('.dt-head').text()
    expect(head).toContain(zh.audit.columns.time)
    expect(head).toContain(zh.audit.columns.user)
    w.unmount()
  })

  it('挂载后切 en → 表头实时变英文（columns 必须是 computed）', async () => {
    const { w, i18n } = await mountAt('zh')
    expect(w.find('.dt-head').text()).toContain(zh.audit.columns.time)

    ;(i18n.global.locale as unknown as { value: string }).value = 'en'
    await nextTick()
    await flushPromises()

    const head = w.find('.dt-head').text()
    expect(head, '表头应收敛到英文').toContain(en.audit.columns.time)
    expect(head, '不应残留中文表头').not.toContain(zh.audit.columns.time)
    w.unmount()
  })
})
