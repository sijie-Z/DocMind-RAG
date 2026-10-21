/**
 * T13 · 登录页法务链接 —— 隐私/服务条款从纯文本改为可点击真链接。
 *
 * 修复前：左栏底部 `&copy; DocMind · 隐私 · 服务条款` 是纯文本，用户无从点开。
 * 修复后：两处均渲染为带 href 的 <a>，文案走 login.privacy / login.terms 词条。
 *
 * 判别点：把 <a> 改回纯文本（或删掉 href）→ 本文件「真链接」断言红。
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory, type Router } from 'vue-router'
import { createI18n } from 'vue-i18n'
import { NForm, NFormItem, NInput, NButton, NIcon } from 'naive-ui'
import zh from '@/locales/zh'

import LoginView from '../index.vue'

vi.mock('@/utils/message', () => ({
  useDedupedMessage: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}))
vi.mock('@/stores/user', () => ({ useUserStore: () => ({ login: vi.fn(), register: vi.fn() }) }))

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

afterEach(() => {
  while (wrappers.length > 0) wrappers.pop()!.unmount()
})

describe('登录页法务链接（隐私 · 服务条款）', () => {
  it('隐私与服务条款渲染为带 href 的真链接（占位路由 /privacy、/terms）', async () => {
    const router = makeRouter()
    await router.push('/login')
    await router.isReady()
    const wrapper = mount(LoginView, {
      global: { plugins: [i18n, router], components: naiveComponents },
    })
    wrappers.push(wrapper)
    await flushPromises()

    const anchors = wrapper.findAll('a')
    const hrefs = anchors.map((a) => a.attributes('href'))
    expect(hrefs, '隐私应为可点击真链接').toContain('/privacy')
    expect(hrefs, '服务条款应为可点击真链接').toContain('/terms')

    // 文案来自词条（zh），而非裸文本游离于链接之外
    const text = wrapper.text()
    expect(text).toContain('隐私')
    expect(text).toContain('服务条款')
    const privacy = anchors.find((a) => a.attributes('href') === '/privacy')
    const terms = anchors.find((a) => a.attributes('href') === '/terms')
    expect(privacy?.text()).toBe('隐私')
    expect(terms?.text()).toBe('服务条款')
  })
})
