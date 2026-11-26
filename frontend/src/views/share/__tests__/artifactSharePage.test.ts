/**
 * 公开工件分享页（批次九）：Markdown 渲染、HTML 沙箱、失效态、最小元数据。
 *
 * 变异点：
 * - HTML 用 v-html 直插宿主 DOM（而非 sandbox iframe）→ 沙箱用例红；
 * - 失效 token 不显示不可用提示 → 失效态用例红；
 * - 渲染内部字段（org/user/conversation）→ 最小元数据用例红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createI18n } from 'vue-i18n'
import en from '@/locales/en'

const { getMock, fetchContentMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  fetchContentMock: vi.fn(),
}))

vi.mock('@/api/artifacts', () => ({
  publicArtifactsApi: { get: getMock, fetchContent: fetchContentMock },
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { token: 'public-token' } }),
}))

import PublicArtifactPage from '../artifact.vue'
import type { PublicArtifactMeta } from '@/types/artifact'

const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })

const MarkdownStub = defineComponent({
  props: { content: String },
  setup(props) {
    return () => h('div', { class: 'md' }, props.content)
  },
})

function meta(overrides: Partial<PublicArtifactMeta> = {}): PublicArtifactMeta {
  return {
    title: 'Shared report',
    kind: 'markdown',
    filename: 'report.md',
    mime_type: 'text/markdown; charset=utf-8',
    version_no: 2,
    created_at: '2026-11-26T10:00:00',
    content_url: '/api/v1/public/artifacts/public-token/content',
    expires_at: null,
    ...overrides,
  }
}

function textResponse(text: string, ok = true): Response {
  return { ok, text: async () => text } as unknown as Response
}

function mountPage() {
  return mount(PublicArtifactPage, {
    global: { plugins: [i18n], stubs: { Markdown: MarkdownStub } },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  URL.createObjectURL = vi.fn(() => 'blob:mock')
  URL.revokeObjectURL = vi.fn()
  getMock.mockResolvedValue({ data: { success: true, data: meta() } })
  fetchContentMock.mockResolvedValue(textResponse('# Shared body'))
})

describe('public artifact share page', () => {
  it('匿名访客按 token 拉取元数据与正文并渲染 Markdown', async () => {
    const w = mountPage()
    await flushPromises()
    expect(getMock).toHaveBeenCalledWith('public-token')
    expect(fetchContentMock).toHaveBeenCalledWith('public-token')
    expect(w.text()).toContain('Shared report')
    expect(w.text()).toContain('Read-only share')
    expect(w.find('.md').text()).toBe('# Shared body')
    expect(w.find('[data-testid="public-artifact-kind"]').text()).toBe('markdown')
    expect(w.text()).toContain('v2')
  })

  it('HTML 只在 sandbox iframe 中渲染，宿主 DOM 不出现活脚本', async () => {
    getMock.mockResolvedValue({
      data: { success: true, data: meta({ kind: 'html', title: 'Page', filename: 'page.html' }) },
    })
    fetchContentMock.mockResolvedValue(
      textResponse('<html><body><script>alert(1)</script><p>hi</p></body></html>'),
    )
    const w = mountPage()
    await flushPromises()
    const frame = w.find('[data-testid="public-artifact-sandbox-frame"]')
    expect(frame.exists()).toBe(true)
    expect(frame.attributes('sandbox')).toBe('allow-scripts')
    expect(frame.attributes('sandbox') || '').not.toContain('allow-same-origin')
    expect(frame.attributes('srcdoc')).toContain('<p>hi</p>')
    expect(w.element.querySelectorAll('script')).toHaveLength(0)
  })

  it('失效/撤销/过期链接显示不可用提示', async () => {
    getMock.mockRejectedValue(new Error('404'))
    const w = mountPage()
    await flushPromises()
    expect(w.find('[data-testid="public-artifact-unavailable"]').exists()).toBe(true)
    expect(w.text()).toContain('does not exist or has expired')
    expect(w.text()).not.toContain('Loading shared content')
  })

  it('元数据成功但正文 404（撤销发生于两次请求之间）也按不可用处理', async () => {
    fetchContentMock.mockResolvedValue(textResponse('', false))
    const w = mountPage()
    await flushPromises()
    expect(w.find('[data-testid="public-artifact-unavailable"]').exists()).toBe(true)
  })

  it('只渲染公开白名单字段：内部标识即便出现在响应里也不展示', async () => {
    getMock.mockResolvedValue({
      data: {
        success: true,
        data: {
          ...meta(),
          organization_id: 101,
          user_id: 202,
          conversation_id: 'conv-internal-303',
          task_id: 'task-internal-404',
          content_sha256: 'f'.repeat(64),
        },
      },
    })
    const w = mountPage()
    await flushPromises()
    const text = w.text()
    expect(text).not.toContain('conv-internal-303')
    expect(text).not.toContain('task-internal-404')
    expect(text).not.toContain('f'.repeat(64))
    expect(text).not.toContain('organization_id')
    expect(text).not.toContain('user_id')
  })

  it('下载按钮把正文导出为文件', async () => {
    const w = mountPage()
    await flushPromises()
    await w.find('[data-testid="public-artifact-download"]').trigger('click')
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1)
    expect(URL.revokeObjectURL).not.toHaveBeenCalled()
  })

  it('有过期时间时显示提示', async () => {
    getMock.mockResolvedValue({
      data: {
        success: true,
        data: meta({ expires_at: '2026-12-31T00:00:00Z' }),
      },
    })
    const w = mountPage()
    await flushPromises()
    expect(w.find('[data-testid="public-artifact-expires-at"]').exists()).toBe(true)
    expect(w.text()).toContain('expires at')
  })
})
