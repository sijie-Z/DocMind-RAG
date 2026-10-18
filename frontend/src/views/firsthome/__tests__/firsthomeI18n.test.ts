/**
 * T13 · 首屏落地页（FirstHome）整页 i18n —— 文案不再硬编码中文。
 *
 * 修复前：模板/脚本内联中文（导航、Hero、三步、六特性、终端演示、CTA、页脚），
 * 切到 en/ja/fr 后整页仍是中文。修复后：全部文案走 firsthome.* 词条（四语齐）。
 *
 * 判别点：任一区块退回内联中文 → en 相位「整页无 CJK」断言红并可见残余中文。
 */
import { describe, it, expect, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createRouter, createMemoryHistory, type Router } from 'vue-router'
import { createI18n } from 'vue-i18n'
import { createPinia } from 'pinia'
import zh from '@/locales/zh'
import en from '@/locales/en'
import ja from '@/locales/ja'
import fr from '@/locales/fr'

import FirstHome from '../index.vue'

const CJK = /[㐀-䶿一-鿿]/

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/firsthome', name: 'FirstHome', component: { template: '<div />' } },
      { path: '/login', name: 'Login', component: { template: '<div />' } },
      { path: '/register', name: 'Register', component: { template: '<div />' } },
      { path: '/dashboard', name: 'Dashboard', component: { template: '<div />' } },
    ],
  })
}

async function mountAt(locale: 'zh' | 'en' | 'ja' | 'fr') {
  const i18n = createI18n({ legacy: false, locale, fallbackLocale: 'zh', messages: { zh, en, ja, fr } })
  const router = makeRouter()
  await router.push('/firsthome')
  await router.isReady()
  const pinia = createPinia()
  const wrapper = mount(FirstHome, { global: { plugins: [i18n, router, pinia] } })
  return wrapper
}

const wrappers: ReturnType<typeof mount>[] = []

afterEach(() => {
  while (wrappers.length > 0) wrappers.pop()!.unmount()
  localStorage.clear()
})

describe('FirstHome 整页中文化纳入 i18n（四语）', () => {
  it('zh 相位渲染既有中文文案（回归锁）', async () => {
    const wrapper = await mountAt('zh')
    wrappers.push(wrapper)
    const text = wrapper.text()
    expect(text).toContain('让每一份文档')
    expect(text).toContain('三步构建你的知识库')
    expect(text).toContain('免费开始')
  })

  it('en 相位不冒中文 —— 整页不再含 CJK 字符', async () => {
    const wrapper = await mountAt('en')
    wrappers.push(wrapper)
    const text = wrapper.text()
    expect(text, `en 相位仍残留硬编码中文：${text.match(CJK)?.input?.slice(0, 40)}`).not.toMatch(CJK)
    expect(text).toContain('Build your knowledge base in three steps')
    expect(text).toContain('Every document')
  })

  it('ja / fr 相位各自渲染本语词条（不回退中文）', async () => {
    const jaWrapper = await mountAt('ja')
    wrappers.push(jaWrapper)
    // 日文本身含汉字，不能以 CJK 字符面判红；以「中文原文不得出现」为判别
    expect(jaWrapper.text()).not.toContain('三步构建你的知识库')
    expect(jaWrapper.text()).toContain('3 ステップでナレッジベースを構築')

    const frWrapper = await mountAt('fr')
    wrappers.push(frWrapper)
    expect(frWrapper.text()).not.toMatch(CJK)
    expect(frWrapper.text()).toContain('Construisez votre base de connaissances en trois étapes')
  })
})
