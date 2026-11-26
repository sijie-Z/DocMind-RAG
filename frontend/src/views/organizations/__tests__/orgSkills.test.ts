/**
 * 组织技能库页面（批次十）契约：列表 / 待审队列 / 作者自审禁用 / 版本 diff /
 * 启用·禁用·回滚·归档 / 错误态 / 无紫色。
 *
 * 变异点：
 * - 作者自审不禁用 → 对应用例红；
 * - diff 不请求/不渲染增删 → 红；
 * - 回滚不取更早 approved 版本 → 红；
 * - 页面重新读 Redis/旧接口（api 不再调用新库）→ mock 断言红。
 */
import { readFileSync } from 'node:fs'
import { resolve as resolvePath } from 'node:path'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { NAlert, NButton, NCard, NModal, NPagination, NSelect, NSpin, NTag } from 'naive-ui'

import OrgSkillsView from '../skills.vue'
import { orgSkillsApi } from '@/api/orgSkills'
import zh from '@/locales/zh'

const userState = vi.hoisted(() => ({ id: 2, admin: true }))
const messageMocks = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))

vi.mock('@/stores/user', () => ({
  useUserStore: () => ({
    get userInfo() {
      return { id: userState.id }
    },
    get isAdmin() {
      return userState.admin
    },
  }),
}))
vi.mock('@/utils/message', () => ({ useDedupedMessage: () => messageMocks }))
vi.mock('vue-router', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>()
  return { ...actual, useRoute: () => ({ query: {} }), useRouter: () => ({ push: vi.fn() }) }
})
vi.mock('@/api/orgSkills', () => ({
  orgSkillsApi: {
    list: vi.fn(),
    get: vi.fn(),
    getVersion: vi.fn(),
    diff: vi.fn(),
    propose: vi.fn(),
    approve: vi.fn(),
    reject: vi.fn(),
    enable: vi.fn(),
    disable: vi.fn(),
    rollback: vi.fn(),
    archive: vi.fn(),
  },
}))

const api = vi.mocked(orgSkillsApi, true)
const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

const SKILL = {
  id: 'sk-1',
  organization_id: 1,
  name: '季度财务总结',
  description: '每季度复用',
  status: 'active' as const,
  latest_version: 2,
  enabled_version_no: 2 as number | null,
  created_by: 1,
  updated_by: 1,
  archived_at: null,
  archived_by: null,
  lock_version: 2,
  created_at: '2026-11-20T00:00:00Z',
  updated_at: '2026-11-22T00:00:00Z',
  pending_count: 1,
}

const V1 = {
  id: 'ver-1',
  skill_id: 'sk-1',
  version_no: 1,
  payload_sha256: 'a'.repeat(64),
  source: 'run_proposal' as const,
  provenance: { source_task_id: 'task-1' },
  review_status: 'approved' as const,
  reviewed_by: 2,
  reviewed_at: '2026-11-20T00:00:00Z',
  review_note: null,
  created_by: 1,
  created_at: '2026-11-20T00:00:00Z',
}
const V2 = {
  ...V1,
  id: 'ver-2',
  version_no: 2,
  payload_sha256: 'b'.repeat(64),
  source: 'manual' as const,
  provenance: { source: 'manual' },
  review_note: '触发词收紧',
  created_at: '2026-11-21T00:00:00Z',
}
const V3 = {
  ...V1,
  id: 'ver-3',
  version_no: 3,
  payload_sha256: 'c'.repeat(64),
  source: 'run_proposal' as const,
  review_status: 'pending' as const,
  reviewed_by: null,
  reviewed_at: null,
  created_at: '2026-11-22T00:00:00Z',
}

function detail(overrides: Record<string, unknown> = {}) {
  return { ...SKILL, versions: [V3, V2, V1], ...overrides }
}

