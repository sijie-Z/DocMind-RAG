/**
 * E14/D1 判别用例：用户管理页错误如实 + 双弹收敛。
 *
 * 修前现场：
 * - 页内自建 getErrorMessage 直接返回 error.message —— axios 黑话
 *   「Request failed with status code 500」「Network Error」原样上屏；
 * - loadUsers 同时弹 toast + 内联 n-result，且请求未带 X-Silent-Error →
 *   全局响应拦截器再弹一条，同一失败最多三个提示表面；
 * - 写操作（建/改/删/重置密码）同样裸 error.message 且与全局 toast 双弹。
 *
 * 修复口径：统一 @/utils/apiError.getErrorMessage（5xx/传输层回退 i18n 文案，
 * 后端 4xx detail 原文优先）；页面自行呈现错误的请求一律带 X-Silent-Error，
 * 全局 toast 关闭；加载失败只保留内联错误态（含重试），写失败只保留一条 toast。
 *
 * 变异点：
 * - 还原本地 getErrorMessage（裸 error.message）→ 「无黑话」用例红；
 * - 请求不带 silent 头 → 「X-Silent-Error」用例红；
 * - 加载失败又弹 toast → 「单一表面」用例红；
 * - 写失败不弹/弹多条 → 「写失败一条」用例红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useUserStore } from '@/stores/user'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import naive, { NMessageProvider, NDialogProvider } from 'naive-ui'

import UsersView from '../index.vue'
import { getUsers, createUser, updateUser, deleteUser } from '@/api/user'
import { getOrganizations } from '@/api/organization'
import zh from '@/locales/zh'

const { messageSpies } = vi.hoisted(() => ({
  messageSpies: { error: vi.fn(), info: vi.fn(), success: vi.fn(), warning: vi.fn(), loading: vi.fn() },
}))

vi.mock('@/utils/message', () => ({ useDedupedMessage: () => messageSpies }))

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
  return {
    ...actual,
    getOrganizations: vi.fn().mockResolvedValue({ data: { success: true, data: { data: [], total: 0 } } }),
  }
})

const mockedGetUsers = vi.mocked(getUsers)
const mockedCreateUser = vi.mocked(createUser)
const mockedUpdateUser = vi.mocked(updateUser)
const mockedDeleteUser = vi.mocked(deleteUser)
const mockedGetOrganizations = vi.mocked(getOrganizations)

const ADMIN_ROW = {
  id: 1,
  username: 'admin',
  email: 'admin@example.com',
  full_name: 'Admin User',
  organization_id: null,
  role: 'admin',
  is_active: true,
  created_at: null,
  last_login_at: null,
}

function axiosError(status: number | undefined, msg: string, detail?: string) {
  return Object.assign(new Error(msg), {
    isAxiosError: true,
    config: {},
    ...(status ? { response: { status, data: detail ? { detail } : {} } } : {}),
  })
}

const SlotStub = (name: string) =>
  defineComponent({ name, setup(_, { slots }) { return () => h('div', slots.default?.()) } })

const i18n = createI18n({ legacy: false, locale: 'zh', missingWarn: false, fallbackWarn: false, messages: { zh } })

const Host = defineComponent({
  render: () => h(NDialogProvider, null, { default: () => h(NMessageProvider, null, { default: () => h(UsersView) }) }),
})

function mountView() {
  return mount(Host, {
    global: {
      plugins: [naive, i18n],
      // teleport 内联渲染，便于断言/操作弹窗内容
      stubs: { teleport: true, Tag: SlotStub('Tag') },
    },
  })
}

beforeEach(() => {
  // W0-1：删除用户按钮只对平台超管渲染；本文件覆盖删除失败路径，需要超管身份。
  setActivePinia(createPinia())
  useUserStore().userInfo = {
    id: 1,
    username: 'root',
    email: 'root@example.com',
    isAdmin: true,
    is_superuser: true,
    createdAt: '',
    organizations: [],
  } as never
  vi.clearAllMocks()
  mockedGetUsers.mockReset()
  mockedCreateUser.mockReset()
  mockedUpdateUser.mockReset()
  mockedDeleteUser.mockReset()
  mockedGetOrganizations.mockResolvedValue({ data: { success: true, data: { data: [], total: 0 } } } as never)
})

describe('UsersView · 加载失败如实且单一表面（E14/D1）', () => {
  it('500：不展示 axios 黑话与后端内部细节，内联错误态含 i18n 兜底文案；无 toast', async () => {
    mockedGetUsers.mockRejectedValue(
      axiosError(500, 'Request failed with status code 500', 'Traceback (most recent call last): /srv/app/internal/users.py'),
    )
    const wrapper = mountView()
    await flushPromises()

    const text = wrapper.text()
    expect(text).not.toContain('Request failed')
    expect(text).not.toContain('Status code')
    expect(text).not.toContain('/srv/app/internal')
    // 内联错误态：标题 + 兜底描述 + 重试
    expect(text).toContain('加载用户失败')
    expect(text).toContain('无法加载用户列表')
    expect(text).toContain('重试')
    // 单一表面：内联错误态已可见，不再弹 toast（全局拦截器由 silent 头关闭）
    expect(messageSpies.error).not.toHaveBeenCalled()
  })

  it('Network Error：传输层黑话不上屏，回退 i18n 文案', async () => {
    mockedGetUsers.mockRejectedValue(axiosError(undefined, 'Network Error'))
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).not.toContain('Network Error')
    expect(wrapper.text()).toContain('无法加载用户列表')
  })

  it('列表请求带 X-Silent-Error（全局拦截器不再二次弹提示）', async () => {
    mockedGetUsers.mockResolvedValue({ data: { total: 0, skip: 0, limit: 10, users: [] } } as never)
    mountView()
    await flushPromises()

    expect(mockedGetUsers).toHaveBeenCalledTimes(1)
    const [, config] = mockedGetUsers.mock.calls[0]
    expect((config as { headers?: Record<string, string> })?.headers?.['X-Silent-Error']).toBe('1')
  })
})

describe('UsersView · 写操作失败一条 toast（E14/D1）', () => {
  it('创建用户 400：后端 detail 原文呈现，只有一条 toast，且不出现 axios 黑话', async () => {
    mockedGetUsers.mockResolvedValue({ data: { total: 0, skip: 0, limit: 10, users: [] } } as never)
    mockedCreateUser.mockRejectedValue(
      axiosError(400, 'Request failed with status code 400', '用户名已存在'),
    )
    const wrapper = mountView()
    await flushPromises()

    // 打开「新建用户」弹窗
    const createBtn = wrapper.findAll('button').find((b) => b.text().includes('新建用户'))
    expect(createBtn, '新建用户按钮应存在').toBeTruthy()
    await createBtn!.trigger('click')
    await nextTick()
    await flushPromises()

    // 填表（真实 NInput → 原生 input）
    await wrapper.find('input[placeholder="请输入用户名"]').setValue('newuser')
    await wrapper.find('input[placeholder="请输入昵称"]').setValue('New User')
    await wrapper.find('input[placeholder="请输入邮箱"]').setValue('new@example.com')
    await wrapper.find('input[type="password"]').setValue('password1')

    const saveBtn = wrapper.findAll('button').find((b) => b.text().includes('保存') && !b.attributes('disabled'))
    expect(saveBtn, '保存按钮应存在').toBeTruthy()
    await saveBtn!.trigger('click')
    await flushPromises()
    await flushPromises()

    expect(mockedCreateUser).toHaveBeenCalledTimes(1)
    // 请求带 silent 头：错误提示只由页面这一条 toast 承担
    const [, config] = mockedCreateUser.mock.calls[0]
    expect((config as { headers?: Record<string, string> })?.headers?.['X-Silent-Error']).toBe('1')

    expect(messageSpies.error).toHaveBeenCalledTimes(1)
    const shown = String(messageSpies.error.mock.calls[0][0])
    expect(shown).toBe('用户名已存在')
    expect(shown).not.toContain('Request failed')
  })
})

describe('UsersView · 组织选项 / 校验 / 改删写路径（E14/D1 补充）', () => {
  it('组织选项加载失败：页面自己给出唯一提示（silent 收敛后不得静默留空）', async () => {
    mockedGetUsers.mockResolvedValue({ data: { total: 0, skip: 0, limit: 10, users: [] } } as never)
    mockedGetOrganizations.mockRejectedValueOnce(axiosError(500, 'Request failed with status code 500'))
    mountView()
    await flushPromises()

    expect(messageSpies.error).toHaveBeenCalledTimes(1)
    const shown = String(messageSpies.error.mock.calls[0][0])
    expect(shown).toBe('无法加载组织选项，请稍后重试')
    expect(shown).not.toContain('Request failed')
  })

  it('表单校验失败：不发请求、不弹 toast（字段级错误由表单呈现）', async () => {
    mockedGetUsers.mockResolvedValue({ data: { total: 0, skip: 0, limit: 10, users: [] } } as never)
    const wrapper = mountView()
    await flushPromises()

    const createBtn = wrapper.findAll('button').find((b) => b.text().includes('新建用户'))
    await createBtn!.trigger('click')
    await nextTick()
    await flushPromises()

    const saveBtn = wrapper.findAll('button').find((b) => b.text().includes('保存'))
    expect(saveBtn, '保存按钮应存在').toBeTruthy()
    await saveBtn!.trigger('click')
    await flushPromises()
    await flushPromises()

    expect(mockedCreateUser).not.toHaveBeenCalled()
    expect(messageSpies.error).not.toHaveBeenCalled()
  })

  it('编辑用户失败：updateUser 带 X-Silent-Error，且只有一条 detail 原文 toast', async () => {
    mockedGetUsers.mockResolvedValue({ data: { total: 1, skip: 0, limit: 10, users: [ADMIN_ROW] } } as never)
    mockedUpdateUser.mockRejectedValue(
      axiosError(400, 'Request failed with status code 400', '邮箱已被占用'),
    )
    const wrapper = mountView()
    await flushPromises()

    const editBtn = wrapper.findAll('button').find((b) => b.text().includes('编辑'))
    expect(editBtn, '编辑按钮应存在').toBeTruthy()
    await editBtn!.trigger('click')
    await nextTick()
    await flushPromises()

    const saveBtn = wrapper.findAll('button').find((b) => b.text().includes('保存'))
    await saveBtn!.trigger('click')
    await flushPromises()
    await flushPromises()

    expect(mockedUpdateUser).toHaveBeenCalledTimes(1)
    const [id, , config] = mockedUpdateUser.mock.calls[0]
    expect(id).toBe(1)
    expect((config as { headers?: Record<string, string> })?.headers?.['X-Silent-Error']).toBe('1')
    expect(messageSpies.error).toHaveBeenCalledTimes(1)
    expect(String(messageSpies.error.mock.calls[0][0])).toBe('邮箱已被占用')
  })

  it('删除用户失败：deleteUser 带 X-Silent-Error，且只有一条 i18n 兜底 toast', async () => {
    mockedGetUsers.mockResolvedValue({ data: { total: 1, skip: 0, limit: 10, users: [ADMIN_ROW] } } as never)
    mockedDeleteUser.mockRejectedValue(axiosError(500, 'Request failed with status code 500'))
    const wrapper = mountView()
    await flushPromises()

    const deleteBtn = wrapper.findAll('button').find((b) => b.text().includes('删除'))
    expect(deleteBtn, '删除按钮应存在').toBeTruthy()
    await deleteBtn!.trigger('click')
    await nextTick()
    await flushPromises()

    // 确认弹窗（NDialogProvider）正按钮
    const confirmBtn = wrapper.findAll('button').find((b) => b.text().includes('确认'))
    expect(confirmBtn, '确认按钮应存在').toBeTruthy()
    await confirmBtn!.trigger('click')
    await flushPromises()
    await flushPromises()

    expect(mockedDeleteUser).toHaveBeenCalledTimes(1)
    const [, config] = mockedDeleteUser.mock.calls[0]
    expect((config as { headers?: Record<string, string> })?.headers?.['X-Silent-Error']).toBe('1')
    expect(messageSpies.error).toHaveBeenCalledTimes(1)
    const shown = String(messageSpies.error.mock.calls[0][0])
    expect(shown).not.toContain('Request failed')
    expect(shown).toBe('操作失败')
  })
})
