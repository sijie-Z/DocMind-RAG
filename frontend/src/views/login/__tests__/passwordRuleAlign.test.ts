/**
 * T9 · 密码规则前后端对齐 —— 注册/登录表单最小长度 6→8。
 *
 * 后端口径（auth.py RegisterRequest.validate_password / ChangePasswordRequest）：
 * 密码不少于 8 位。前端登录页规则曾为 min: 6，注册 6 位密码可绕过前端校验。
 *
 * 判别点：把 login/index.vue 密码规则的 min 从 8 改回 6（或 i18n 文案改回
 * 「至少为 6 位」），本文件「6 位被拦截」与「提示 ≥8」断言即红。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory, type Router } from 'vue-router'
import { createI18n } from 'vue-i18n'
import { NForm, NFormItem, NInput, NButton, NIcon } from 'naive-ui'
import zh from '@/locales/zh'

import LoginView from '../index.vue'

const messageSpies = vi.hoisted(() => ({
  success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn(), loading: vi.fn(),
}))
vi.mock('@/utils/message', () => ({ useDedupedMessage: () => messageSpies }))

const storeSpies = vi.hoisted(() => ({
  login: vi.fn(),
  register: vi.fn(),
}))
vi.mock('@/stores/user', () => ({ useUserStore: () => storeSpies }))

const i18n = createI18n({ legacy: false, locale: 'zh', fallbackLocale: 'zh', messages: { zh } })
const naiveComponents = { NForm, NFormItem, NInput, NButton, NIcon }

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/login', name: 'Login', component: { template: '<div />' } },
      { path: '/register', name: 'Register', component: { template: '<div />' } },
    ],
  })
}

const wrappers: ReturnType<typeof mount>[] = []

async function mountAt(path: string) {
  const router = makeRouter()
  await router.push(path)
  await router.isReady()
  const wrapper = mount(LoginView, {
    global: { plugins: [i18n, router], components: naiveComponents },
  })
  wrappers.push(wrapper)
  await flushPromises()
  return { wrapper, router }
}

async function fillInputs(wrapper: ReturnType<typeof mount>, username: string, email: string, password: string) {
  const textInputs = wrapper.findAll('input[type="text"]')
  if (textInputs.length > 0) await textInputs[0].setValue(username)
  if (textInputs.length > 1) await textInputs[1].setValue(email || 'new@example.com')
  await wrapper.find('input[type="password"]').setValue(password)
}

async function submit(wrapper: ReturnType<typeof mount>) {
  await wrapper.find('form').trigger('submit')
  await flushPromises()
  await new Promise((resolve) => setTimeout(resolve, 20))
  await flushPromises()
}

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  while (wrappers.length > 0) wrappers.pop()!.unmount()
  document.body.innerHTML = ''
})

describe('注册表单密码最小长度与后端口径对齐（≥8）', () => {
  it('6 位密码被前端拦下：不发起注册调用，并提示至少 8 位', async () => {
    const { wrapper } = await mountAt('/register')
    await fillInputs(wrapper, 'newuser', 'new@example.com', '123456')
    await submit(wrapper)

    expect(storeSpies.register, '6 位密码不应通过前端校验').not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('密码长度至少为 8 位')
  })

  it('8 位密码通过前端校验并正常提交（防过度收紧）', async () => {
    storeSpies.register.mockResolvedValue({ success: false, message: '用户名已存在' })
    const { wrapper } = await mountAt('/register')
    await fillInputs(wrapper, 'newuser', 'new@example.com', '12345678')
    await submit(wrapper)

    expect(storeSpies.register).toHaveBeenCalledTimes(1)
    expect(storeSpies.register).toHaveBeenCalledWith({
      username: 'newuser',
      email: 'new@example.com',
      password: '12345678',
    })
    // 校验通过，不应出现长度错误提示
    expect(wrapper.text()).not.toContain('密码长度至少为 8 位')
  })
})

describe('登录表单只校验非空，不套用注册长度规则', () => {
  it('6 位密码可以发起登录请求，不被前端拦截', async () => {
    storeSpies.login.mockResolvedValue({ success: false, message: '用户名或密码错误' })
    const { wrapper } = await mountAt('/login')
    await fillInputs(wrapper, 'admin', '', '123456')
    await submit(wrapper)

    expect(storeSpies.login).toHaveBeenCalledWith({ username: 'admin', password: '123456' })
    expect(wrapper.text()).not.toContain('密码长度至少为 8 位')
  })

  it('2 位用户名可以发起登录请求，不被前端拦截', async () => {
    storeSpies.login.mockResolvedValue({ success: false, message: '用户名或密码错误' })
    const { wrapper } = await mountAt('/login')
    await fillInputs(wrapper, 'ab', '', '12345678')
    await submit(wrapper)

    expect(storeSpies.login).toHaveBeenCalledWith({ username: 'ab', password: '12345678' })
    expect(wrapper.text()).not.toContain('用户名至少3位')
  })
})
