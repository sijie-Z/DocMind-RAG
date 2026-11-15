/**
 * 知识库「重新解析」按 RBAC 显隐（P1-1）。
 *
 * 后端 `POST /knowledge/rebuild/{id}` 要求 `knowledge_base:rebuild_index`
 * 权限（无权限 403），此前按钮对所有用户可见——点了才报错。
 * 修复：/users/me 与登录 user_info 下发 `permissions: string[]`，知识库页按
 * `showRebuild = can('knowledge_base:rebuild_index') !== false` 显隐；
 * permissions 字段缺失（旧后端）→ 未知，保持可见（诚实降级，不误伤）。
 *
 * 变异点：
 *  - 删 store.can / permissions 透传 → 无权限用例红（按钮又可见了）；
 *  - 把 showRebuild 改成恒 true → 无权限用例红；
 *  - 把降级逻辑改成「缺失即隐藏」→ 旧后端用例红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import naive, { NMessageProvider, NDialogProvider } from 'naive-ui'

import KnowledgeView from '../index.vue'
import { useUserStore } from '@/stores/user'
import zh from '@/locales/zh'

const apiMocks = vi.hoisted(() => ({
  getKnowledgeBases: vi.fn(),
  rebuildKnowledgeBase: vi.fn(),
  getRagMetrics: vi.fn(),
}))
vi.mock('@/api/rag', () => ({
  getRagMetrics: apiMocks.getRagMetrics,
}))
vi.mock('@/api/knowledge', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/knowledge')>()
  return {
    ...actual,
    getKnowledgeBases: apiMocks.getKnowledgeBases,
    rebuildKnowledgeBase: apiMocks.rebuildKnowledgeBase,
    getRagMetrics: apiMocks.getRagMetrics,
  }
})

const KB_FIXTURE = {
  success: true,
  data: {
    data: [
      {
        id: 'doc-1',
        title: '失败文档',
        file_name: 'a.pdf',
        status: 'failed',
        file_type: 'pdf',
        tags: [],
        file_size: 100,
        created_at: '2026-11-01T00:00:00',
        updated_at: '2026-11-01T00:00:00',
      },
    ],
    total: 1,
    page: 1,
    page_size: 20,
  },
}

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

function makeRouter() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/knowledge', name: 'Knowledge', component: { template: '<div />' } },
      { path: '/knowledge/:documentId', name: 'KnowledgeDocument', component: { template: '<div />' } },
      { path: '/dashboard', name: 'Dashboard', component: { template: '<div />' } },
    ],
  })
  void router.push('/knowledge')
  return router
}

async function mountView() {
  const router = makeRouter()
  await router.isReady()
  const Host = defineComponent({
    render: () => h(NDialogProvider, null, { default: () => h(NMessageProvider, null, { default: () => h(KnowledgeView) }) }),
  })
  const w = mount(Host, { global: { plugins: [naive, i18n, router] } })
  await flushPromises()
  await flushPromises()
  return w
}

function seedUser(store: ReturnType<typeof useUserStore>, permissions?: string[], isSuperuser = false) {
  store.token = 'tok'
  store.userInfo = {
    id: 1,
    username: 'u',
    email: 'u@x',
    ...(permissions ? { permissions } : {}),
    is_superuser: isSuperuser,
  } as never
}


function visibleRebuildButtons(w: Awaited<ReturnType<typeof mountView>>) {
  // 统计渲染出来的重建入口（桌面行 / 移动卡片 / 顶部条）：按 aria-label 匹配
  return w.findAll('button[aria-label="重新解析"]').length
    + w.findAll('button').filter((b) => b.text().includes('重建')).length
}

beforeEach(() => {
  vi.clearAllMocks()
  apiMocks.getKnowledgeBases.mockResolvedValue({ data: KB_FIXTURE })
  apiMocks.getRagMetrics.mockResolvedValue({ data: { success: true, data: null } })
  apiMocks.rebuildKnowledgeBase.mockResolvedValue({ data: { success: true } })
})

describe('知识库 · 重建入口按 RBAC 显隐', () => {
  it('无 rebuild 权限 → 所有重建入口隐藏', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    seedUser(useUserStore(), ['knowledge_base:read'])
    const w = await mountView()

    expect(visibleRebuildButtons(w), '无权限仍渲染重建入口').toBe(0)
    w.unmount()
  })

  it('有 rebuild 权限 → 重建入口可见', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    seedUser(useUserStore(), ['knowledge_base:read', 'knowledge_base:rebuild_index'])
    const w = await mountView()

    const txt = w.text()
    expect(visibleRebuildButtons(w), '有权限应渲染重建入口').toBeGreaterThan(0)
    w.unmount()
  })

  it('permissions 未下发（旧后端）→ 诚实降级保持可见', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    seedUser(useUserStore(), undefined)
    const w = await mountView()

    expect(visibleRebuildButtons(w), '权限未知时不应误隐藏入口').toBeGreaterThan(0)
    w.unmount()
  })

  it('超管恒可见（即使 permissions 列表漏了该键）', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    seedUser(useUserStore(), [], true)
    const w = await mountView()

    expect(visibleRebuildButtons(w)).toBeGreaterThan(0)
    w.unmount()
  })
})
