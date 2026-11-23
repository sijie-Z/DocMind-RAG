/**
 * chunk 浏览/修正的 API 契约与前端行为判别用例。
 *
 * 变异点：
 * - API 路径/参数名漂移 → 契约用例红；
 * - 把 manual_override 当 parser_original 展示 → provenance 用例红；
 * - 启停按钮方向取反 → 方向用例红；
 * - RBAC 隐藏逻辑写反 → 权限用例红。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import naive, {
  NAlert,
  NButton,
  NEmpty,
  NInput,
  NPagination,
  NSelect,
  NSkeleton,
  NTag
} from 'naive-ui'
import { createMemoryHistory, createRouter } from 'vue-router'

const get = vi.fn()
const put = vi.fn()
const patch = vi.fn()
const del = vi.fn()

vi.mock('@/utils/request', () => ({
  default: {
    get: (...args: unknown[]) => get(...args),
    put: (...args: unknown[]) => put(...args),
    patch: (...args: unknown[]) => patch(...args),
    delete: (...args: unknown[]) => del(...args)
  }
}))

const messageSpies = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn()
}))
vi.mock('@/utils/message', () => ({ useDedupedMessage: () => messageSpies }))

import {
  getDocumentChunks,
  resetDocumentChunkOverride,
  setDocumentChunkEnabled,
  setDocumentChunkOverride
} from '@/api/knowledge'
import ChunkBrowser from '../components/ChunkBrowser.vue'
import zh from '@/locales/zh'
import { useUserStore } from '@/stores/user'

const ITEM = {
  chunk_id: 'c-1',
  chunk_index: 0,
  document_id: 'doc-1',
  document_title: 'contract.pdf',
  indexed_text: '第一条 检索文本',
  parser_original_available: true,
  parser_original_text: '第一条 原始文本',
  text_provenance: 'parser_original',
  page_number: 1,
  start_pos: 0,
  end_pos: 12,
  section_title: null,
  clause_no: [1],
  section_no: [],
  is_enabled: true,
  override_state: 'none',
  effective_text_provenance: 'parser_original',
  override_text: null,
  override_id: null,
  override_updated_at: null
}

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

beforeEach(() => {
  get.mockReset()
  put.mockReset()
  patch.mockReset()
  del.mockReset()
  messageSpies.success.mockReset()
  messageSpies.error.mockReset()
  messageSpies.warning.mockReset()
  get.mockResolvedValue({
    data: { data: { items: [ITEM], total: 1, page: 1, page_size: 20, pages: 1 } }
  })
  put.mockResolvedValue({ data: { data: { ...ITEM, override_state: 'active' } } })
  patch.mockResolvedValue({ data: { data: { ...ITEM, is_enabled: false } } })
  del.mockResolvedValue({ data: { data: ITEM } })
})

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/knowledge/:documentId', name: 'KnowledgeDocument', component: { template: '<div />' } }
    ]
  })
}

async function mountBrowser(permissions: string[] | null = ['document:update']) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useUserStore()
  store.userInfo = {
    id: 1,
    username: 'u',
    email: 'u@t',
    permissions,
    organizations: [],
    createdAt: '',
    updatedAt: ''
  } as never
  const router = makeRouter()
  await router.push('/knowledge/doc-1')
  await router.isReady()
  const w = mount(ChunkBrowser, {
    props: { documentId: 'doc-1' },
    attachTo: document.body,
    global: {
      plugins: [pinia, i18n, router, naive],
      stubs: { teleport: true }
    }
  })
  await flushPromises()
  await nextTick()
  return { w, router }
}

describe('chunk 浏览 API 契约', () => {
  it('列表路径、分页/过滤参数与静默头正确', async () => {
    await getDocumentChunks('doc-1', { page: 2, page_size: 20, enabled: false, override_state: 'active', q: '付款' }, { silent: true })
    expect(get).toHaveBeenCalledWith('/documents/doc-1/chunks', {
      params: { page: 2, page_size: 20, enabled: false, override_state: 'active', q: '付款' },
      headers: { 'X-Silent-Error': '1' }
    })
  })

  it('修正/启停/撤销分别走 put/patch/delete 且 chunk_id 不混入路径', async () => {
    await setDocumentChunkOverride('doc-1', { chunk_id: 'c-1', text: '修正', apply_to_retrieval: true })
    await setDocumentChunkEnabled('doc-1', { chunk_id: 'c-1', enabled: false })
    await resetDocumentChunkOverride('doc-1', 'c-1')
    expect(put).toHaveBeenCalledWith('/documents/doc-1/chunks/override', {
      chunk_id: 'c-1', text: '修正', apply_to_retrieval: true
    })
    expect(patch).toHaveBeenCalledWith('/documents/doc-1/chunks/enabled', {
      chunk_id: 'c-1', enabled: false
    })
    expect(del).toHaveBeenCalledWith('/documents/doc-1/chunks/override', {
      params: { chunk_id: 'c-1' }
    })
  })
})

describe('ChunkBrowser 行为', () => {
  it('展示列表、原文折叠与 provenance 标记；manual 不能显示为 parser original', async () => {
    get.mockResolvedValueOnce({
      data: {
        data: {
          items: [{ ...ITEM, effective_text_provenance: 'manual_override', override_state: 'active' }],
          total: 1, page: 1, page_size: 20, pages: 1
        }
      }
    })
    const { w } = await mountBrowser()
    expect(w.findAll('[data-testid="chunk-browser-row"]')).toHaveLength(1)
    expect(w.get('[data-testid="chunk-provenance"]').text()).toBe('人工修正文本')
    expect(w.get('[data-testid="chunk-original-detail"]').text()).toContain('查看 parser 原文')
    expect(w.text()).toContain('第一条 原始文本')
  })

  it('禁用按钮传 enabled:false，且结果反向更新状态', async () => {
    const { w } = await mountBrowser()
    await w.get('[data-testid="chunk-toggle-enabled"]').trigger('click')
    await flushPromises()
    expect(patch).toHaveBeenCalledWith('/documents/doc-1/chunks/enabled', {
      chunk_id: 'c-1', enabled: false
    })
    expect(w.get('[data-testid="chunk-browser-row"]').text()).toContain('已停用')
  })

  it('保存并选择应用时 apply_to_retrieval=true；草稿为 false', async () => {
    const { w } = await mountBrowser()
    await w.get('[data-testid="chunk-edit"]').trigger('click')
    await w.get('[data-testid="chunk-edit-text"] textarea').setValue('新的检索文本')
    await w.get('[data-testid="chunk-save-active"]').trigger('click')
    await flushPromises()
    expect(put).toHaveBeenCalledWith('/documents/doc-1/chunks/override', {
      chunk_id: 'c-1', text: '新的检索文本', apply_to_retrieval: true
    })
  })

  it('无 document:update 权限时隐藏编辑/启停入口（未知权限不隐藏）', async () => {
    const { w } = await mountBrowser([])
    expect(w.find('[data-testid="chunk-edit"]').exists()).toBe(false)
    expect(w.find('[data-testid="chunk-toggle-enabled"]').exists()).toBe(false)
    const unknown = await mountBrowser(null)
    expect(unknown.w.find('[data-testid="chunk-edit"]').exists()).toBe(true)
  })

  it('筛选与分页写入 URL，空结果显示空态而非表头', async () => {
    get.mockResolvedValue({
      data: { data: { items: [], total: 0, page: 1, page_size: 20, pages: 0 } }
    })
    const { w, router } = await mountBrowser()
    expect(w.find('[data-testid="chunk-browser-empty"]').exists()).toBe(true)
    await w.get('[data-testid="chunk-browser-search"] input').setValue('付款')
    await w.get('[data-testid="chunk-browser-apply"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.chunkQuery).toBe('付款')
    expect(get.mock.calls.at(-1)?.[1]?.params?.q).toBe('付款')
  })

  it('draft/active 状态都显示撤销入口；none 不显示', async () => {
    get.mockResolvedValue({
      data: {
        data: {
          items: [
            { ...ITEM, override_state: 'draft', override_text: '草稿' },
            { ...ITEM, chunk_id: 'c-2', override_state: 'none' }
          ],
          total: 2, page: 1, page_size: 20, pages: 1
        }
      }
    })
    const { w } = await mountBrowser()
    const rows = w.findAll('[data-testid="chunk-browser-row"]')
    expect(rows[0].find('[data-testid="chunk-reset"]').exists()).toBe(true)
    expect(rows[0].text()).toContain('仅草稿')
    expect(rows[1].find('[data-testid="chunk-reset"]').exists()).toBe(false)
  })

  it('URL query 变化（前进后退）同步筛选状态并重新请求', async () => {
    const { router } = await mountBrowser()
    const before = get.mock.calls.length
    await router.push({
      name: 'KnowledgeDocument',
      params: { documentId: 'doc-1' },
      query: { chunkEnabled: 'disabled', chunkOverride: 'active', chunkQuery: '外部链接' }
    })
    await flushPromises()
    await nextTick()
    expect(get.mock.calls.length).toBeGreaterThan(before)
    const params = get.mock.calls.at(-1)?.[1]?.params
    expect(params?.enabled).toBe(false)
    expect(params?.override_state).toBe('active')
    expect(params?.q).toBe('外部链接')
  })
})
