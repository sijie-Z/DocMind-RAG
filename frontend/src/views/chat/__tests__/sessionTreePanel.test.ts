/**
 * 批次八 · 会话树面板（SessionTreePanel）契约。
 *
 * 判别点：
 * - 分支按 depth 渲染为树、当前分支高亮（data-active / aria-current）；
 * - 点击非当前分支只 emit select（父页据此 branch-context 原位切换），不 fork；
 * - 摘要成功更新分支摘要；503 SUMMARY_MODEL_UNAVAILABLE 显示显式失败，不伪造摘要；
 * - 分享按钮把只读链接（window.origin + 既有 /share/{token}）写入剪贴板；
 * - 空树 / 标签筛选 / 标签编辑。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import SessionTreePanel from '../components/SessionTreePanel.vue'
import zh from '@/locales/zh'
import type { SessionTreeBranch, SessionTreeData } from '@/api/sessions'

const { treeMock, summarizeMock, shareTreeMock, setBranchLabelMock, forkMock } = vi.hoisted(() => ({
  treeMock: vi.fn(),
  summarizeMock: vi.fn(),
  shareTreeMock: vi.fn(),
  setBranchLabelMock: vi.fn(),
  forkMock: vi.fn(),
}))

vi.mock('@/api/sessions', () => ({
  sessionsApi: {
    tree: treeMock,
    summarize: summarizeMock,
    shareTree: shareTreeMock,
    setBranchLabel: setBranchLabelMock,
    fork: forkMock,
    branchContext: vi.fn(),
  },
}))

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

function branch(overrides: Partial<SessionTreeBranch>): SessionTreeBranch {
  return {
    session_id: 'root',
    title: '根会话',
    parent_session_id: null,
    root_session_id: 'root',
    fork_point_message_id: null,
    fork_point_preview: null,
    fork_reason: null,
    branch_label: null,
    branch_summary: null,
    summary_model: null,
    summarized_at: null,
    created_at: '2026-11-01T00:00:00',
    depth: 0,
    is_root: true,
    is_current: false,
    parent_visible: true,
    message_count: 2,
    artifact_count: 0,
    artifact_latest_version: 0,
    artifact_version_total: 0,
    ...overrides,
  }
}

function tree(branches: SessionTreeBranch[]): SessionTreeData {
  return {
    root_session_id: 'root',
    current_session_id: branches.find((b) => b.is_current)?.session_id ?? 'root',
    root_present: branches.some((b) => b.session_id === 'root'),
    branch_count: branches.length,
    branches_truncated: false,
    tree_truncated: false,
    branches,
  }
}

const ROOT = branch({
  branch_label: '主线',
  branch_summary: '根摘要',
  summary_model: 'fake',
  is_current: true,
  message_count: 4,
  artifact_count: 1,
  artifact_latest_version: 1,
  artifact_version_total: 1,
})

const CHILD = branch({
  session_id: 'child',
  title: '试验分支',
  parent_session_id: 'root',
  fork_point_message_id: 'm2',
  fork_point_preview: '从这里分叉',
  branch_label: '试验',
  depth: 1,
  is_root: false,
  message_count: 2,
  artifact_count: 2,
  artifact_latest_version: 2,
  artifact_version_total: 3,
})

function mockTree(data: SessionTreeData = tree([ROOT, CHILD])) {
  // 组件会就地更新摘要/标签；每个用例必须拿到独立副本，避免跨用例污染。
  const snapshot = JSON.parse(JSON.stringify(data)) as SessionTreeData
  treeMock.mockResolvedValue({ data: { success: true, data: snapshot } })
}

function mountPanel() {
  return mount(SessionTreePanel, {
    props: { show: true, sessionId: 'root' },
    global: { plugins: [i18n] },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  mockTree()
})

afterEach(() => {
  document.body.innerHTML = ''
})

describe('SessionTreePanel · 树渲染与切换', () => {
  it('按 depth 渲染分支，当前分支高亮（data-active + aria-current）', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    const nodes = wrapper.findAll('[data-testid="session-tree-node"]')
    expect(nodes).toHaveLength(2)
    const rows = wrapper.findAll('li[data-session-id]')
    expect(rows[0].attributes('data-session-id')).toBe('root')
    expect(rows[1].attributes('data-session-id')).toBe('child')

    const rootNode = wrapper.find('[data-session-id="root"]')
    const childNode = wrapper.find('[data-session-id="child"]')
    expect(rootNode.attributes('data-active')).toBe('true')
    expect(rootNode.find('[data-testid="session-tree-node"]').attributes('aria-current')).toBe('true')
    expect(childNode.attributes('data-active')).toBe('false')
    expect(childNode.find('[data-testid="session-tree-node"]').attributes('aria-current')).toBeUndefined()

    expect(childNode.text()).toContain('试验')
    expect(childNode.text()).toContain('2 个工件 · 最新 v2')
    expect(childNode.find('[data-testid="session-tree-fork-point"]').text()).toContain('从这里分叉')
    expect(rootNode.find('[data-testid="session-tree-summary"]').text()).toContain('根摘要')
  })

  it('点击非当前分支只 emit select（原位切换），绝不调用 fork', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    await wrapper.find('[data-session-id="child"] [data-testid="session-tree-node"]').trigger('click')
    expect(wrapper.emitted('select')).toEqual([['child']])
    expect(wrapper.emitted('update:show')).toEqual([[false]])
    expect(forkMock).not.toHaveBeenCalled()
  })

  it('空树显示空状态', async () => {
    mockTree(tree([]))
    const wrapper = mountPanel()
    await flushPromises()

    expect(wrapper.find('[data-testid="session-tree-empty"]').exists()).toBe(true)
    expect(wrapper.findAll('[data-testid="session-tree-node"]')).toHaveLength(0)
  })

  it('按标签筛选只保留命中分支', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    await wrapper.find('[data-testid="session-tree-filter"]').setValue('试验')
    const ids = wrapper
      .findAll('li[data-session-id]')
      .map((node) => node.attributes('data-session-id'))
    expect(ids).toEqual(['child'])
  })
})

describe('SessionTreePanel · 摘要 / 分享 / 标签', () => {
  it('生成摘要成功：写入分支并显示模型结果', async () => {
    summarizeMock.mockResolvedValue({
      data: {
        success: true,
        data: {
          session_id: 'child',
          generated: true,
          summary: '新的分支摘要',
          summary_model: 'test-model',
          summarized_at: '2026-11-26T00:00:00Z',
          message_count: 2,
        },
      },
    })
    const wrapper = mountPanel()
    await flushPromises()

    const child = wrapper.find('[data-session-id="child"]')
    await child.find('[data-testid="session-tree-summarize"]').trigger('click')
    await flushPromises()

    expect(summarizeMock).toHaveBeenCalledWith('child', 'llm')
    expect(wrapper.find('[data-session-id="child"]').text()).toContain('新的分支摘要')
    expect(wrapper.find('[data-testid="session-tree-summary-error"]').exists()).toBe(false)
  })

  it('模型不可用：显式失败文案，不显示伪造摘要', async () => {
    summarizeMock.mockRejectedValue({
      response: { data: { success: false, error_code: 'SUMMARY_MODEL_UNAVAILABLE' } },
    })
    const wrapper = mountPanel()
    await flushPromises()

    await wrapper
      .find('[data-session-id="child"] [data-testid="session-tree-summarize"]')
      .trigger('click')
    await flushPromises()

    const error = wrapper.find('[data-testid="session-tree-summary-error"]')
    expect(error.exists()).toBe(true)
    expect(error.text()).toContain('摘要生成失败：模型不可用，未生成摘要')
    expect(wrapper.find('[data-session-id="child"]').find('[data-testid="session-tree-summary"]').exists()).toBe(false)
  })

  it('分享按钮复制 origin + 既有 /share/{token} 只读链接', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })
    shareTreeMock.mockResolvedValue({
      data: {
        success: true,
        data: {
          root_session_id: 'root',
          token: 'tok-123',
          url: '/share/tok-123',
          createdAt: '2026-11-26T00:00:00Z',
        },
      },
    })
    const wrapper = mountPanel()
    await flushPromises()

    await wrapper.find('[data-testid="session-tree-share"]').trigger('click')
    await flushPromises()

    expect(shareTreeMock).toHaveBeenCalledWith('root')
    expect(writeText).toHaveBeenCalledWith(`${window.location.origin}/share/tok-123`)
    expect(wrapper.find('[data-testid="session-tree-share-copied"]').exists()).toBe(true)
    expect(
      (wrapper.find('[data-testid="session-tree-share-link"]').element as HTMLInputElement).value,
    ).toBe(`${window.location.origin}/share/tok-123`)
  })

  it('树未加载或根不可见时分享禁用，绝不用当前分支兜底', async () => {
    mockTree(tree([CHILD]))
    const wrapper = mountPanel()
    await flushPromises()
    const share = wrapper.find('[data-testid="session-tree-share"]')
    expect(share.attributes('disabled')).toBeDefined()
    await share.trigger('click')
    await flushPromises()
    expect(shareTreeMock).not.toHaveBeenCalled()
  })

  it('空分支摘要清掉旧摘要，不显示陈旧内容', async () => {
    mockTree(tree([ROOT, { ...CHILD, branch_summary: '旧摘要', summary_model: 'fake' }]))
    summarizeMock.mockResolvedValue({
      data: { success: true, data: { generated: false, summary: null } },
    })
    const wrapper = mountPanel()
    await flushPromises()
    const child = wrapper.find('[data-session-id="child"]')
    expect(child.text()).toContain('旧摘要')
    await child.find('[data-testid="session-tree-summarize"]').trigger('click')
    await flushPromises()
    expect(child.text()).not.toContain('旧摘要')
    expect(wrapper.find('[data-testid="session-tree-summary-error"]').exists()).toBe(true)
  })

  it('标签编辑保存后只更新该分支标签', async () => {
    setBranchLabelMock.mockResolvedValue({
      data: { success: true, data: { session_id: 'child', branch_label: '新标签', updated: true } },
    })
    const wrapper = mountPanel()
    await flushPromises()

    const child = wrapper.find('[data-session-id="child"]')
    await child.find('[data-testid="session-tree-edit-label"]').trigger('click')
    await child.find('[data-testid="session-tree-label-input"]').setValue('新标签')
    await child.find('[data-testid="session-tree-label-save"]').trigger('click')
    await flushPromises()

    expect(setBranchLabelMock).toHaveBeenCalledWith('child', '新标签')
    expect(wrapper.find('[data-session-id="child"]').text()).toContain('新标签')
  })
})
