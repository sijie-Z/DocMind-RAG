/**
 * 文档深链页判别用例：/knowledge/:documentId?chunk=…
 *
 * 背景：聊天引用、检索命中与知识库列表都落到同一独立页；页面必须
 * 1) 全文可达并高亮命中分块；2) 只带 chunk 参数时也能定位；
 * 3) 分块失败/内容截断时如实降级，不把未定位说成已定位；
 * 4) 403/404 给出可行动的错误态；5) 同路由切换参数会重新取数（浏览器前进/后退）。
 */
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { createI18n } from 'vue-i18n'
import type { Plugin } from 'vue'
import zh from '@/locales/zh'
import en from '@/locales/en'
import Detail from '../detail.vue'
import { setPageTitleText } from '@/locales'
import {
  getDocumentChunk,
  getDocumentContent,
  getDocumentDetail,
  getDocumentDownloadUrl
} from '@/api/knowledge'

vi.mock('@/api/knowledge', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/knowledge')>()
  return {
    ...actual,
    getDocumentDetail: vi.fn(),
    getDocumentContent: vi.fn(),
    getDocumentChunk: vi.fn(),
    getDocumentDownloadUrl: vi.fn()
  }
})

const messageSpies = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
  loading: vi.fn()
}))
vi.mock('@/utils/message', () => ({
  useDedupedMessage: () => messageSpies
}))

const mockedDetail = vi.mocked(getDocumentDetail)
const mockedContent = vi.mocked(getDocumentContent)
const mockedChunk = vi.mocked(getDocumentChunk)
const mockedDownloadUrl = vi.mocked(getDocumentDownloadUrl)

enableAutoUnmount(afterEach)

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

const DOC = {
  id: 'doc-1',
  title: '软件采购合同',
  filename: '软件采购合同.pdf',
  file_type: 'pdf',
  file_size: 2048,
  status: 'indexed',
  created_at: '2026-11-01T09:00:00',
  upload_source: '知识库上传',
  upload_source_code: 'knowledge' as const
}

const CONTENT = {
  id: 'doc-1',
  filename: '软件采购合同.pdf',
  content: '第一条 标的\n\n第二条 付款 30 日\n\n第三条 验收',
  text_provenance: 'parser_original' as const,
  truncated: false,
  total_chunks: 3
}

const CHUNK = {
  chunk_id: 'chunk-1',
  chunk_index: 1,
  page_number: 2,
  text: '第二条 付款 30 日',
  text_provenance: 'parser_original' as const,
  section_title: '付款'
}

function ok<T>(data: T) {
  return { data: { data } } as never
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

async function makeRouter(path: string): Promise<Router> {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'Home', component: { template: '<div />' } },
      { path: '/knowledge', name: 'Knowledge', component: { template: '<div />' } },
      { path: '/knowledge/:documentId', name: 'KnowledgeDocument', component: { template: '<div />' } }
    ]
  })
  await router.push(path)
  await router.isReady()
  return router
}

async function mountPage(path: string, i18nInstance: Plugin = i18n) {
  const router = await makeRouter(path)
  const wrapper = mount(Detail, {
    attachTo: document.body,
    global: { plugins: [router, i18nInstance] }
  })
  await flushPromises()
  await nextTick()
  return { wrapper, router }
}

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn()
})

beforeEach(() => {
  vi.clearAllMocks()
  mockedDetail.mockResolvedValue(ok(DOC))
  mockedContent.mockResolvedValue(ok(CONTENT))
  mockedChunk.mockResolvedValue(ok(CHUNK))
})

