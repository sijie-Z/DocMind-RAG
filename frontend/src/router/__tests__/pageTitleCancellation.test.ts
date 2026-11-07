import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import { applyRoutePageTitle } from '../pageTitle'
import { setPageTitleText } from '@/locales'

const Stub = { template: '<div />' }

describe('路由标题决策', () => {
  it('被取消的导航不得覆盖已落地页面的标题', async () => {
    let release!: () => void
    const gate = new Promise<void>((resolve) => { release = resolve })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/home', name: 'Home', component: Stub, meta: { title: 'router.welcome' } },
        { path: '/a', name: 'A', component: Stub, meta: { title: 'router.welcome' } },
        { path: '/b', name: 'B', component: Stub, meta: { title: 'router.login' } }
      ]
    })
    router.beforeEach(async (to) => {
      if (to.name === 'A') await gate
    })
    router.afterEach((to, from, failure) => applyRoutePageTitle(to, from, failure))

    await router.push('/home')
    const slowA = router.push('/a')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await router.push('/b')
    expect(document.title).toContain('登录')

    // 放行被取消的 A：其 afterEach 携带 failure，必须跳过标题写入。
    release()
    await slowA
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(document.title).toContain('登录')
    expect(document.title).not.toContain('欢迎')
  })

  it('同页面仅 query 变化保留页面级标题', () => {
    setPageTitleText('文档A')
    const route = { name: 'Doc', params: { id: '1' }, meta: { title: 'router.login' } }
    applyRoutePageTitle(route, route)
    expect(document.title).toContain('文档A')
  })
})


describe('路由接线', () => {
  it('afterEach 必须把 failure 传给标题决策', () => {
    const source = readFileSync(resolve(process.cwd(), 'src/router/index.ts'), 'utf-8')
    expect(source).toContain('applyRoutePageTitle(to, from, failure)')
  })
})
