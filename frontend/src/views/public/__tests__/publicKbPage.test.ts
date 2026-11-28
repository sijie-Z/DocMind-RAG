/**
 * 公开知识库只读页 + 匿名聊天（批次十三/十七）判别用例。
 *
 * 变异点：
 * - 失效 token 不显示不可用提示 → 失效态用例红；
 * - 搜索结果渲染内部字段 → 内部字段用例红；
 * - 空结果显示为错误（或反之）→ 空态用例红；
 * - 搜索不传 token/top_k → 调用断言红；
 * - chat_enabled=false 仍展示聊天区 / 聊天不发 history / 预算错误不显示 → 对应用例红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { createMemoryHistory, createRouter } from 'vue-router'
import en from '@/locales/en'

const mocks = vi.hoisted(() => {
  class PublicKbChatError extends Error {
    status: number | null
    code: string
    detail: unknown

    constructor(
      code: string,
      message: string,
      options: { status?: number | null; detail?: unknown } = {},
    ) {
      super(message)
      this.name = 'PublicKbChatError'
      this.code = code
      this.status = options.status ?? null
      this.detail = options.detail ?? null
    }
  }
  return {
    get: vi.fn(),
    search: vi.fn(),
    chat: vi.fn(),
    PublicKbChatError,
  }
})

vi.mock('@/api/publicKb', () => ({
  publicKbPublicApi: { get: mocks.get, search: mocks.search, chat: mocks.chat },
  publicKbApi: {},
  PublicKbChatError: mocks.PublicKbChatError,
}))

import PublicKbPage from '../kb.vue'

const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })

async function mountPage(
  token = 'token-1',
  query: Record<string, string> = {},
) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/public/kb/:token', component: { template: '<div />' } }],
  })
  await router.push({ path: `/public/kb/${token}`, query })
  await router.isReady()
  const wrapper = mount(PublicKbPage, { global: { plugins: [i18n, router] } })
  return { wrapper, router }
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.get.mockResolvedValue({
    title: 'Public policy KB',
    description: 'Read-only policies',
    expires_at: null,
    chat_enabled: false,
  })
  mocks.search.mockResolvedValue({
    results: [
      { rank: 1, title: 'Policy A', snippet: 'First clause text', score: 0.912 },
      { rank: 2, title: 'Policy B', snippet: 'Second clause text', score: 0.5 },
    ],
    total: 2,
  })
  mocks.chat.mockImplementation(async (_token, _payload, handlers) => {
    handlers?.onSources?.([
      { rank: 1, title: 'Policy A', snippet: 'First clause text', score: 0.9 },
    ])
    handlers?.onDelta?.('Based on ')
    handlers?.onDelta?.('the policy.')
    handlers?.onDone?.({ done: true, source_count: 1 })
  })
})

function enableChat() {
  mocks.get.mockResolvedValue({
    title: 'Public policy KB',
    description: 'Read-only policies',
    expires_at: null,
    chat_enabled: true,
  })
}

describe('public KB page', () => {
  it('匿名按 token 拉取元数据并展示标题/描述', async () => {
    const { wrapper } = await mountPage()
    await flushPromises()
    expect(mocks.get).toHaveBeenCalledWith('token-1')
    expect(wrapper.text()).toContain('Public policy KB')
    expect(wrapper.text()).toContain('Read-only policies')
    expect(wrapper.find('[data-testid="public-kb-unavailable"]').exists()).toBe(false)
  })

  it('搜索：提交 query/top_k，渲染 rank/title/snippet/score', async () => {
    const { wrapper } = await mountPage()
    await flushPromises()
    await wrapper.find('[data-testid="public-kb-search-input"]').setValue('policy')
    await wrapper.find('form.public-kb-search').trigger('submit')
    await flushPromises()

    expect(mocks.search).toHaveBeenCalledWith('token-1', 'policy', 10)
    expect(wrapper.find('[data-testid="public-kb-result-1"]').text()).toContain('Policy A')
    expect(wrapper.find('[data-testid="public-kb-result-1"]').text()).toContain('First clause text')
    expect(wrapper.find('[data-testid="public-kb-result-1"]').text()).toContain('0.912')
    expect(wrapper.text()).toContain('2 result(s)')
  })

  it('空结果：显示空态而不是错误', async () => {
    mocks.search.mockResolvedValue({ results: [], total: 0 })
    const { wrapper } = await mountPage()
    await flushPromises()
    await wrapper.find('[data-testid="public-kb-search-input"]').setValue('nothing')
    await wrapper.find('form.public-kb-search').trigger('submit')
    await flushPromises()
    expect(wrapper.find('[data-testid="public-kb-empty"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="public-kb-result-1"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="public-kb-unavailable"]').exists()).toBe(false)
  })

  it('失效/撤销/过期 token：元数据 404 → 统一不可用提示', async () => {
    mocks.get.mockRejectedValue({ response: { status: 404 } })
    const { wrapper } = await mountPage()
    await flushPromises()
    expect(wrapper.find('[data-testid="public-kb-unavailable"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('does not exist or has expired')
    expect(wrapper.find('[data-testid="public-kb-search-input"]').exists()).toBe(false)
  })

  it('检索期间链接被撤销（search 404）也按不可用处理', async () => {
    mocks.search.mockRejectedValue({ response: { status: 404 } })
    const { wrapper } = await mountPage()
    await flushPromises()
    await wrapper.find('[data-testid="public-kb-search-input"]').setValue('policy')
    await wrapper.find('form.public-kb-search').trigger('submit')
    await flushPromises()
    expect(wrapper.find('[data-testid="public-kb-unavailable"]').exists()).toBe(true)
  })

  it('元数据 5xx/网络错误 → 可重试错误态（不谎称链接失效），重试可恢复', async () => {
    mocks.get.mockRejectedValueOnce({ response: { status: 502 } })
    const { wrapper } = await mountPage()
    await flushPromises()
    expect(wrapper.find('[data-testid="public-kb-load-error"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="public-kb-unavailable"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('Search failed, please retry later')

    await wrapper.find('[data-testid="public-kb-retry"]').trigger('click')
    await flushPromises()
    expect(mocks.get).toHaveBeenCalledTimes(2)
    expect(wrapper.find('[data-testid="public-kb-load-error"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('Public policy KB')
  })

  it('检索 5xx/429/网络错误 → 保留搜索框并显示可重试错误，不切到失效态', async () => {
    mocks.search.mockRejectedValue({ response: { status: 502 } })
    const { wrapper } = await mountPage()
    await flushPromises()
    await wrapper.find('[data-testid="public-kb-search-input"]').setValue('policy')
    await wrapper.find('form.public-kb-search').trigger('submit')
    await flushPromises()
    expect(wrapper.find('[data-testid="public-kb-search-error"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="public-kb-unavailable"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="public-kb-search-input"]').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('does not exist or has expired')
  })

  it('只渲染公开字段：内部 id/路径/组织即使出现在响应里也不展示', async () => {
    mocks.get.mockResolvedValue({
      title: 'Public policy KB',
      description: 'Read-only policies',
      expires_at: null,
      organization_id: 991,
      knowledge_base_id: 'kb-internal-1',
      document_id: 'doc-internal-2',
    })
    mocks.search.mockResolvedValue({
      results: [
        {
          rank: 1,
          title: 'Policy A',
          snippet: 'First clause text',
          score: 0.9,
          document_id: 'doc-internal-2',
          chunk_id: 'chunk-internal-3',
          file_path: '/secret/path.pdf',
          uploaded_by: 77,
        },
      ],
      total: 1,
    })
    const { wrapper } = await mountPage()
    await flushPromises()
    await wrapper.find('[data-testid="public-kb-search-input"]').setValue('policy')
    await wrapper.find('form.public-kb-search').trigger('submit')
    await flushPromises()
    const text = wrapper.text()
    for (const forbidden of [
      'kb-internal-1',
      'doc-internal-2',
      'chunk-internal-3',
      '/secret/path.pdf',
      '991',
      '77',
    ]) {
      expect(text).not.toContain(forbidden)
    }
  })
})

describe('public KB anonymous chat (batch 17)', () => {
  it('meta.chat_enabled=false：不渲染聊天区', async () => {
    const { wrapper } = await mountPage()
    await flushPromises()
    expect(wrapper.find('[data-testid="public-kb-chat"]').exists()).toBe(false)
    expect(mocks.chat).not.toHaveBeenCalled()
  })

  it('meta.chat_enabled=true：展示聊天区并可流式提问（sources/delta/历史）', async () => {
    enableChat()
    const { wrapper } = await mountPage()
    await flushPromises()
    expect(wrapper.find('[data-testid="public-kb-chat"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="public-kb-chat-empty"]').exists()).toBe(true)

    await wrapper.find('[data-testid="public-kb-chat-input"]').setValue('policy?')
    await wrapper.find('form.public-kb-chat-form').trigger('submit')
    await flushPromises()

    expect(mocks.chat).toHaveBeenCalledTimes(1)
    const [token, payload, handlers, options] = mocks.chat.mock.calls[0]
    expect(token).toBe('token-1')
    expect(payload).toEqual({ message: 'policy?', history: [] })
    expect(typeof handlers.onDelta).toBe('function')
    expect(options.signal).toBeInstanceOf(AbortSignal)

    const text = wrapper.text()
    expect(text).toContain('policy?')
    expect(text).toContain('Based on the policy.')
    expect(text).toContain('Sources')
    expect(text).toContain('First clause text')
    expect((wrapper.find('[data-testid="public-kb-chat-input"]').element as HTMLInputElement).value).toBe('')

    // 第二轮携带上一轮 user/assistant 历史（服务端不持久化）。
    await wrapper.find('[data-testid="public-kb-chat-input"]').setValue('second?')
    await wrapper.find('form.public-kb-chat-form').trigger('submit')
    await flushPromises()
    expect(mocks.chat.mock.calls[1][1]).toEqual({
      message: 'second?',
      history: [
        { role: 'user', content: 'policy?' },
        { role: 'assistant', content: 'Based on the policy.' },
      ],
    })
  })

  it('预算错误 429 → 展示 used/budget 文案（不是笼统网络错误）', async () => {
    mocks.chat.mockRejectedValue(
      new mocks.PublicKbChatError('PUBLIC_KB_CHAT_BUDGET_EXCEEDED', 'budget', {
        status: 429,
        detail: { used: 5, budget: 5, reset_at: '2026-11-29T00:00:00Z' },
      }),
    )
    enableChat()
    const { wrapper } = await mountPage()
    await flushPromises()
    await wrapper.find('[data-testid="public-kb-chat-input"]').setValue('policy?')
    await wrapper.find('form.public-kb-chat-form').trigger('submit')
    await flushPromises()

    const error = wrapper.find('[data-testid="public-kb-chat-error"]')
    expect(error.exists()).toBe(true)
    expect(error.text()).toContain('5/5')
    expect(error.text().toLowerCase()).toContain('budget')
    expect(wrapper.find('[data-testid="public-kb-chat-input"]').exists()).toBe(true)
  })

  it('聊天端点 404 → 聊天不可用提示，但保留检索区（不误判整页失效）', async () => {
    mocks.chat.mockRejectedValue(
      new mocks.PublicKbChatError('NOT_FOUND', 'missing', { status: 404, detail: null }),
    )
    enableChat()
    const { wrapper } = await mountPage()
    await flushPromises()
    await wrapper.find('[data-testid="public-kb-chat-input"]').setValue('policy?')
    await wrapper.find('form.public-kb-chat-form').trigger('submit')
    await flushPromises()

    expect(wrapper.find('[data-testid="public-kb-chat-error"]').text()).toContain(
      'Chat is not available',
    )
    expect(wrapper.find('[data-testid="public-kb-unavailable"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="public-kb-search-input"]').exists()).toBe(true)
  })

  it('停止按钮中止请求：保留已生成内容、不显示错误', async () => {
    let signal: AbortSignal | undefined
    mocks.chat.mockImplementation((_token, _payload, handlers, options) => {
      handlers.onDelta('partial')
      signal = options.signal
      return new Promise((_resolve, reject) => {
        options.signal.addEventListener('abort', () => {
          reject(new DOMException('aborted', 'AbortError'))
        })
      })
    })
    enableChat()
    const { wrapper } = await mountPage()
    await flushPromises()
    await wrapper.find('[data-testid="public-kb-chat-input"]').setValue('policy?')
    await wrapper.find('form.public-kb-chat-form').trigger('submit')
    await flushPromises()

    expect(wrapper.find('[data-testid="public-kb-chat-stop"]').exists()).toBe(true)
    await wrapper.find('[data-testid="public-kb-chat-stop"]').trigger('click')
    await flushPromises()

    expect(signal?.aborted).toBe(true)
    expect(wrapper.find('[data-testid="public-kb-chat-stop"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="public-kb-chat-error"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('partial')
  })

  it('引用只渲染公开字段：内部 id/path/org 即使出现在响应里也不展示', async () => {
    mocks.chat.mockImplementation(async (_token, _payload, handlers) => {
      handlers.onSources([
        {
          rank: 1,
          title: 'Policy A',
          snippet: 'First clause text',
          score: 0.9,
          document_id: 'doc-internal-2',
          chunk_id: 'chunk-internal-3',
          file_path: '/secret/path.pdf',
          organization_id: 991,
        },
      ])
      handlers.onDelta('answer')
      handlers.onDone({ done: true, source_count: 1 })
    })
    enableChat()
    const { wrapper } = await mountPage()
    await flushPromises()
    await wrapper.find('[data-testid="public-kb-chat-input"]').setValue('policy?')
    await wrapper.find('form.public-kb-chat-form').trigger('submit')
    await flushPromises()

    const text = wrapper.text()
    for (const forbidden of [
      'doc-internal-2',
      'chunk-internal-3',
      '/secret/path.pdf',
      '991',
    ]) {
      expect(text).not.toContain(forbidden)
    }
  })
})

describe('public KB chat edge cases (review hardening)', () => {
  it('长回答截断到 2000 字符后再作为历史发送（否则后端 400）', async () => {
    mocks.chat.mockImplementation(async (_token, _payload, handlers) => {
      handlers.onDelta('x'.repeat(2500))
      handlers.onDone({ done: true, source_count: 0 })
    })
    enableChat()
    const { wrapper } = await mountPage()
    await flushPromises()
    await wrapper.find('[data-testid="public-kb-chat-input"]').setValue('q1')
    await wrapper.find('form.public-kb-chat-form').trigger('submit')
    await flushPromises()
    await wrapper.find('[data-testid="public-kb-chat-input"]').setValue('q2')
    await wrapper.find('form.public-kb-chat-form').trigger('submit')
    await flushPromises()

    const history = mocks.chat.mock.calls[1][1].history as Array<{
      role: string
      content: string
    }>
    const assistant = history.find((item) => item.role === 'assistant')
    expect(assistant?.content.length).toBe(2000)
  })

  it('零命中 done：保留助手气泡并显示“没有相关内容”', async () => {
    mocks.chat.mockImplementation(async (_token, _payload, handlers) => {
      handlers.onSources([])
      handlers.onDone({ done: true, source_count: 0 })
    })
    enableChat()
    const { wrapper } = await mountPage()
    await flushPromises()
    await wrapper.find('[data-testid="public-kb-chat-input"]').setValue('out of scope')
    await wrapper.find('form.public-kb-chat-form').trigger('submit')
    await flushPromises()

    expect(wrapper.find('[data-testid="public-kb-chat-no-answer"]').text()).toContain(
      'No relevant information',
    )
    expect(wrapper.find('[data-testid="public-kb-chat-msg-1"]').exists()).toBe(true)
  })

  it('首 token 前停止（已有 sources）：显示“已停止”，不谎称无相关内容', async () => {
    let signal: AbortSignal | undefined
    mocks.chat.mockImplementation((_token, _payload, handlers, options) => {
      handlers.onSources([{ rank: 1, title: 'Policy A', snippet: 'text', score: 0.9 }])
      signal = options.signal
      return new Promise((_resolve, reject) => {
        options.signal.addEventListener('abort', () => {
          reject(new DOMException('aborted', 'AbortError'))
        })
      })
    })
    enableChat()
    const { wrapper } = await mountPage()
    await flushPromises()
    await wrapper.find('[data-testid="public-kb-chat-input"]').setValue('q')
    await wrapper.find('form.public-kb-chat-form').trigger('submit')
    await flushPromises()
    await wrapper.find('[data-testid="public-kb-chat-stop"]').trigger('click')
    await flushPromises()

    expect(signal?.aborted).toBe(true)
    expect(wrapper.find('[data-testid="public-kb-chat-stopped"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="public-kb-chat-no-answer"]').exists()).toBe(false)
  })

  it('SSE error 回调：显示错误并移除空助手气泡', async () => {
    mocks.chat.mockImplementation(async (_token, _payload, handlers) => {
      handlers.onError(
        new mocks.PublicKbChatError('PUBLIC_KB_CHAT_GENERATION_FAILED', 'failed'),
      )
    })
    enableChat()
    const { wrapper } = await mountPage()
    await flushPromises()
    await wrapper.find('[data-testid="public-kb-chat-input"]').setValue('q')
    await wrapper.find('form.public-kb-chat-form').trigger('submit')
    await flushPromises()

    expect(wrapper.find('[data-testid="public-kb-chat-error"]').exists()).toBe(true)
    // 只剩用户消息；失败的空助手卡片被移除。
    expect(wrapper.find('[data-testid="public-kb-chat-msg-1"]').exists()).toBe(false)
  })

  it('限流错误显示可重试文案', async () => {
    mocks.chat.mockRejectedValue(
      new mocks.PublicKbChatError('RATE_LIMIT_EXCEEDED', 'slow down', { status: 429 }),
    )
    enableChat()
    const { wrapper } = await mountPage()
    await flushPromises()
    await wrapper.find('[data-testid="public-kb-chat-input"]').setValue('q')
    await wrapper.find('form.public-kb-chat-form').trigger('submit')
    await flushPromises()
    expect(wrapper.find('[data-testid="public-kb-chat-error"]').text()).toContain(
      'Too many questions',
    )
  })

  it('组件卸载中止在途请求（离开页面不再后台消耗预算）', async () => {
    let signal: AbortSignal | undefined
    mocks.chat.mockImplementation((_token, _payload, _handlers, options) => {
      signal = options.signal
      return new Promise(() => undefined)
    })
    enableChat()
    const { wrapper } = await mountPage()
    await flushPromises()
    await wrapper.find('[data-testid="public-kb-chat-input"]').setValue('q')
    await wrapper.find('form.public-kb-chat-form').trigger('submit')
    await flushPromises()
    wrapper.unmount()
    expect(signal?.aborted).toBe(true)
  })
})

describe('public KB chat generation guard (review follow-up)', () => {
  it('token 切换后旧请求回调不写入已清空数组（无 TypeError、无残留消息）', async () => {
    let oldHandlers: {
      onSources?: (v: unknown[]) => void
      onDelta?: (v: string) => void
      onDone?: (v: unknown) => void
    } = {}
    let oldSignal: AbortSignal | undefined
    mocks.chat.mockImplementation((_token, _payload, handlers, options) => {
      oldHandlers = handlers
      oldSignal = options.signal
      return new Promise((_resolve, reject) => {
        options.signal.addEventListener('abort', () => {
          reject(new DOMException('aborted', 'AbortError'))
        })
      })
    })
    enableChat()
    const { wrapper, router } = await mountPage('token-1')
    await flushPromises()
    await wrapper.find('[data-testid="public-kb-chat-input"]').setValue('q')
    await wrapper.find('form.public-kb-chat-form').trigger('submit')
    await flushPromises()

    mocks.get.mockResolvedValue({
      title: 'Second KB',
      description: null,
      expires_at: null,
      chat_enabled: true,
    })
    await router.push('/public/kb/token-2')
    await flushPromises()
    expect(oldSignal?.aborted).toBe(true)

    // 旧请求迟到的回调必须被代际保护静默丢弃。
    expect(() => oldHandlers.onDelta?.('late')).not.toThrow()
    expect(() => oldHandlers.onDone?.({ done: true })).not.toThrow()
    expect(() => oldHandlers.onSources?.([{ rank: 1 }])).not.toThrow()
    await flushPromises()

    expect(wrapper.text()).toContain('Second KB')
    expect(wrapper.find('[data-testid="public-kb-chat-empty"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="public-kb-chat-error"]').exists()).toBe(false)
  })
})

describe('public KB embed mode (batch 18)', () => {
  it('embed=1 隐藏品牌与页脚，保留标题、检索与聊天', async () => {
    enableChat()
    const { wrapper } = await mountPage('token-1', { embed: '1' })
    await flushPromises()

    expect(wrapper.find('.public-kb-page').classes()).toContain('is-embed')
    expect(wrapper.find('.public-kb-page').attributes('data-embed')).toBe('1')
    expect(wrapper.find('[data-testid="public-kb-brand"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="public-kb-page-footer"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('Public policy KB')
    expect(wrapper.find('[data-testid="public-kb-search-input"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="public-kb-chat"]').exists()).toBe(true)
  })

  it('普通模式保持品牌与页脚（嵌入分支不得影响默认外观）', async () => {
    const { wrapper } = await mountPage()
    await flushPromises()

    expect(wrapper.find('.public-kb-page').classes()).not.toContain('is-embed')
    expect(wrapper.find('.public-kb-page').attributes('data-embed')).toBeUndefined()
    expect(wrapper.find('[data-testid="public-kb-brand"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="public-kb-page-footer"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('Powered by DocMind')
  })

  it('embed=1 但 chat_enabled=false 时不渲染聊天区', async () => {
    const { wrapper } = await mountPage('token-1', { embed: '1' })
    await flushPromises()

    expect(wrapper.find('.public-kb-page').classes()).toContain('is-embed')
    expect(wrapper.find('[data-testid="public-kb-chat"]').exists()).toBe(false)
  })

  it('非 1 的 embed 值不启用嵌入模式', async () => {
    const { wrapper } = await mountPage('token-1', { embed: '0' })
    await flushPromises()

    expect(wrapper.find('.public-kb-page').classes()).not.toContain('is-embed')
    expect(wrapper.find('[data-testid="public-kb-brand"]').exists()).toBe(true)
  })
})

describe('public KB token hygiene (batch 18 review M2)', () => {
  it('token 不进入 document.title', async () => {
    const token = 'tok-title-secret-123'
    document.title = 'DocMind'
    const { wrapper } = await mountPage(token)
    await flushPromises()

    expect(wrapper.text()).toContain('Public policy KB')
    expect(document.title).not.toContain(token)
  })

  it('load/chat 路径不把 token 输出到 console.log/warn/error', async () => {
    const token = 'tok-console-secret-456'
    const spies = [
      vi.spyOn(console, 'log').mockImplementation(() => {}),
      vi.spyOn(console, 'warn').mockImplementation(() => {}),
      vi.spyOn(console, 'error').mockImplementation(() => {}),
    ]
    try {
      enableChat()
      const { wrapper } = await mountPage(token)
      await flushPromises()
      await wrapper.find('[data-testid="public-kb-chat-input"]').setValue('console hygiene?')
      await wrapper.find('form.public-kb-chat-form').trigger('submit')
      await flushPromises()

      const output = spies
        .flatMap((spy) => spy.mock.calls.map((args) => args.map((arg) => String(arg)).join(' ')))
        .join('\n')
      expect(output).not.toContain(token)
    } finally {
      spies.forEach((spy) => spy.mockRestore())
    }
  })
})
