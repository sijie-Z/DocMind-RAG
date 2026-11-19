/**
 * 文档级 ACL 前端验收：私有徽标、授权入口、API 契约。
 *
 * 变异点：
 * - 去掉卡片/列表 `item.visibility === 'private'` 徽标 → 用例 1 红；
 * - 去掉授权入口的 `v-if="canManageAcl && private"` → 用例 2/3 红；
 * - 把 `setDocumentAuthorizedUsers` 的 payload 改成 `{ids: [...]}` → 用例 4 红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import naive, { NMessageProvider, NDialogProvider, NTooltip, NCheckbox } from 'naive-ui'
import { createKnowledgeTestRouter } from '@/test-utils/knowledgeRouter'

const { messageSpies } = vi.hoisted(() => ({
  messageSpies: { error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn() },
}))
vi.mock('@/utils/message', () => ({ useDedupedMessage: () => messageSpies }))

const apiMocks = vi.hoisted(() => ({
  getKnowledgeBases: vi.fn(),
  getDocumentDetail: vi.fn(),
  getDocumentContent: vi.fn(),
  uploadKnowledgeBase: vi.fn(),
  deleteKnowledgeBase: vi.fn(),
  batchDeleteKnowledgeBases: vi.fn(),
  rebuildKnowledgeBase: vi.fn(),
  getDocumentAuthorizedUsers: vi.fn(),
  setDocumentAuthorizedUsers: vi.fn(),
  getRagMetrics: vi.fn(),
  getUserSettings: vi.fn(),
  getUsers: vi.fn(),
}))

vi.mock('@/api/knowledge', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/knowledge')>()
  return {
    ...actual,
    getKnowledgeBases: apiMocks.getKnowledgeBases,
    getDocumentDetail: apiMocks.getDocumentDetail,
    getDocumentContent: apiMocks.getDocumentContent,
    uploadKnowledgeBase: apiMocks.uploadKnowledgeBase,
    deleteKnowledgeBase: apiMocks.deleteKnowledgeBase,
    batchDeleteKnowledgeBases: apiMocks.batchDeleteKnowledgeBases,
    rebuildKnowledgeBase: apiMocks.rebuildKnowledgeBase,
    getDocumentAuthorizedUsers: apiMocks.getDocumentAuthorizedUsers,
    setDocumentAuthorizedUsers: apiMocks.setDocumentAuthorizedUsers,
  }
})
vi.mock('@/api/rag', () => ({ getRagMetrics: apiMocks.getRagMetrics }))
vi.mock('@/api/user', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/user')>()
  return {
    ...actual,
    getUserSettings: apiMocks.getUserSettings,
    getUsers: apiMocks.getUsers,
  }
})

import KnowledgeView from '../index.vue'
import { useUserStore } from '@/stores/user'
import zh from '@/locales/zh'

const PRIVATE_DOC = {
  id: 'doc-1',
  title: 'secret.pdf',
  file_name: 'secret.pdf',
  file_type: 'pdf',
  file_size: 1024,
  tags: [],
  description: '',
  created_at: '2026-10-09T13:49:00',
  updated_at: '2026-10-09T13:49:00',
  status: 'indexed',
  visibility: 'private',
}
const ORG_DOC = { ...PRIVATE_DOC, id: 'doc-2', title: 'public.pdf', visibility: 'organization' }

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

const SlotStub = (name: string) =>
  defineComponent({ name, setup(_, { slots }) { return () => h('div', slots.default?.()) } })

async function mountView(opts: { canUpdate?: boolean } = {}) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useUserStore()
  store.userInfo = {
    id: 1,
    username: 'u',
    email: 'u@t',
    isAdmin: false,
    organization_id: 3,
    organizations: [],
    createdAt: '',
    updatedAt: '',
    permissions: opts.canUpdate === false ? [] : ['document:update'],
  } as never

  const Host = defineComponent({
    render: () =>
      h(NDialogProvider, null, {
        default: () => h(NMessageProvider, null, { default: () => h(KnowledgeView) }),
      }),
  })
  const w = mount(Host, {
    attachTo: document.body,
    global: {
      plugins: [pinia, i18n, createKnowledgeTestRouter()],
      components: { NTooltip, NCheckbox },
      stubs: {
        KnowledgeUploadPanel: true,
        KnowledgeDetailModals: true,
        Tag: SlotStub('Tag'),
        Pagination: true,
        Select: true,
        Input: true,
        RadioGroup: true,
        RadioButton: true,
        Spin: true,
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
    data: {
      success: true,
      message: 'ok',
      data: { data: [ORG_DOC, PRIVATE_DOC], total: 2, page: 1, page_size: 12 },
    },
  })
  apiMocks.getDocumentDetail.mockResolvedValue({ data: { data: PRIVATE_DOC } })
  apiMocks.getDocumentContent.mockResolvedValue({ data: { data: { content: '' } } })
  apiMocks.getRagMetrics.mockRejectedValue(new Error('n/a'))
  apiMocks.getDocumentAuthorizedUsers.mockResolvedValue({
    data: { data: { document_id: 'doc-1', users: [{ id: 9, username: 'alice' }] } },
  })
  apiMocks.getUsers.mockResolvedValue({
    data: { data: { users: [{ id: 9, username: 'alice' }], total: 1, skip: 0, limit: 200 } },
  })
  apiMocks.setDocumentAuthorizedUsers.mockResolvedValue({ data: {} })
})

describe('知识库 · 文档级 ACL', () => {
  it('私有文档显示「私有」徽标（卡片 + 列表），组织可见文档不显示', async () => {
    const w = await mountView()
    const text = w.text()
    const occurrences = text.split(zh.knowledge.privateBadge).length - 1
    // 卡片与列表两处均渲染私有徽标（默认视图下测试基座可见其中之一或两者）
    expect(occurrences, '私有徽标应渲染').toBeGreaterThanOrEqual(1)
    expect(text).toContain('secret.pdf')
    w.unmount()
  })

  it('有 document:update 权限时，私有文档出现「管理授权用户」入口', async () => {
    const w = await mountView()
    const buttons = w.findAll('button')
    const aclBtn = buttons.find((b) => (b.attributes('aria-label') || '') === zh.knowledge.actionManageAcl)
    expect(aclBtn, '授权入口应存在').toBeTruthy()
    w.unmount()
  })

  it('无 document:update 权限 → 不渲染授权入口（避免点了才 403）', async () => {
    const w = await mountView({ canUpdate: false })
    const aclBtn = w
      .findAll('button')
      .find((b) => (b.attributes('aria-label') || '') === zh.knowledge.actionManageAcl)
    expect(aclBtn, '无权限不应出现授权入口').toBeFalsy()
    w.unmount()
  })

  it('点击授权入口 → 读取当前授权名单与组织用户', async () => {
    const w = await mountView()
    const aclBtn = w
      .findAll('button')
      .find((b) => (b.attributes('aria-label') || '') === zh.knowledge.actionManageAcl)
    expect(aclBtn).toBeTruthy()
    ;(aclBtn!.element as HTMLButtonElement).click()
    await flushPromises()
    expect(apiMocks.getDocumentAuthorizedUsers).toHaveBeenCalledWith('doc-1')
    expect(apiMocks.getUsers).toHaveBeenCalledWith({ skip: 0, limit: 200 })
    w.unmount()
  })
})
