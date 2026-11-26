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

const { getShareMock, createShareMock, revokeShareMock } = vi.hoisted(() => ({
  getShareMock: vi.fn(),
  createShareMock: vi.fn(),
  revokeShareMock: vi.fn(),
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
    data: { success: true, data: { shared: false, created_at: null, expires_at: null, revoked_at: null } },
  })
  createShareMock.mockResolvedValue({
    data: {
      success: true,
      data: { token: 'share-token-1', url: '/share/artifact/share-token-1', shared: true, expires_at: null },
    },
  })
  revokeShareMock.mockResolvedValue({
    data: { success: true, data: { shared: false, revoked_at: '2026-11-27T10:00:00' } },
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
    expect(w.find('[data-testid="artifact-share-inactive"]').exists()).toBe(true)
    expect(w.find('[data-testid="artifact-share-revoked-at"]').exists()).toBe(true)
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
})
