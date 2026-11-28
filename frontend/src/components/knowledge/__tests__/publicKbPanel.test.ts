/**
 * 公开知识库发布面板（批次十三）判别用例。
 *
 * 变异点：
 * - 删除安全警告 → 警告用例红；
 * - 创建时不传白名单勾选 → payload 断言红；
 * - token 不展示/创建后不提示只显示一次 → 链接用例红；
 * - 撤销不调 API → 撤销用例红；
 * - 管理白名单保存不是 replace → setDocuments 断言红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import en from '@/locales/en'

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  create: vi.fn(),
  get: vi.fn(),
  rotate: vi.fn(),
  revoke: vi.fn(),
  candidates: vi.fn(),
  documents: vi.fn(),
  setDocuments: vi.fn(),
  setChatSettings: vi.fn(),
  listKnowledgeBases: vi.fn(),
}))

vi.mock('@/api/publicKb', () => ({
  publicKbApi: {
    list: mocks.list,
    create: mocks.create,
    get: mocks.get,
    rotate: mocks.rotate,
    revoke: mocks.revoke,
    candidates: mocks.candidates,
    documents: mocks.documents,
    setDocuments: mocks.setDocuments,
    setChatSettings: mocks.setChatSettings,
  },
  publicKbPublicApi: { get: vi.fn(), search: vi.fn(), chat: vi.fn() },
}))

vi.mock('@/api/knowledgeBases', () => ({
  listKnowledgeBases: mocks.listKnowledgeBases,
}))

import PublicKbPanel from '../PublicKbPanel.vue'

const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })

const publication = {
  id: 'pub-1',
  knowledge_base_id: 'kb-1',
  knowledge_base_name: 'Contract KB',
  title: 'Public policy KB',
  description: null,
  enabled: true,
  active: true,
  chat_enabled: false,
  daily_token_budget: null,
  expires_at: null,
  revoked_at: null,
  created_at: '2026-11-27T10:00:00Z',
  document_count: 1,
}

function mountPanel() {
  return mount(PublicKbPanel, { global: { plugins: [i18n] } })
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.list.mockResolvedValue([publication])
  mocks.listKnowledgeBases.mockResolvedValue({
    data: [
      { id: 'kb-1', organization_id: 1, name: 'Contract KB', created_at: '2026-01-01' },
      { id: 'kb-2', organization_id: 1, name: 'HR KB', created_at: '2026-01-01' },
    ],
    total: 2,
    page: 1,
    page_size: 100,
  })
  mocks.candidates.mockResolvedValue({
    documents: [
      { id: 'doc-a', title: 'Policy A' },
      { id: 'doc-b', title: 'Policy B' },
    ],
    truncated: false,
  })
  mocks.documents.mockResolvedValue({
    documents: [{ id: 'doc-a', title: 'Policy A', status: 'indexed', searchable: true }],
    document_count: 1,
  })
  mocks.create.mockResolvedValue({
    ...publication,
    token: 'token-new',
    url: '/public/kb/token-new',
  })
  mocks.revoke.mockResolvedValue({ ...publication, active: false })
  mocks.setDocuments.mockResolvedValue({ documents: [], document_count: 2 })
  mocks.setChatSettings.mockResolvedValue({
    ...publication,
    chat_enabled: true,
    daily_token_budget: 1234,
  })
})

describe('PublicKbPanel', () => {
  it('展示发布列表、状态与安全警告（任何持链接者可检索）', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    expect(mocks.list).toHaveBeenCalledTimes(1)
    expect(wrapper.find('[data-testid="public-kb-warning"]').text()).toContain(
      'Anyone with the link can search',
    )
    const row = wrapper.find('[data-testid="public-kb-publication-pub-1"]')
    expect(row.exists()).toBe(true)
    expect(row.text()).toContain('Public policy KB')
    expect(row.text()).toContain('Active')
    expect(row.text()).toContain('1')
  })

  it('创建：选 KB 拉候选、勾选白名单、展示一次性 token 链接', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    await wrapper.find('[data-testid="public-kb-create"]').trigger('click')
    await flushPromises()

    await wrapper.find('[data-testid="public-kb-form-kb"]').setValue('kb-1')
    await flushPromises()
    expect(mocks.candidates).toHaveBeenCalledWith('kb-1')

    await wrapper.find('[data-testid="public-kb-form-title"]').setValue('Policy portal')
    await wrapper.find('[data-testid="public-kb-form-description"]').setValue('Read only')
    await wrapper.find('[data-testid="public-kb-candidate-doc-a"]').setValue(true)
    await wrapper.find('[data-testid="public-kb-candidate-doc-b"]').setValue(true)
    await wrapper.find('[data-testid="public-kb-create-dialog"] form').trigger('submit')
    await flushPromises()

    expect(mocks.create).toHaveBeenCalledWith({
      knowledge_base_id: 'kb-1',
      title: 'Policy portal',
      description: 'Read only',
      expires_in_days: null,
      document_ids: ['doc-a', 'doc-b'],
    })
    const link = wrapper.find('[data-testid="public-kb-created-link"]').element as HTMLInputElement
    expect(link.value).toContain('/public/kb/token-new')
    expect(wrapper.text()).toContain('returned only once')
    // 创建成功后表单不再可编辑，token 只在本次展示。
    expect(wrapper.find('[data-testid="public-kb-form-title"]').exists()).toBe(false)
  })

  it('管理白名单：合并候选与当前项，保存走 replace', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    await wrapper.find('[data-testid="public-kb-manage-pub-1"]').trigger('click')
    await flushPromises()

    expect(mocks.documents).toHaveBeenCalledWith('pub-1')
    expect(mocks.candidates).toHaveBeenCalledWith('kb-1')
    expect(wrapper.find('[data-testid="public-kb-manage-doc-doc-a"]').element).toBeTruthy()
    // 追加 doc-b 后保存。
    await wrapper.find('[data-testid="public-kb-manage-doc-doc-b"]').setValue(true)
    await wrapper.find('[data-testid="public-kb-manage-save"]').trigger('click')
    await flushPromises()

    expect(mocks.setDocuments).toHaveBeenCalledWith('pub-1', 'replace', ['doc-a', 'doc-b'])
  })

  it('撤销：确认后调用 API 并刷新列表', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    await wrapper.find('[data-testid="public-kb-revoke-pub-1"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="public-kb-revoke-dialog"]').exists()).toBe(true)
    await wrapper.find('[data-testid="public-kb-revoke-confirm"]').trigger('click')
    await flushPromises()
    expect(mocks.revoke).toHaveBeenCalledWith('pub-1')
    expect(mocks.list).toHaveBeenCalledTimes(2)
  })
})

describe('PublicKbPanel chat settings (batch 17)', () => {
  it('列表展示聊天开关状态（默认未开启）', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    const status = wrapper.find('[data-testid="public-kb-chat-status-pub-1"]')
    expect(status.exists()).toBe(true)
    expect(status.text()).toBe('Off')
  })

  it('管理对话框保存聊天开关与日预算（走 chat-settings，不是白名单 replace）', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    await wrapper.find('[data-testid="public-kb-manage-pub-1"]').trigger('click')
    await flushPromises()

    const enabled = wrapper.find('[data-testid="public-kb-chat-enabled"]')
    expect((enabled.element as HTMLInputElement).checked).toBe(false)
    expect(
      (wrapper.find('[data-testid="public-kb-chat-budget"]').element as HTMLInputElement).value,
    ).toBe('')

    await enabled.setValue(true)
    await wrapper.find('[data-testid="public-kb-chat-budget"]').setValue('1234')
    await wrapper.find('[data-testid="public-kb-chat-settings-save"]').trigger('click')
    await flushPromises()

    expect(mocks.setChatSettings).toHaveBeenCalledWith('pub-1', {
      chat_enabled: true,
      daily_token_budget: 1234,
    })
    expect(mocks.setDocuments).not.toHaveBeenCalled()
    expect(mocks.list).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('Chat settings updated')
  })

  it('预算留空 = null（使用全局默认）', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    await wrapper.find('[data-testid="public-kb-manage-pub-1"]').trigger('click')
    await flushPromises()
    await wrapper.find('[data-testid="public-kb-chat-enabled"]').setValue(true)
    await wrapper.find('[data-testid="public-kb-chat-settings-save"]').trigger('click')
    await flushPromises()
    expect(mocks.setChatSettings).toHaveBeenCalledWith('pub-1', {
      chat_enabled: true,
      daily_token_budget: null,
    })
  })

  it('负数预算：前端拒绝且不调用 API', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    await wrapper.find('[data-testid="public-kb-manage-pub-1"]').trigger('click')
    await flushPromises()
    await wrapper.find('[data-testid="public-kb-chat-budget"]').setValue('-1')
    await wrapper.find('[data-testid="public-kb-chat-settings-save"]').trigger('click')
    await flushPromises()
    expect(mocks.setChatSettings).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="public-kb-chat-settings-error"]').exists()).toBe(true)
  })

  it('已开启时列表显示 Enabled', async () => {
    mocks.list.mockResolvedValue([{ ...publication, chat_enabled: true, daily_token_budget: 500 }])
    const wrapper = mountPanel()
    await flushPromises()
    expect(wrapper.find('[data-testid="public-kb-chat-status-pub-1"]').text()).toBe('Enabled')
  })
})

describe('PublicKbPanel chat settings edge cases (review hardening)', () => {
  it('打开管理对话框时预填已保存的聊天开关与预算', async () => {
    mocks.list.mockResolvedValue([
      { ...publication, chat_enabled: true, daily_token_budget: 500 },
    ])
    const wrapper = mountPanel()
    await flushPromises()
    await wrapper.find('[data-testid="public-kb-manage-pub-1"]').trigger('click')
    await flushPromises()

    expect(
      (wrapper.find('[data-testid="public-kb-chat-enabled"]').element as HTMLInputElement)
        .checked,
    ).toBe(true)
    expect(
      (wrapper.find('[data-testid="public-kb-chat-budget"]').element as HTMLInputElement).value,
    ).toBe('500')
  })

  it('超过上限的预算：前端拒绝且不调用 API', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    await wrapper.find('[data-testid="public-kb-manage-pub-1"]').trigger('click')
    await flushPromises()
    await wrapper.find('[data-testid="public-kb-chat-budget"]').setValue('10000001')
    await wrapper.find('[data-testid="public-kb-chat-settings-save"]').trigger('click')
    await flushPromises()
    expect(mocks.setChatSettings).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="public-kb-chat-settings-error"]').exists()).toBe(true)
  })

  it('保存失败：显示错误且不改写目标', async () => {
    mocks.setChatSettings.mockRejectedValueOnce(new Error('network'))
    const wrapper = mountPanel()
    await flushPromises()
    await wrapper.find('[data-testid="public-kb-manage-pub-1"]').trigger('click')
    await flushPromises()
    await wrapper.find('[data-testid="public-kb-chat-settings-save"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="public-kb-chat-settings-error"]').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('Chat settings updated')
  })
})
