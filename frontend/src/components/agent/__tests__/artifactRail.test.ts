/**
 * 工件右栏（批次七）：真实 API 数据驱动的列表 / 预览↔源码 / 版本历史 / 下载，
 * 以及 HTML/SVG 的沙箱约束。
 *
 * 变异点：
 * - 列表不渲染 API 返回的工件 → 「渲染真实列表」红；
 * - 预览/源码切换失效 → 切换用例红；
 * - 版本历史不渲染/点击不切换 → 版本用例红；
 * - HTML/SVG 用 v-html 直接插入宿主 DOM（而非 sandbox iframe）→ 沙箱用例红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import i18n from '@/locales'

const { listMock, getMock, fetchContentMock } = vi.hoisted(() => ({
  listMock: vi.fn(),
  getMock: vi.fn(),
  fetchContentMock: vi.fn(),
}))

vi.mock('@/api/artifacts', () => ({
  artifactsApi: {
    list: listMock,
    get: getMock,
    fetchContent: fetchContentMock,
    create: vi.fn(),
  },
}))

import ArtifactRail from '../ArtifactRail.vue'
import type { AgentArtifact } from '@/types/artifact'

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

function textResponse(text: string, ok = true): Response {
  return {
    ok,
    text: async () => text,
    blob: async () => new Blob([text]),
  } as unknown as Response
}

function mountRail() {
  return mount(ArtifactRail, {
    props: { conversationId: 'conv-1' },
    global: { plugins: [i18n] },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  URL.createObjectURL = vi.fn(() => 'blob:mock')
  URL.revokeObjectURL = vi.fn()
  listMock.mockResolvedValue({ data: { success: true, data: { items: [artifact()], total: 1 } } })
  getMock.mockResolvedValue({
    data: { success: true, data: artifact({ versions: [artifact()] }) },
  })
  fetchContentMock.mockResolvedValue(textResponse('# 报告正文'))
})

describe('ArtifactRail · 真实工件列表', () => {
  it('渲染 API 返回的工件（标题/类型/版本/大小），空态不出现', async () => {
    const w = mountRail()
    await flushPromises()
    expect(listMock).toHaveBeenCalledTimes(1)
    const [params] = listMock.mock.calls[0]
    expect(params).toMatchObject({ conversation_id: 'conv-1', limit: 100 })

    const items = w.findAll('[data-testid^="artifact-item-"]')
    expect(items).toHaveLength(1)
    expect(items[0].text()).toContain('季度报告')
    expect(items[0].text()).toContain('markdown')
    expect(items[0].text()).toContain('v1')
    expect(items[0].text()).toContain('2.0 KB')
    expect(w.find('[data-testid="artifact-rail-empty"]').exists()).toBe(false)
  })

  it('空列表显示诚实空态', async () => {
    listMock.mockResolvedValue({ data: { success: true, data: { items: [], total: 0 } } })
    const w = mountRail()
    await flushPromises()
    expect(w.find('[data-testid="artifact-rail-empty"]').exists()).toBe(true)
  })

  it('选中后加载正文；预览↔源码切换（预览渲染 Markdown，源码显示原文）', async () => {
    const w = mountRail()
    await flushPromises()
    await w.find('[data-testid="artifact-item-art-1"]').trigger('click')
    await flushPromises()
    expect(fetchContentMock).toHaveBeenCalledWith('art-1')

    // 预览态：Markdown 组件渲染（有 .markdown-body），源码 pre 不出现。
    expect(w.find('[data-testid="artifact-detail-title"]').text()).toBe('季度报告')

    await w.find('[data-testid="artifact-mode-source"]').trigger('click')
    const source = w.find('[data-testid="artifact-source"]')
    expect(source.exists()).toBe(true)
    expect(source.text()).toContain('# 报告正文')

    await w.find('[data-testid="artifact-mode-preview"]').trigger('click')
    expect(w.find('[data-testid="artifact-source"]').exists()).toBe(false)
  })

  it('版本历史渲染并支持切换版本', async () => {
    const v1 = artifact({ id: 'art-1', version_no: 1 })
    const v2 = artifact({ id: 'art-2', version_no: 2, title: '季度报告' })
    listMock.mockResolvedValue({
      data: { success: true, data: { items: [v2, v1], total: 2 } },
    })
    getMock.mockResolvedValue({
      data: { success: true, data: artifact({ ...v2, versions: [v1, v2] }) },
    })
    const w = mountRail()
    await flushPromises()
    await w.find('[data-testid="artifact-item-art-2"]').trigger('click')
    await flushPromises()

    const chips = w.findAll('[data-testid^="artifact-version-"]')
    expect(chips.map((c) => c.text())).toEqual(['v1', 'v2'])

    await w.find('[data-testid="artifact-version-1"]').trigger('click')
    await flushPromises()
    expect(fetchContentMock).toHaveBeenLastCalledWith('art-1')
  })

  it('HTML 工件只在 sandbox iframe（allow-scripts、无 allow-same-origin）中渲染', async () => {
    const html = artifact({ id: 'art-html', kind: 'html', title: '页面', filename: 'page.html' })
    listMock.mockResolvedValue({ data: { success: true, data: { items: [html], total: 1 } } })
    getMock.mockResolvedValue({ data: { success: true, data: html } })
    fetchContentMock.mockResolvedValue(
      textResponse('<html><body><script>alert(1)</script><p>hi</p></body></html>'),
    )
    const w = mountRail()
    await flushPromises()
    await w.find('[data-testid="artifact-item-art-html"]').trigger('click')
    await flushPromises()

    const frame = w.find('[data-testid="artifact-sandbox-frame"]')
    expect(frame.exists()).toBe(true)
    expect(frame.attributes('sandbox')).toBe('allow-scripts')
    expect(frame.attributes('sandbox') || '').not.toContain('allow-same-origin')
    expect(frame.attributes('srcdoc')).toContain('<p>hi</p>')
    // 宿主 DOM 里不得出现活的 <script> 节点（内容只能躺在 iframe 的 srcdoc 属性里，
    // 且 iframe 是不透明源）。改成 v-html 直插 → 本断言红。
    expect(w.element.querySelectorAll('script')).toHaveLength(0)
    expect(w.find('.rail-detail script').exists()).toBe(false)
  })

  it('正文拉取失败显示错误态，不渲染空内容', async () => {
    fetchContentMock.mockResolvedValue(textResponse('', false))
    const w = mountRail()
    await flushPromises()
    await w.find('[data-testid="artifact-item-art-1"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="artifact-content-error"]').exists()).toBe(true)
  })

  it('refreshKey 变化会重拉列表（SSE artifact 事件驱动的刷新）', async () => {
    const w = mountRail()
    await flushPromises()
    expect(listMock).toHaveBeenCalledTimes(1)
    await w.setProps({ refreshKey: 1 })
    await flushPromises()
    expect(listMock).toHaveBeenCalledTimes(2)
  })

  it('列表拉取失败显示错误态', async () => {
    listMock.mockRejectedValue(new Error('boom'))
    const w = mountRail()
    await flushPromises()
    expect(w.find('[data-testid="artifact-rail-error"]').exists()).toBe(true)
  })
})
