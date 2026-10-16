/**
 * F 波（w2tf1）· 登录页 ?registered=1 提示只弹一次 —— 刷新不再重复弹「注册成功」。
 *
 * 背景：/register 未发 token 时 replace 到 /login?username=…&registered=1，登录页
 * onMounted/watch 读 registered 弹 toast。旧实现不剥离参数：用户每次刷新都会再弹一次
 * （跨会话实机两跑一致复现：URL 保留 registered=1、二次加载再弹一次）。
 * 修法：弹毕 router.replace 剥离 registered（保留 username 等其余参数，不新增历史条目）。
 *
 * 变异点：删掉 replace 剥离 → 「URL 已剥离」与「重挂（刷新）不再弹」两断言红。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory, type Router } from 'vue-router'
import { createI18n } from 'vue-i18n'
import { createPinia } from 'pinia'
import { NForm, NFormItem, NInput, NButton, NIcon } from 'naive-ui'
import zh from '@/locales/zh'

import LoginView from '../index.vue'

const messageSpies = vi.hoisted(() => ({
  success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn(), loading: vi.fn(),
}))
vi.mock('@/utils/message', () => ({ useDedupedMessage: () => messageSpies }))

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

async function mountLogin(router: Router) {
  const wrapper = mount(LoginView, {
    global: { plugins: [i18n, router, createPinia()], components: naiveComponents },
  })
  wrappers.push(wrapper)
  await flushPromises()
  await new Promise((resolve) => setTimeout(resolve, 20))
  await flushPromises()
  return wrapper
}

describe('登录页 ?registered=1 提示只弹一次', () => {
  let router: Router

  beforeEach(async () => {
    vi.clearAllMocks()
    router = makeRouter()
    await router.push('/login?registered=1&username=tester2')
    await router.isReady()
  })

  afterEach(() => {
    while (wrappers.length > 0) wrappers.pop()!.unmount()
    document.body.innerHTML = ''
  })

  it('首挂载弹一次，并把 registered 从 URL 剥离（保留 username，replace 不新增历史）', async () => {
    await mountLogin(router)

    expect(messageSpies.success).toHaveBeenCalledTimes(1)
    expect(String(messageSpies.success.mock.calls[0][0])).toContain('注册成功')
    expect(router.currentRoute.value.query.registered, 'registered 应已从 URL 剥离').toBeUndefined()
    expect(router.currentRoute.value.query.username, 'username 应保留').toBe('tester2')
  })

  it('刷新（同 URL 重新挂载）不再重复弹', async () => {
    const w1 = await mountLogin(router)
    await flushPromises()
    w1.unmount()
    wrappers.pop()

    // 模拟浏览器刷新：地址栏保持当前 URL，组件树重建
    await mountLogin(router)

    expect(messageSpies.success, '两次挂载合计只应弹一次').toHaveBeenCalledTimes(1)
  })
})
