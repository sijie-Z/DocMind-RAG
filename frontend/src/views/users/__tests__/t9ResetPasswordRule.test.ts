/**
 * T9 · 重置密码弹窗规则与后端口径对齐（最小长度 ≥8）。
 *
 * 后端口径：注册 ≥8（auth.py RegisterRequest.validate_password）、
 * 改密 min_length=8（ChangePasswordRequest）。前端用户管理「重置密码」
 * 弹窗曾为 min: 6 —— 管理员重置为 6 位密码可通过前端校验。
 *
 * 判别点：把 users/index.vue 的 resetPasswordRules.newPassword.min 从 8
 * 改回 6，本文件「6 位被拦下」断言即红（resetUserPassword 会被调用）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import { NMessageProvider, NDialogProvider, NEmpty } from 'naive-ui'

import UsersView from '../index.vue'
import { getUsers, resetUserPassword } from '@/api/user'
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
vi.mock('@/api/organization', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/organization')>()
  return { ...actual, getOrganizations: vi.fn() }
})

const mockedGetUsers = vi.mocked(getUsers)
const mockedGetOrganizations = vi.mocked(getOrganizations)
const mockedResetUserPassword = vi.mocked(resetUserPassword)

// 轻量表格桩：本用例不依赖表格行为，只需组件可挂载
const DataTableStub = defineComponent({
  name: 'DataTable',
  props: { data: { type: Array, default: () => [] } },
  setup() { return () => h('div', { class: 'dt-stub' }) },
})
const SlotStub = (name: string) =>
  defineComponent({ name, setup(_, { slots }) { return () => h('div', slots.default?.()) } })

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

const Host = defineComponent({
  render: () => h(NDialogProvider, null, { default: () => h(NMessageProvider, null, { default: () => h(UsersView) }) }),
})

const wrappers: ReturnType<typeof mount>[] = []

function mountView() {
  const wrapper = mount(Host, {
    global: {
      plugins: [i18n],
      components: { NEmpty },
      stubs: { DataTable: DataTableStub, Tag: SlotStub('Tag') },
    },
  })
  wrappers.push(wrapper)
  return wrapper
}

interface UsersViewVm {
  openResetPwd: (user: { id: number; username: string }) => void
  resetPassword: () => Promise<void>
  resetPasswordForm: { newPassword: string; confirmPassword: string }
}

async function openResetAndSubmit(password: string) {
  const wrapper = mountView()
  await flushPromises()
  const view = wrapper.findComponent(UsersView)
  const vm = view.vm as unknown as UsersViewVm
  vm.openResetPwd({ id: 2, username: 'bob' })
  await nextTick()
  await flushPromises()
  await nextTick()

  vm.resetPasswordForm.newPassword = password
  vm.resetPasswordForm.confirmPassword = password
  await nextTick()
  await vm.resetPassword()
  await flushPromises()
  await nextTick()
  return document.body.textContent || ''
}

beforeEach(() => {
  vi.clearAllMocks()
  mockedGetUsers.mockResolvedValue({ data: { total: 0, skip: 0, limit: 100, users: [] } } as never)
  mockedGetOrganizations.mockResolvedValue({ data: { data: [] } } as never)
})

afterEach(() => {
  while (wrappers.length > 0) wrappers.pop()!.unmount()
  document.body.innerHTML = ''
})

describe('重置密码弹窗最小长度与后端口径对齐（≥8）', () => {
  it('6 位新密码被前端拦下：不发起重置请求，并提示至少 8 位', async () => {
    const bodyText = await openResetAndSubmit('123456')

    expect(mockedResetUserPassword, '6 位密码不应通过前端校验').not.toHaveBeenCalled()
    expect(bodyText).toContain('密码长度至少为 8 位')
  })

  it('8 位新密码通过校验并正常提交（防过度收紧）', async () => {
    mockedResetUserPassword.mockResolvedValue({} as never)
    await openResetAndSubmit('12345678')

    expect(mockedResetUserPassword).toHaveBeenCalledTimes(1)
    // E14/D1：第三个实参为 silent 请求配置（X-Silent-Error）
    expect(mockedResetUserPassword).toHaveBeenCalledWith(
      2,
      '12345678',
      expect.objectContaining({ headers: { 'X-Silent-Error': '1' } }),
    )
  })
})
