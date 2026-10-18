/**
 * R1 · 法务占位页可达性 —— /privacy、/terms 必须命中真路由并渲染页面。
 *
 * 修复前：登录页法务链接已是 <a href="/privacy|/terms">（legalLinks.test.ts 只断言
 * href 存在），但路由表没有对应记录，点击落进 catch-all 404 —— 链接可点但不可达。
 *
 * 判别点：删除任一法务路由记录（或改路径）→ hasRoute/resolve 与渲染断言红。
 */
import { describe, it, expect, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createRouter, createMemoryHistory, RouterView, type Router } from 'vue-router'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import { routes } from '@/router'
import zh from '@/locales/zh'

const i18n = createI18n({ legacy: false, locale: 'zh', fallbackLocale: 'zh', messages: { zh } })

const wrappers: ReturnType<typeof mount>[] = []
afterEach(() => {
  while (wrappers.length > 0) wrappers.pop()!.unmount()
})

function makeRouter(): Router {
  return createRouter({ history: createMemoryHistory(), routes })
}

const Host = defineComponent({ render: () => h(RouterView) })

async function mountAt(path: string) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = makeRouter()
  await router.push(path)
  await router.isReady()
  const wrapper = mount(Host, { global: { plugins: [pinia, router, i18n] } })
  wrappers.push(wrapper)
  await flushPromises()
  return { router, wrapper }
}

describe('法务链接端到端可达性（/privacy、/terms）', () => {
  it('路由表注册 Privacy / Terms，路径可解析且匿名可访问', () => {
    const router = makeRouter()
    expect(router.hasRoute('Privacy'), '缺少 Privacy 路由').toBe(true)
    expect(router.hasRoute('Terms'), '缺少 Terms 路由').toBe(true)

    const cases = [
      ['/privacy', 'Privacy'],
      ['/terms', 'Terms'],
    ] as const
    for (const [path, name] of cases) {
      const resolved = router.resolve(path)
      expect(resolved.name, `${path} 未解析到 ${name}`).toBe(name)
      expect(resolved.matched.length, `${path} 没有匹配记录`).toBeGreaterThan(0)
      expect(resolved.meta.requiresAuth, `${path} 应匿名可访问`).toBe(false)
    }
  })

  it('/privacy 渲染隐私占位页而非 404', async () => {
    const { router, wrapper } = await mountAt('/privacy')
    expect(router.currentRoute.value.name).toBe('Privacy')
    expect(wrapper.text()).toContain('隐私政策')
    expect(wrapper.text()).toContain('占位')
    // 未注册路由会渲染 404 页；其标题不应出现
    expect(wrapper.find('[data-testid="not-found-page"]').exists()).toBe(false)
  })

  it('/terms 渲染服务条款占位页而非 404', async () => {
    const { router, wrapper } = await mountAt('/terms')
    expect(router.currentRoute.value.name).toBe('Terms')
    expect(wrapper.text()).toContain('服务条款')
    expect(wrapper.text()).toContain('占位')
    expect(wrapper.find('[data-testid="not-found-page"]').exists()).toBe(false)
  })
})