describe('文档深链页 · 定位与降级', () => {
  it('带 chunk 参数：取详情/全文/分块，正文高亮命中块并显示定位横幅', async () => {
    const { wrapper } = await mountPage('/knowledge/doc-1?chunk=chunk-1')

    expect(mockedDetail).toHaveBeenCalledWith('doc-1', { silent: true })
    expect(mockedContent).toHaveBeenCalledWith('doc-1', { silent: true })
    expect(mockedChunk).toHaveBeenCalledWith('doc-1', 'chunk-1', { silent: true })

    expect(wrapper.find('[data-testid="document-title"]').text()).toContain('软件采购合同')
    // 可分享页的书签名应体现文档名，而不是全站通用标题。
    expect(document.title).toContain('软件采购合同')
    const mark = wrapper.find('[data-testid="document-focus"]')
    expect(mark.exists()).toBe(true)
    expect(mark.text()).toBe('第二条 付款 30 日')
    const banner = wrapper.find('[data-testid="document-focus-banner"]')
    expect(banner.text()).toContain('已定位到引用分块')
    expect(banner.text()).toContain('付款')
    expect(banner.text()).toContain('第 2 页')
    // 定位结果异步到达：必须处于活区，读屏用户才能听到「已定位」。
    expect(banner.attributes('role')).toBe('status')
    expect(banner.attributes('aria-live')).toBe('polite')
  })

  it('不带 chunk 参数：不请求分块，也不出现定位横幅', async () => {
    const { wrapper } = await mountPage('/knowledge/doc-1')

    expect(mockedChunk).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="document-focus-banner"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('第三条 验收')
  })

  it('分块网络/5xx 失败：正文照常可读，提示可重试', async () => {
    mockedChunk.mockRejectedValueOnce(new Error('network'))
    const { wrapper } = await mountPage('/knowledge/doc-1?chunk=missing')

    expect(wrapper.find('[data-testid="document-focus"]').exists()).toBe(false)
    const miss = wrapper.find('[data-testid="document-focus-miss"]')
    expect(miss.exists()).toBe(true)
    expect(miss.text()).toContain('引用分块加载失败')
    expect(wrapper.find('[data-testid="document-chunk-retry"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('第三条 验收')
  })

  it('分块 404：按未定位降级且不提供无意义重试', async () => {
    mockedChunk.mockRejectedValueOnce({ response: { status: 404 } })
    const { wrapper } = await mountPage('/knowledge/doc-1?chunk=missing')

    const miss = wrapper.find('[data-testid="document-focus-miss"]')
    expect(miss.text()).toContain('未能定位到引用分块')
    expect(wrapper.find('[data-testid="document-chunk-retry"]').exists()).toBe(false)
  })

  it('重试定位：再次请求分块并恢复高亮', async () => {
    mockedChunk.mockRejectedValueOnce(new Error('network'))
    mockedChunk.mockResolvedValueOnce(ok(CHUNK) as never)
    const { wrapper } = await mountPage('/knowledge/doc-1?chunk=chunk-1')

    await wrapper.find('[data-testid="document-chunk-retry"]').trigger('click')
    await flushPromises()
    await nextTick()

    expect(mockedChunk).toHaveBeenCalledTimes(2)
    expect(wrapper.find('[data-testid="document-focus"]').text()).toBe('第二条 付款 30 日')
    expect(wrapper.find('[data-testid="document-chunk-retry"]').exists()).toBe(false)
  })

  it('截断文档中分块未命中：文案不得声称「已显示全文」', async () => {
    mockedContent.mockResolvedValueOnce(ok({ ...CONTENT, truncated: true }) as never)
    mockedChunk.mockRejectedValueOnce({ response: { status: 404 } })
    const { wrapper } = await mountPage('/knowledge/doc-1?chunk=missing')

    const miss = wrapper.find('[data-testid="document-focus-miss"]')
    expect(miss.exists()).toBe(true)
    expect(miss.text()).toContain('下载原文查看完整内容')
    expect(miss.text()).not.toContain('已显示全文')
  })

  it('403：显示无权访问，不泄露文档内容', async () => {
    // 先种下旧文档标题，确保断言不依赖前一个用例的残留状态。
    setPageTitleText('软件采购合同')
    mockedDetail.mockRejectedValueOnce({ response: { status: 403 } })
    const { wrapper } = await mountPage('/knowledge/doc-1')

    expect(wrapper.find('[data-testid="document-error"]').text()).toContain('无权访问该文档')
    expect(wrapper.text()).not.toContain('第三条 验收')
    // 异步失败不得保留上一篇文档名。
    expect(document.title).not.toContain('软件采购合同')
  })

  it('404：显示不存在或已删除', async () => {
    mockedDetail.mockRejectedValueOnce({ response: { status: 404 } })
    const { wrapper } = await mountPage('/knowledge/doc-1')

    expect(wrapper.find('[data-testid="document-error"]').text()).toContain('文档不存在或已删除')
  })

  it('同路由切换 documentId/chunk：重新取数并更新高亮', async () => {
    const { wrapper, router } = await mountPage('/knowledge/doc-1?chunk=chunk-1')

    mockedDetail.mockResolvedValueOnce(ok({ ...DOC, id: 'doc-2', title: '运维服务合同' }) as never)
    mockedContent.mockResolvedValueOnce(ok({
      ...CONTENT,
      id: 'doc-2',
      content: '第一条 服务范围\n\n第二条 季度付款',
      total_chunks: 2
    }) as never)
    mockedChunk.mockResolvedValueOnce(ok({
      ...CHUNK,
      chunk_id: 'chunk-9',
      text: '第二条 季度付款',
      section_title: '付款'
    }) as never)

    await router.push('/knowledge/doc-2?chunk=chunk-9')
    await flushPromises()
    await nextTick()

    expect(mockedDetail).toHaveBeenLastCalledWith('doc-2', { silent: true })
    expect(mockedChunk).toHaveBeenLastCalledWith('doc-2', 'chunk-9', { silent: true })
    expect(wrapper.find('[data-testid="document-title"]').text()).toContain('运维服务合同')
    expect(wrapper.find('[data-testid="document-focus"]').text()).toBe('第二条 季度付款')
  })

  it('legacy 原文与截断：必须可见', async () => {
    mockedContent.mockResolvedValueOnce(ok({
      ...CONTENT,
      text_provenance: 'legacy_unverified',
      truncated: true
    }) as never)
    const { wrapper } = await mountPage('/knowledge/doc-1')

    expect(wrapper.find('[data-testid="document-provenance"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="document-truncated"]').exists()).toBe(true)
  })
})

describe('文档深链页 · 操作', () => {
  it('复制链接写入当前深链', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true
    })
    const { wrapper } = await mountPage('/knowledge/doc-1?chunk=chunk-1')

    await wrapper.find('[data-testid="document-copy-link"]').trigger('click')
    await flushPromises()

    const copied = String(writeText.mock.calls[0]?.[0] || '')
    expect(copied).toContain('/knowledge/doc-1?chunk=chunk-1')
    expect(messageSpies.success).toHaveBeenCalled()  })

  it('下载：只打开通过校验的预签名 URL', async () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)
    mockedDownloadUrl.mockResolvedValueOnce(ok({
      downloadUrl: 'https://minio.example.com/bucket/file.pdf?X-Amz-Signature=abc',
      filename: '软件采购合同.pdf',
      expiresIn: 3600
    }) as never)
    const { wrapper } = await mountPage('/knowledge/doc-1')

    await wrapper.find('[data-testid="document-download"]').trigger('click')
    await flushPromises()

    expect(openSpy).toHaveBeenCalledTimes(1)
    expect(String(openSpy.mock.calls[0][0])).toContain('X-Amz-Signature=abc')
    openSpy.mockRestore()
  })

  it('下载：不可信 URL（javascript:）不打开并显示错误', async () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)
    mockedDownloadUrl.mockResolvedValueOnce(ok({
      downloadUrl: 'javascript:alert(1)',
      filename: 'x.pdf',
      expiresIn: 3600
    }) as never)
    const { wrapper } = await mountPage('/knowledge/doc-1')

    await wrapper.find('[data-testid="document-download"]').trigger('click')
    await flushPromises()

    expect(openSpy).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="document-download-error"]').exists()).toBe(true)
    openSpy.mockRestore()
  })
})