function mountView() {
  return mount(OrgSkillsView, {
    global: {
      plugins: [i18n],
      components: { NAlert, NButton, NCard, NModal, NPagination, NSelect, NSpin, NTag },
      stubs: { teleport: true },
    },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  userState.id = 2
  userState.admin = true
  api.list.mockResolvedValue({
    data: { success: true, data: { items: [SKILL], total: 1, page: 1, page_size: 20 } },
  } as never)
  api.get.mockResolvedValue({ data: { success: true, data: detail() } } as never)
  api.diff.mockResolvedValue({
    data: {
      success: true,
      data: {
        skill_id: 'sk-1',
        base_version_no: 1,
        target_version_no: 2,
        base_sha256: 'a'.repeat(64),
        target_sha256: 'b'.repeat(64),
        trigger_phrases: { base: ['季度财务总结'], target: ['季度财务总结', '分析现金流'], added: ['分析现金流'], removed: [] },
        tool_sequence: { base: ['search_knowledge_base'], target: ['search_knowledge_base', 'web_search'], added: ['web_search'], removed: [] },
      },
    },
  } as never)
  api.approve.mockResolvedValue({ data: { success: true, data: {} } } as never)
  api.reject.mockResolvedValue({ data: { success: true, data: {} } } as never)
  api.enable.mockResolvedValue({ data: { success: true, data: {} } } as never)
  api.disable.mockResolvedValue({ data: { success: true, data: {} } } as never)
  api.rollback.mockResolvedValue({ data: { success: true, data: {} } } as never)
  api.archive.mockResolvedValue({ data: { success: true, data: {} } } as never)
})

describe('OrgSkillsView', () => {
  it('列表请求默认 active，渲染名称/启用版本/待审数', async () => {
    const wrapper = mountView()
    await flushPromises()

    expect(api.list).toHaveBeenCalledWith({ status: 'active', page: 1, page_size: 20 })
    const row = wrapper.find('[data-testid="org-skill-row-sk-1"]')
    expect(row.exists()).toBe(true)
    expect(row.text()).toContain('季度财务总结')
    expect(row.text()).toContain('已启用 v2')
    expect(row.text()).toContain('待审 1')
    expect(wrapper.find('[data-testid="org-skill-pending-queue"]').exists()).toBe(true)
  })

  it('待审队列：管理员可批准/拒绝，作者自己不可审核', async () => {
    const wrapper = mountView()
    await flushPromises()

    const approve = wrapper.find('[data-testid="org-skill-approve-sk-1-3"]')
    expect(approve.exists()).toBe(true)
    expect(approve.attributes('disabled')).toBeUndefined()
    await approve.trigger('click')
    await flushPromises()
    expect(api.approve).toHaveBeenCalledWith('sk-1', 3)

    // 作者视角：同一 pending 版本按钮禁用 + 明确提示。
    userState.id = 1
    const authorView = mountView()
    await flushPromises()
    const authorApprove = authorView.find('[data-testid="org-skill-approve-sk-1-3"]')
    expect(authorApprove.attributes('disabled')).toBeDefined()
    expect(authorView.text()).toContain('作者不能审核自己的版本')
  })

  it('普通成员不能审核（后端组织管理员门禁的 UI 镜像）', async () => {
    userState.admin = false
    const wrapper = mountView()
    await flushPromises()
    const approve = wrapper.find('[data-testid="org-skill-approve-sk-1-3"]')
    expect(approve.attributes('disabled')).toBeDefined()
  })

  it('详情：版本历史 + 结构化 diff（触发短语/工具序列增删）', async () => {
    const wrapper = mountView()
    await flushPromises()

    await wrapper.find('[data-testid="org-skill-open-sk-1"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="org-skill-version-2"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="org-skill-version-1"]').exists()).toBe(true)

    await wrapper.find('[data-testid="org-skill-diff-2"]').trigger('click')
    await flushPromises()
    expect(api.diff).toHaveBeenCalledWith('sk-1', 1, 2)
    const diff = wrapper.find('[data-testid="org-skill-diff"]')
    expect(diff.exists()).toBe(true)
    expect(diff.text()).toContain('分析现金流')
    expect(diff.text()).toContain('web_search')
  })

  it('启用 / 禁用 / 回滚（更早 approved）/ 归档调用正确', async () => {
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('[data-testid="org-skill-open-sk-1"]').trigger('click')
    await flushPromises()

    await wrapper.find('[data-testid="org-skill-enable-1"]').trigger('click')
    await flushPromises()
    expect(api.enable).toHaveBeenCalledWith('sk-1', 1)

    await wrapper.find('[data-testid="org-skill-rollback"]').trigger('click')
    await flushPromises()
    expect(api.rollback).toHaveBeenCalledWith('sk-1', 1)

    await wrapper.find('[data-testid="org-skill-disable"]').trigger('click')
    await flushPromises()
    expect(api.disable).toHaveBeenCalledWith('sk-1')

    await wrapper.find('[data-testid="org-skill-archive"]').trigger('click')
    await flushPromises()
    expect(api.archive).toHaveBeenCalledWith('sk-1')
  })

  it('加载失败：诚实错误态 + 重试', async () => {
    api.list.mockRejectedValue({ response: { data: { message: '数据库暂时不可用' } } })
    const wrapper = mountView()
    await flushPromises()
    const alert = wrapper.find('[data-testid="org-skill-load-error"]')
    expect(alert.exists()).toBe(true)
    expect(alert.text()).toContain('数据库暂时不可用')
  })

  it('页面不出现紫色系（check:no-purple 之外的本地兜底）', () => {
    const source = readFileSync(resolvePath(__dirname, '../skills.vue'), 'utf-8')
    expect(source).not.toMatch(/\b(?:purple|violet|fuchsia)\b/)
    expect(source).not.toMatch(/#(?:[0-9a-f]{2})?(?:8b5cf6|a855f7|9333ea|7c3aed)/i)
  })
})
