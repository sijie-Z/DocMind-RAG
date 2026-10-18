/**
 * J3 · 登录页品牌区标题与表单标题去重（双「欢迎回来」）。
 *
 * 现场（截图 r5q-auth）：桌面端左品牌区 h1 硬编码 '欢迎回来' / '加入 DocMind'，
 * 与右侧表单 h2 t('login.welcomeBack') 构成同名双标题；切英文后左栏仍是中文。
 * 修法：品牌区标题/副文案改用 login.brandHeadline / login.brandSubtitle 词条，
 * 不再复述表单标题。
 *
 * 变异点：品牌区标题改回复述 '欢迎回来' → 中文用例（出现次数 2）与英文用例
 * （残留中文）双红。
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory, type Router } from 'vue-router'
import { createI18n } from 'vue-i18n'
import { createPinia } from 'pinia'
import { NForm, NFormItem, NInput, NButton, NIcon } from 'naive-ui'
import zh from '@/locales/zh'
import en from '@/locales/en'

import LoginView from '../index.vue'

vi.mock('@/utils/message', () => ({
  useDedupedMessage: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn(), loading: vi.fn() }),
}))

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

async function mountLogin(locale: 'zh' | 'en') {
  const router = makeRouter()
  await router.push('/login')
  await router.isReady()
  const i18nInstance = createI18n({
    legacy: false,
    locale,
    fallbackLocale: 'zh',
    missingWarn: false,
    fallbackWarn: false,
    messages: { zh, en },
  })
  const wrapper = mount(LoginView, {
    global: { plugins: [i18nInstance, router, createPinia()], components: naiveComponents },
  })
  wrappers.push(wrapper)
  await flushPromises()
  return wrapper
}

const occurrences = (haystack: string, needle: string) => haystack.split(needle).length - 1

afterEach(() => {
  while (wrappers.length > 0) wrappers.pop()!.unmount()
  document.body.innerHTML = ''
})

describe('J3 · 登录页双标题清理（品牌区不复述表单标题）', () => {
  it('中文态：「欢迎回来」只出现一次（表单标题），品牌区使用独立标语', async () => {
    const wrapper = await mountLogin('zh')
    const text = wrapper.text()

    expect(occurrences(text, '欢迎回来'), '「欢迎回来」不应在品牌区与表单标题重复出现').toBe(1)
    expect(wrapper.find('h1').text()).toBe(zh.login.brandHeadline)
    expect(text).toContain(zh.login.brandSubtitle)
  })

  it('英文态：无中文残留，「Welcome Back」只出现一次', async () => {
    const wrapper = await mountLogin('en')
    const text = wrapper.text()

    expect(text).not.toContain('欢迎回来')
    expect(occurrences(text, 'Welcome Back')).toBe(1)
    expect(wrapper.find('h1').text()).toBe(en.login.brandHeadline)
  })
})
