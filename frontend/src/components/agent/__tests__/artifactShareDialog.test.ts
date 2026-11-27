/**
 * 工件分享对话框（批次九）：创建/轮换、复制、撤销、过期选择与安全提示。
 *
 * 变异点：
 * - 不调用 createShare/revokeShare API → 对应用例红；
 * - 创建后不回显一次性链接 → 「链接显示」红；
 * - 不显示「任何拿到链接的人都能查看」警告 → 警告用例红；
 * - 复制不写剪贴板 → 复制用例红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import en from '@/locales/en'

const { getShareMock, createShareMock, revokeShareMock, listSharesMock } = vi.hoisted(() => ({
  getShareMock: vi.fn(),
  createShareMock: vi.fn(),
  revokeShareMock: vi.fn(),
  listSharesMock: vi.fn(),
}))

vi.mock('@/api/artifacts', () => ({
  artifactsApi: {
    list: vi.fn(),
    get: vi.fn(),
    create: vi.fn(),
    fetchContent: vi.fn(),
    getShare: getShareMock,
    createShare: createShareMock,
    revokeShare: revokeShareMock,
    listShares: listSharesMock,
  },
}))

import ArtifactSharePanel from '../ArtifactSharePanel.vue'
import type { AgentArtifact } from '@/types/artifact'

const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })

function artifact(overrides: Partial<AgentArtifact> = {}): AgentArtifact {
  return {
    id: 'art-1',
    organization_id: 1,
    user_id: 1,
    conversation_id: 'conv-1',
    task_id: null,
    agent_id: null,
    kind: 'markdown',
    title: '季度报告',
    filename: '季度报告.md',
    mime_type: 'text/markdown; charset=utf-8',
    content_sha256: 'a'.repeat(64),
    file_size: 2048,
    source_tool: 'create_artifact',
    source_call_id: 'call-1',
    version_no: 1,
    replaces_artifact_id: null,
    status: 'ready',
    created_at: '2026-11-25T10:00:00',
    ...overrides,
  }
}

function mountPanel() {
  return mount(ArtifactSharePanel, {
    props: { show: true, artifact: artifact() },
    global: { plugins: [i18n] },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  getShareMock.mockResolvedValue({
    data: {
      success: true,
      data: {
        shared: false,
        created_at: null,
        expires_at: null,
        revoked_at: null,
        access_count: 0,
        last_accessed_at: null,
      },
    },
  })
  listSharesMock.mockResolvedValue({
    data: { success: true, data: { items: [], total: 0 } },
  })
  createShareMock.mockResolvedValue({
    data: {
      success: true,
      data: {
        token: 'share-token-1',
        url: '/share/artifact/share-token-1',
        shared: true,
        created_at: '2026-11-27T10:00:00+00:00',
        expires_at: null,
        revoked_at: null,
        access_count: 0,
        last_accessed_at: null,
      },
    },
  })
  revokeShareMock.mockResolvedValue({
    data: {
      success: true,
      data: {
        shared: false,
        created_at: '2026-11-26T10:00:00+00:00',
        expires_at: null,
        revoked_at: '2026-11-27T10:00:00+00:00',
        access_count: 0,
        last_accessed_at: null,
      },
    },
  })
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
    configurable: true,
  })
})

describe('ArtifactSharePanel', () => {
  it('加载状态并在未分享时显示创建入口与安全警告', async () => {
    const w = mountPanel()
    await flushPromises()
    expect(getShareMock).toHaveBeenCalledWith('art-1')
    expect(w.find('[data-testid="artifact-share-inactive"]').exists()).toBe(true)
    expect(w.find('[data-testid="artifact-share-warning"]').text()).toContain(
      'Anyone with this link can view this artifact version',
    )
    expect(w.find('[data-testid="artifact-share-copy"]').attributes('disabled')).toBeDefined()
    expect(w.find('[data-testid="artifact-share-revoke"]').attributes('disabled')).toBeDefined()
  })

  it('创建链接后显示一次性链接并可复制', async () => {
    const w = mountPanel()
    await flushPromises()
    await w.find('[data-testid="artifact-share-create"]').trigger('click')
    await flushPromises()
    expect(createShareMock).toHaveBeenCalledWith('art-1', null)

    const input = w.find('[data-testid="artifact-share-link"]').element as HTMLInputElement
    expect(input.value).toBe(`${window.location.origin}/share/artifact/share-token-1`)
    expect(w.text()).toContain('shown only once')

    await w.find('[data-testid="artifact-share-copy"]').trigger('click')
    await flushPromises()
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      `${window.location.origin}/share/artifact/share-token-1`,
    )
    expect(w.text()).toContain('Copied')
  })

  it('有效期选择会传给创建 API', async () => {
    const w = mountPanel()
    await flushPromises()
    await w.find('[data-testid="artifact-share-expiry"]').setValue('30')
    await w.find('[data-testid="artifact-share-create"]').trigger('click')
    await flushPromises()
    expect(createShareMock).toHaveBeenCalledWith('art-1', 30)
  })

  it('已分享时可重新生成（轮换）并撤销', async () => {
    getShareMock.mockResolvedValue({
      data: {
        success: true,
        data: { shared: true, created_at: '2026-11-26T10:00:00', expires_at: null, revoked_at: null },
      },
    })
    const w = mountPanel()
    await flushPromises()
    expect(w.find('[data-testid="artifact-share-active"]').exists()).toBe(true)
    expect(w.find('[data-testid="artifact-share-create"]').text()).toContain('Regenerate')

    await w.find('[data-testid="artifact-share-revoke"]').trigger('click')
    await flushPromises()
    expect(revokeShareMock).toHaveBeenCalledWith('art-1')
    expect(w.find('[data-testid="artifact-share-revoked"]').exists()).toBe(true)
    expect(w.find('[data-testid="artifact-share-inactive"]').exists()).toBe(false)
    expect(w.find('[data-testid="artifact-share-revoked-at"]').exists()).toBe(true)
  })

  it('过期分享显示过期态，而不是“尚未分享”', async () => {
    getShareMock.mockResolvedValue({
      data: {
        success: true,
        data: {
          shared: false,
          expires_at: new Date(Date.now() - 60_000).toISOString(),
          revoked_at: null,
        },
      },
    })
    const w = mountPanel()
    await flushPromises()
    expect(w.find('[data-testid="artifact-share-expired"]').exists()).toBe(true)
    expect(w.find('[data-testid="artifact-share-inactive"]').exists()).toBe(false)
  })

  it('轮换时回填现有过期档位，不会静默变成永久链接', async () => {
    getShareMock.mockResolvedValue({
      data: {
        success: true,
        data: {
          shared: true,
          expires_at: new Date(Date.now() + 30 * 86_400_000).toISOString(),
          revoked_at: null,
        },
      },
    })
    const w = mountPanel()
    await flushPromises()
    await w.find('[data-testid="artifact-share-create"]').trigger('click')
    await flushPromises()
    expect(createShareMock).toHaveBeenCalledWith('art-1', 30)
  })

  it('轮换会调用创建 API 并显示新 token', async () => {
    getShareMock.mockResolvedValue({
      data: { success: true, data: { shared: true, created_at: null, expires_at: null, revoked_at: null } },
    })
    const w = mountPanel()
    await flushPromises()
    await w.find('[data-testid="artifact-share-create"]').trigger('click')
    await flushPromises()
    expect(createShareMock).toHaveBeenCalledWith('art-1', null)
    const input = w.find('[data-testid="artifact-share-link"]').element as HTMLInputElement
    expect(input.value).toContain('share-token-1')
  })

  it('创建失败显示错误态', async () => {
    createShareMock.mockRejectedValue(new Error('boom'))
    const w = mountPanel()
    await flushPromises()
    await w.find('[data-testid="artifact-share-create"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="artifact-share-error"]').text()).toContain(
      'Failed to create share link',
    )
    expect(w.find('[data-testid="artifact-share-link"]').exists()).toBe(false)
  })

  it('状态加载失败显示错误态', async () => {
    getShareMock.mockRejectedValue(new Error('boom'))
    const w = mountPanel()
    await flushPromises()
    expect(w.find('[data-testid="artifact-share-error"]').text()).toContain(
      'Failed to load share status',
    )
  })

  // ── 批次十一：访问统计 / 我的分享列表 ────────────────────────────────────

  it('状态返回访问次数与最后访问时间时正确渲染', async () => {
    getShareMock.mockResolvedValue({
      data: {
        success: true,
        data: {
          shared: true,
          created_at: '2026-11-26T10:00:00+00:00',
          expires_at: null,
          revoked_at: null,
          access_count: 7,
          last_accessed_at: '2026-11-27T09:00:00+00:00',
        },
      },
    })
    const w = mountPanel()
    await flushPromises()
    expect(w.find('[data-testid="artifact-share-access-count"]').text()).toContain('Views: 7')
    expect(w.find('[data-testid="artifact-share-last-accessed"]').text()).toContain(
      'Last accessed',
    )
  })

  it('从未访问时显示 never accessed（而不是空白）', async () => {
    getShareMock.mockResolvedValue({
      data: {
        success: true,
        data: {
          shared: true,
          created_at: '2026-11-26T10:00:00+00:00',
          expires_at: null,
          revoked_at: null,
          access_count: 0,
          last_accessed_at: null,
        },
      },
    })
    const w = mountPanel()
    await flushPromises()
    expect(w.find('[data-testid="artifact-share-access-count"]').text()).toContain('Views: 0')
    expect(w.find('[data-testid="artifact-share-last-accessed"]').text()).toContain(
      'Never accessed',
    )
  })

  it('已撤销的分享仍显示保留的访问统计', async () => {
    getShareMock.mockResolvedValue({
      data: {
        success: true,
        data: {
          shared: false,
          created_at: '2026-11-26T10:00:00+00:00',
          expires_at: null,
          revoked_at: '2026-11-27T10:00:00+00:00',
          access_count: 5,
          last_accessed_at: '2026-11-27T09:00:00+00:00',
        },
      },
    })
    const w = mountPanel()
    await flushPromises()
    expect(w.find('[data-testid="artifact-share-revoked"]').exists()).toBe(true)
    expect(w.find('[data-testid="artifact-share-access-count"]').text()).toContain('Views: 5')
  })

  it('我的分享列表渲染标题/版本/次数/过期，并可撤销后移除', async () => {
    listSharesMock
      .mockResolvedValueOnce({
        data: {
          success: true,
          data: {
            items: [
              {
                share_id: 'share-1',
                artifact_id: 'art-1',
                title: '季度报告',
                kind: 'markdown',
                version_no: 2,
                file_size: 2048,
                created_at: '2026-11-26T10:00:00+00:00',
                expires_at: '2026-12-26T10:00:00+00:00',
                access_count: 3,
                last_accessed_at: '2026-11-27T09:00:00+00:00',
              },
            ],
            total: 1,
          },
        },
      })
      .mockResolvedValueOnce({ data: { success: true, data: { items: [], total: 0 } } })
    const w = mountPanel()
    await flushPromises()

    const item = w.find('[data-testid="artifact-share-list-item-art-1"]')
    expect(item.exists()).toBe(true)
    expect(item.text()).toContain('季度报告')
    expect(item.text()).toContain('v2')
    expect(item.text()).toContain('Views: 3')
    expect(item.text()).toContain('Expires')
    expect(item.text()).toContain('Last accessed')

    await w.find('[data-testid="artifact-share-list-revoke-art-1"]').trigger('click')
    await flushPromises()
    expect(revokeShareMock).toHaveBeenCalledWith('art-1')
    expect(listSharesMock).toHaveBeenCalledTimes(2)
    expect(w.find('[data-testid="artifact-share-list-item-art-1"]').exists()).toBe(false)
    expect(w.find('[data-testid="artifact-share-list-empty"]').exists()).toBe(true)
  })

  it('我的分享列表空态', async () => {
    const w = mountPanel()
    await flushPromises()
    expect(w.find('[data-testid="artifact-share-list-empty"]').text()).toContain(
      'No active share links',
    )
  })

  it('我的分享列表加载失败显示错误态', async () => {
    listSharesMock.mockRejectedValue(new Error('boom'))
    const w = mountPanel()
    await flushPromises()
    expect(w.find('[data-testid="artifact-share-list-error"]').text()).toContain(
      'Failed to load active shares',
    )
  })

  it('从列表撤销失败显示列表错误，且不移除条目', async () => {
    listSharesMock.mockResolvedValue({
      data: {
        success: true,
        data: {
          items: [
            {
              share_id: 'share-1',
              artifact_id: 'art-2',
              title: '他工件',
              kind: 'text',
              version_no: 1,
              file_size: 10,
              created_at: '2026-11-26T10:00:00+00:00',
              expires_at: null,
              access_count: 0,
              last_accessed_at: null,
            },
          ],
          total: 1,
        },
      },
    })
    revokeShareMock.mockRejectedValue(new Error('boom'))
    const w = mountPanel()
    await flushPromises()
    await w.find('[data-testid="artifact-share-list-revoke-art-2"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="artifact-share-list-error"]').text()).toContain(
      'Failed to revoke share',
    )
    expect(w.find('[data-testid="artifact-share-list-item-art-2"]').exists()).toBe(true)
  })

  it('刷新按钮同时重读当前工件状态与分享列表', async () => {
    const w = mountPanel()
    await flushPromises()
    expect(getShareMock).toHaveBeenCalledTimes(1)
    expect(listSharesMock).toHaveBeenCalledTimes(1)

    await w.find('[data-testid="artifact-share-list-refresh"]').trigger('click')
    await flushPromises()
    expect(getShareMock).toHaveBeenCalledTimes(2)
    expect(listSharesMock).toHaveBeenCalledTimes(2)
  })

  it('切换工件后状态加载失败，不残留上一工件的撤销/统计', async () => {
    getShareMock
      .mockResolvedValueOnce({
        data: {
          success: true,
          data: {
            shared: false,
            created_at: '2026-11-26T10:00:00+00:00',
            expires_at: null,
            revoked_at: '2026-11-27T10:00:00+00:00',
            access_count: 5,
            last_accessed_at: '2026-11-27T09:00:00+00:00',
          },
        },
      })
      .mockRejectedValueOnce(new Error('boom'))
    const w = mountPanel()
    await flushPromises()
    expect(w.find('[data-testid="artifact-share-revoked"]').exists()).toBe(true)
    expect(w.find('[data-testid="artifact-share-stats"]').exists()).toBe(true)

    await w.setProps({ artifact: artifact({ id: 'art-2', title: '另一个工件' }) })
    await flushPromises()
    expect(w.find('[data-testid="artifact-share-error"]').text()).toContain(
      'Failed to load share status',
    )
    expect(w.find('[data-testid="artifact-share-revoked"]').exists()).toBe(false)
    expect(w.find('[data-testid="artifact-share-expired"]').exists()).toBe(false)
    expect(w.find('[data-testid="artifact-share-stats"]').exists()).toBe(false)
  })
})
