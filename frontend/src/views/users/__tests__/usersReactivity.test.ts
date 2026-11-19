/**
 * 用户管理页：表头与角色下拉随语言切换实时更新。
 *
 * 背景：`views/users/index.vue` 的 `columns`（表头）与 `roleOptions`（角色
 * 下拉）原先在 setup 期一次性求值 `t()`，切语言后停留旧语言；同文件的
 * `formRules` 早已是 computed，口径不一致。
 *
 * 变异点：把 columns / roleOptions 退回普通数组常量 → 切 en 后表头与
 * 下拉仍显示中文，本用例红。
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
import en from '@/locales/en'

vi.mock('@/api/user', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/user')>()
  return {
    ...actual,
    getUsers: vi.fn(),
    createUser: vi.fn(),
    updateUser: vi.fn(),
    deleteUser: vi.fn(),
    resetUserPassword: vi.fn(),
    getOrganizations: vi.fn().mockResolvedValue({ data: { data: { data: [], total: 0 } } }),
  }
})

const mockedGetUsers = vi.mocked(getUsers)

const ADMIN = {
  id: 1, username: 'admin', email: 'admin@example.com', full_name: null,
  organization_id: null, role: 'admin', is_active: true,
  created_at: '2026-10-07T11:15:48', last_login_at: null,
}

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

const Host = defineComponent({
  render: () => h(NDialogProvider, null, { default: () => h(NMessageProvider, null, { default: () => h(UsersView) }) }),
})

async function mountAt(locale: 'zh' | 'en') {
  const i18n = createI18n({ legacy: false, locale, missingWarn: false, fallbackWarn: false, messages: { zh, en } })
  const w = mount(Host, {
    global: {
      plugins: [i18n],
      components: { NEmpty },
      stubs: { DataTable: DataTableStub, Tag: true, Button: true, Icon: true },
    },
  })
  await flushPromises()
  return { w, i18n }
}

beforeEach(() => {
  setActivePinia(createPinia())
  mockedGetUsers.mockReset()
  mockedGetUsers.mockResolvedValue({ data: { total: 1, skip: 0, limit: 100, users: [ADMIN] } } as never)
})

describe('用户管理页 · 表头/角色下拉随语言切换', () => {
  it('zh 挂载 → 表头为中文', async () => {
    const { w } = await mountAt('zh')
    const head = w.find('.dt-head').text()
    expect(head).toContain(zh.profile.username)
    expect(head).toContain(zh.profile.email)
    w.unmount()
  })

  it('挂载后切 en → 表头实时变英文（columns 必须是 computed）', async () => {
    const { w, i18n } = await mountAt('zh')
    const before = w.find('.dt-head').text()
    expect(before).toContain(zh.profile.username)

    ;(i18n.global.locale as unknown as { value: string }).value = 'en'
    await nextTick()
    await flushPromises()

    const after = w.find('.dt-head').text()
    expect(after, '表头应收敛到英文').toContain(en.profile.username)
    expect(after, '不应残留中文表头').not.toContain(zh.profile.username)
    w.unmount()
  })

  it('挂载后切 en → 角色下拉选项实时变英文（roleOptions 必须是 computed）', async () => {
    const { w, i18n } = await mountAt('zh')
    const vm = w.findComponent(UsersView).vm as unknown as {
      roleOptions: Array<{ label: string; value: string }>
    }
    expect(vm.roleOptions.map((o) => o.label)).toContain(zh.users.roleUser)

    ;(i18n.global.locale as unknown as { value: string }).value = 'en'
    await nextTick()

    const labels = (w.findComponent(UsersView).vm as unknown as {
      roleOptions: Array<{ label: string; value: string }>
    }).roleOptions.map((o) => o.label)
    expect(labels).toContain(en.users.roleUser)
    expect(labels).not.toContain(zh.users.roleUser)
    w.unmount()
  })
})
