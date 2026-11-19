/**
 * 文档级启停的前端行为：方向、徽标、RBAC 显隐。
 *
 * 评审 B 变异实验指出：这些点此前零覆盖，例如把方向写反（永远传
 * enabled:true）整套测试仍然全绿。
 *
 * 变异点：
 * - handleToggleEnabled 方向取反 → 用例 1/2 红；
 * - 停用徽标 v-if 写反 → 用例 3 红；
 * - 去掉 showToggleEnabled 的 v-if → 用例 4 红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import naive, { NMessageProvider, NDialogProvider, NCheckbox } from 'naive-ui'
import { createKnowledgeTestRouter } from '@/test-utils/knowledgeRouter'

const { messageSpies } = vi.hoisted(() => ({
  messageSpies: { error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn() },
}))
vi.mock('@/utils/message', () => ({ useDedupedMessage: () => messageSpies }))

const apiMocks = vi.hoisted(() => ({
  setKnowledgeEnabled: vi.fn(),
  getKnowledgeBases: vi.fn(),
  getDocumentDetail: vi.fn(),
  getDocumentContent: vi.fn(),
  uploadKnowledgeBase: vi.fn(),
  deleteKnowledgeBase: vi.fn(),
  batchDeleteKnowledgeBases: vi.fn(),
  rebuildKnowledgeBase: vi.fn(),
  getRagMetrics: vi.fn(),
  getUserSettings: vi.fn(),
  getMyOrganizations: vi.fn(),
}))

vi.mock('@/api/knowledge', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/knowledge')>()
  return {
    ...actual,
    setKnowledgeEnabled: apiMocks.setKnowledgeEnabled,
    getKnowledgeBases: apiMocks.getKnowledgeBases,
    getDocumentDetail: apiMocks.getDocumentDetail,
    getDocumentContent: apiMocks.getDocumentContent,
    uploadKnowledgeBase: apiMocks.uploadKnowledgeBase,
    deleteKnowledgeBase: apiMocks.deleteKnowledgeBase,
    batchDeleteKnowledgeBases: apiMocks.batchDeleteKnowledgeBases,
    rebuildKnowledgeBase: apiMocks.rebuildKnowledgeBase,
  }
})
vi.mock('@/api/rag', () => ({ getRagMetrics: apiMocks.getRagMetrics }))
vi.mock('@/api/user', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/user')>()
  return {
    ...actual,
    getUserSettings: apiMocks.getUserSettings,
    getMyOrganizations: apiMocks.getMyOrganizations,
  }
})

import KnowledgeView from '../index.vue'
import { useUserStore } from '@/stores/user'
import zh from '@/locales/zh'

const DOC = {
  id: 'doc-1',
  title: 'contract.pdf',
  file_name: 'contract.pdf',
  file_type: 'pdf',
  file_size: 1024,
  tags: [],
  created_at: '2026-10-09T13:49:00',
  updated_at: '2026-10-09T13:49:00',
  status: 'indexed',
  is_enabled: true,
}

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

async function mountView(opts: { canUpdate: boolean } = { canUpdate: true }) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useUserStore()
  store.userInfo = {
    id: 1, username: 'u', email: 'u@t', isAdmin: false,
    organization_id: 3, organizations: [], createdAt: '', updatedAt: '',
    permissions: opts.canUpdate ? ['document:update'] : [],
  } as never

  const Host = defineComponent({
    render: () => h(NDialogProvider, null, { default: () => h(NMessageProvider, null, { default: () => h(KnowledgeView) }) }),
  })
  const w = mount(Host, {
    attachTo: document.body,
    global: {
      plugins: [pinia, i18n, createKnowledgeTestRouter()],
      components: { NCheckbox },
      stubs: {
        NTooltip: defineComponent({ name: 'NTooltip', setup(_, { slots }) { return () => h('div', [slots.trigger?.(), slots.default?.()]) } }),
        KnowledgeUploadPanel: true,
        KnowledgeDetailModals: true,
        Pagination: true,
        Select: true,
        Input: true,
        RadioGroup: true,
        RadioButton: true,
        Spin: true,
        Tag: defineComponent({ name: 'Tag', setup(_, { slots }) { return () => h('span', slots.default?.()) } }),
        teleport: true,
      },
    },
  })
  await flushPromises()
  await nextTick()
  return w
}

beforeEach(() => {
  vi.clearAllMocks()
  document.body.innerHTML = ''
  apiMocks.getKnowledgeBases.mockResolvedValue({
    data: { success: true, message: 'ok', data: { data: [{ ...DOC }], total: 1, page: 1, page_size: 10 } },
  })
  apiMocks.getDocumentDetail.mockResolvedValue({ data: { data: DOC } })
  apiMocks.getDocumentContent.mockResolvedValue({ data: { data: { content: '' } } })
  apiMocks.getRagMetrics.mockRejectedValue(new Error('n/a'))
  apiMocks.setKnowledgeEnabled.mockResolvedValue({ data: {} })
})

function toggleButtons() {
  return Array.from(document.querySelectorAll('button')).filter((b) => {
    const label = b.getAttribute('aria-label') || ''
    return label.includes('启用') || label.includes('停用')
  })
}

describe('知识库 · 文档级启停', () => {
  it('已启用文档点启停 → 调 API 传 enabled:false', async () => {
    const w = await mountView()
    const btns = toggleButtons()
    expect(btns.length, '应渲染启停按钮').toBeGreaterThan(0)
    ;(btns[0] as HTMLButtonElement).click()
    await flushPromises()
    expect(apiMocks.setKnowledgeEnabled).toHaveBeenCalledWith('doc-1', false, { silent: true })
    w.unmount()
  })

  it('已停用文档点启停 → 调 API 传 enabled:true（方向不翻转）', async () => {
    apiMocks.getKnowledgeBases.mockResolvedValue({
      data: { success: true, message: 'ok', data: { data: [{ ...DOC, is_enabled: false }], total: 1, page: 1, page_size: 10 } },
    })
    const w = await mountView()
    const btns = toggleButtons()
    expect(btns.length).toBeGreaterThan(0)
    ;(btns[0] as HTMLButtonElement).click()
    await flushPromises()
    expect(apiMocks.setKnowledgeEnabled).toHaveBeenCalledWith('doc-1', true, { silent: true })
    w.unmount()
  })

  it('停用文档渲染「已停用」徽标；启用文档不渲染', async () => {
    apiMocks.getKnowledgeBases.mockResolvedValue({
      data: { success: true, message: 'ok', data: { data: [{ ...DOC, is_enabled: false }], total: 1, page: 1, page_size: 10 } },
    })
    const w = await mountView()
    expect(w.text()).toContain(zh.knowledge.enabledBadge)
    w.unmount()

    apiMocks.getKnowledgeBases.mockResolvedValue({
      data: { success: true, message: 'ok', data: { data: [{ ...DOC }], total: 1, page: 1, page_size: 10 } },
    })
    document.body.innerHTML = ''
    const w2 = await mountView()
    expect(w2.text()).not.toContain(zh.knowledge.enabledBadge)
    w2.unmount()
  })

  it('无 document:update 权限 → 不渲染启停按钮（避免点了才 403）', async () => {
    const w = await mountView({ canUpdate: false })
    expect(toggleButtons().length, '无权限不应出现启停按钮').toBe(0)
    w.unmount()
  })
})