describe('文档深链页 · 国际化', () => {
  it('英文界面不回落中文来源/相时，单块文档用单数', async () => {
    const enI18n = createI18n({ legacy: false, locale: 'en', messages: { en }, missingWarn: false, fallbackWarn: false })
    mockedContent.mockResolvedValueOnce(ok({ ...CONTENT, total_chunks: 1 }) as never)
    const { wrapper } = await mountPage('/knowledge/doc-1', enI18n)

    expect(wrapper.text()).toContain('Knowledge base upload')
    expect(wrapper.text()).not.toContain('知识库上传')
    expect(wrapper.text()).toContain('1 chunk')
    expect(wrapper.text()).not.toContain('1 chunks')
    expect(wrapper.text()).not.toContain('小时前')
  })
})

describe('文档深链页 · 竞态与诚实性', () => {
  it('文档响应乱序：迟到的旧文档不得覆盖新文档', async () => {
    const { wrapper, router } = await mountPage('/knowledge/doc-1')
    const d2 = deferred<unknown>()
    const d3 = deferred<unknown>()
    const c2 = deferred<unknown>()
    const c3 = deferred<unknown>()
    mockedDetail.mockImplementation((id: string) => (
      id === 'doc-2' ? d2.promise : id === 'doc-3' ? d3.promise : Promise.resolve(ok(DOC))
    ) as never)
    mockedContent.mockImplementation((id: string) => (
      id === 'doc-2' ? c2.promise : id === 'doc-3' ? c3.promise : Promise.resolve(ok(CONTENT))
    ) as never)

    await router.push('/knowledge/doc-2')
    await nextTick()
    await router.push('/knowledge/doc-3')
    await nextTick()

    d3.resolve(ok({ ...DOC, id: 'doc-3', title: '新文档' }))
    c3.resolve(ok({ ...CONTENT, id: 'doc-3', content: '新文档正文', total_chunks: 1 }))
    await flushPromises()
    await nextTick()
    expect(wrapper.find('[data-testid="document-title"]').text()).toContain('新文档')

    d2.resolve(ok({ ...DOC, id: 'doc-2', title: '旧文档' }))
    c2.resolve(ok({ ...CONTENT, id: 'doc-2', content: '旧文档正文', total_chunks: 1 }))
    await flushPromises()
    await nextTick()
    expect(wrapper.find('[data-testid="document-title"]').text()).toContain('新文档')
    expect(wrapper.text()).not.toContain('旧文档正文')
  })

  it('分块响应乱序：迟到的 chunk 不得覆盖当前 chunk', async () => {
    mockedContent.mockResolvedValue(ok({ ...CONTENT, content: '甲\n\n乙\n\n丙' }) as never)
    const { wrapper, router } = await mountPage('/knowledge/doc-1')
    const c1 = deferred<unknown>()
    const c2 = deferred<unknown>()
    mockedChunk.mockImplementation((_id: string, chunkId: string) => (
      chunkId === 'c1' ? c1.promise : chunkId === 'c2' ? c2.promise : Promise.resolve(ok(CHUNK))
    ) as never)

    await router.push('/knowledge/doc-1?chunk=c1')
    await nextTick()
    await router.push('/knowledge/doc-1?chunk=c2')
    await nextTick()

    c2.resolve(ok({ ...CHUNK, chunk_id: 'c2', text: '乙', chunk_index: 1 }))
    await flushPromises()
    await nextTick()
    expect(wrapper.find('[data-testid="document-focus"]').text()).toBe('乙')

    c1.resolve(ok({ ...CHUNK, chunk_id: 'c1', text: '甲', chunk_index: 0 }))
    await flushPromises()
    await nextTick()
    expect(wrapper.find('[data-testid="document-focus"]').text()).toBe('乙')
  })

  it('chunk 成功但文本不在已加载正文：仍按未定位处理', async () => {
    mockedChunk.mockResolvedValueOnce(ok({ ...CHUNK, text: '不在正文里的片段' }) as never)
    const { wrapper } = await mountPage('/knowledge/doc-1?chunk=chunk-1')

    expect(wrapper.find('[data-testid="document-focus"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="document-focus-miss"]').exists()).toBe(true)
  })

  it('查看全文：清除定位、保留全文，且不重复请求', async () => {
    const { wrapper } = await mountPage('/knowledge/doc-1?chunk=chunk-1')
    expect(wrapper.find('[data-testid="document-focus"]').exists()).toBe(true)

    await wrapper.find('[data-testid="document-clear-focus"]').trigger('click')
    await flushPromises()
    await nextTick()

    expect(wrapper.find('[data-testid="document-focus"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('第三条 验收')
    expect(mockedDetail).toHaveBeenCalledTimes(1)
    expect(mockedContent).toHaveBeenCalledTimes(1)
    expect(mockedChunk).toHaveBeenCalledTimes(1)
  })
})

describe('文档深链页 · A→B→A 在途重取', () => {
  it('旧 chunk 在途时返回 A 会重新取块，迟到旧响应不覆盖', async () => {
    const { wrapper, router } = await mountPage('/knowledge/doc-1')
    const dB = deferred<unknown>()
    const cB = deferred<unknown>()
    const a1 = deferred<unknown>()
    const a2 = deferred<unknown>()
    let aCalls = 0
    mockedDetail.mockImplementation((id: string) => (
      id === 'doc-2' ? dB.promise : Promise.resolve(ok(DOC))
    ) as never)
    mockedContent.mockImplementation((id: string) => (
      id === 'doc-2' ? cB.promise : Promise.resolve(ok(CONTENT))
    ) as never)
    mockedChunk.mockImplementation((id: string, chunkId: string) => {
      if (id === 'doc-1' && chunkId === 'chunk-1') {
        aCalls += 1
        return (aCalls === 1 ? a1.promise : a2.promise) as never
      }
      return Promise.resolve(ok(CHUNK)) as never
    })

    await router.push('/knowledge/doc-1?chunk=chunk-1')
    await nextTick()
    await router.push('/knowledge/doc-2')
    await nextTick()
    await router.push('/knowledge/doc-1?chunk=chunk-1')
    await nextTick()
    await flushPromises()
    await nextTick()
    expect(aCalls).toBe(2)

    a2.resolve(ok(CHUNK))
    await flushPromises()
    await nextTick()
    expect(wrapper.find('[data-testid="document-focus"]').text()).toBe('第二条 付款 30 日')

    a1.resolve(ok({ ...CHUNK, text: '第一条 标的', chunk_index: 0 }))
    await flushPromises()
    await nextTick()
    expect(wrapper.find('[data-testid="document-focus"]').text()).toBe('第二条 付款 30 日')

    dB.resolve(ok({ ...DOC, id: 'doc-2', title: '文档 B' }))
    cB.resolve(ok({ ...CONTENT, id: 'doc-2', content: 'B 正文', total_chunks: 1 }))
    await flushPromises()
    await nextTick()
    expect(wrapper.find('[data-testid="document-title"]').text()).toContain('软件采购合同')
  })
})


describe('文档深链页 · 卸载与标题', () => {
  it('卸载后迟到的详情响应不改写标题', async () => {
    const pending = deferred<unknown>()
    mockedDetail.mockReturnValueOnce(pending.promise as never)
    mockedContent.mockResolvedValueOnce(ok(CONTENT) as never)
    const { wrapper } = await mountPage('/knowledge/doc-1')

    setPageTitleText('保留标题')
    wrapper.unmount()
    pending.resolve(ok(DOC))
    await flushPromises()
    await nextTick()

    expect(document.title).toContain('保留标题')
  })
})
