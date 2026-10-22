/**
 * 帮助中心（使用文档）—— 页面行为判别：
 * 目录分组渲染、站内搜索过滤（标题/正文/技术标识符/四语标题）、页内锚点
 * （标题 id + 目录跳转 + URL 锚点 + 深链滚动 + 重复标题）、上一篇/下一篇、
 * 移动端目录抽屉（含 Escape/焦点/滚动锁）、无内容空态、内容缺失错误态、XSS 消毒。
 *
 * 变异点：
 * - 搜索只匹配标题或只匹配正文 → 对应用例红；
 * - 剥离 Markdown 时删掉 `_`/`-` 或内联代码 → 技术标识符搜索用例红；
 * - 渲染后不给标题补 id / 不重建目录 → 锚点与切文档用例红；
 * - 滚动到错误标题 → 滚动目标用例红；
 * - 移动端抽屉点选后不关闭 / Escape 不生效 / 不加滚动锁 → 抽屉用例红；
 * - docs 为空仍渲染首篇 → 空态用例红；
 * - 绕过 markdownRenderer/DOMPurify 直接 v-html 原文 → XSS 用例红。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import DOMPurify from 'dompurify'
import zh from '@/locales/zh'
import en from '@/locales/en'
import HelpCenter from '@/views/systemHelp/index.vue'

interface FixtureDoc {
  id: string
  group: 'gettingStarted' | 'concepts' | 'guides' | 'faq' | 'troubleshooting'
  titleKey: string
  content: string
}

const DOCS: FixtureDoc[] = [
  {
    id: 'register-login',
    group: 'gettingStarted',
    titleKey: 'help.center.docs.registerLogin.title',
    content: [
      '## 第一步：注册',
      '',
      '前往登录页，点击「去注册」创建账号。',
      '',
      '## 第二步：登录',
      '',
      '登录成功后访问令牌会自动续期，登录状态可保持。',
    ].join('\n'),
  },
  {
    id: 'chat',
    group: 'guides',
    titleKey: 'help.center.docs.chat.title',
    content: [
      '## 快速问答',
      '',
      '在输入框提问；需要长文档分析时可上传附件。',
    ].join('\n'),
  },
]

const i18n = createI18n({
  legacy: false,
  locale: 'zh',
  fallbackLocale: 'zh',
  messages: { zh, en },
})

let scrollSpy: ReturnType<typeof vi.fn>

async function makeRouter(initial = '/system-help') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/system-help', name: 'SystemHelp', component: { template: '<div />' } },
      { path: '/chat', name: 'Chat', component: { template: '<div />' } },
    ],
  })
  await router.push(initial)
  await router.isReady()
  return router
}

async function mountPage(docs: FixtureDoc[] = DOCS, initial?: string, attach = false) {
  const router = await makeRouter(initial)
  const wrapper = mount(HelpCenter, {
    props: { docs },
    attachTo: attach ? document.body : undefined,
    global: { plugins: [i18n, router, createPinia()] },
  })
  await nextTick()
  await nextTick()
  return { wrapper, router }
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'zh'
  scrollSpy = vi.fn()
  Element.prototype.scrollIntoView = scrollSpy as unknown as typeof Element.prototype.scrollIntoView
  document.body.style.overflow = ''
})

afterEach(() => {
  i18n.global.locale.value = 'zh'
})

describe('帮助中心 · 目录与内容', () => {
  it('默认加载真实文档注册表：首篇渲染且侧栏含全部五个分组', async () => {
    const router = await makeRouter()
    const wrapper = mount(HelpCenter, { global: { plugins: [i18n, router, createPinia()] } })
    await nextTick()
    await nextTick()
    expect(wrapper.find('[data-testid="help-doc-register-login"]').exists()).toBe(true)
    const content = wrapper.find('[data-testid="help-content"]')
    expect(content.text()).toContain('登录状态如何保持')
    for (const group of ['gettingStarted', 'concepts', 'guides', 'faq', 'troubleshooting']) {
      expect(wrapper.find(`[data-testid="help-group-${group}"]`).exists(), `缺少分组 ${group}`).toBe(true)
    }
    wrapper.unmount()
  })

  it('渲染分组名与文档标题，默认展示第一篇的 Markdown 内容', async () => {
    const { wrapper } = await mountPage()
    expect(wrapper.find('[data-testid="help-center"]').exists()).toBe(true)
    // 分组标题（四语词条）
    expect(wrapper.text()).toContain('快速开始')
    expect(wrapper.text()).toContain('功能指南')
    // 文档标题
    expect(wrapper.find('[data-testid="help-doc-register-login"]').text()).toContain('注册与登录')
    expect(wrapper.find('[data-testid="help-doc-chat"]').text()).toContain('对话页使用指南')
    // 正文经 markdown 渲染为标题/段落
    const content = wrapper.find('[data-testid="help-content"]')
    expect(content.find('h2').text()).toBe('第一步：注册')
    expect(content.text()).toContain('前往登录页')
    wrapper.unmount()
  })

  it('分组可折叠：点击分组标题后该组文档项隐藏', async () => {
    const { wrapper } = await mountPage()
    await wrapper.find('[data-testid="help-group-gettingStarted"]').trigger('click')
    await nextTick()
    expect(wrapper.find('[data-testid="help-doc-register-login"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="help-doc-chat"]').exists()).toBe(true)
    wrapper.unmount()
  })
})

describe('帮助中心 · 站内搜索', () => {
  it('按正文关键词过滤：只保留命中文档', async () => {
    const { wrapper } = await mountPage()
    await wrapper.find('[data-testid="help-search"] input').setValue('附件')
    await nextTick()
    expect(wrapper.find('[data-testid="help-doc-chat"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="help-doc-register-login"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="help-search-count"]').text()).toContain('1')
    wrapper.unmount()
  })

  it('按标题关键词过滤：只出现在标题里的词也能搜到', async () => {
    const { wrapper } = await mountPage()
    // 「使用指南」只出现在 chat 标题中，正文没有；若实现只看正文，本用例会红
    await wrapper.find('[data-testid="help-search"] input').setValue('使用指南')
    await nextTick()
    expect(wrapper.find('[data-testid="help-doc-chat"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="help-doc-register-login"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('技术标识符可搜索：内联代码中的 _ 与 - 不被剥离', async () => {
    const docs: FixtureDoc[] = [
      {
        id: 'chat',
        group: 'guides',
        titleKey: 'help.center.docs.chat.title',
        content: [
          '## 缓存',
          '',
          '语义缓存由 `SEMANTIC_CACHE_ANSWER_ENABLED` 控制，请求头是 `X-Request-ID`。',
        ].join('\n'),
      },
    ]
    const { wrapper } = await mountPage(docs)
    await wrapper.find('[data-testid="help-search"] input').setValue('SEMANTIC_CACHE_ANSWER_ENABLED')
    await nextTick()
    expect(wrapper.find('[data-testid="help-doc-chat"]').exists()).toBe(true)
    await wrapper.find('[data-testid="help-search"] input').setValue('X-Request-ID')
    await nextTick()
    expect(wrapper.find('[data-testid="help-doc-chat"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('英文界面按本地化标题过滤', async () => {
    const { wrapper } = await mountPage()
    i18n.global.locale.value = 'en'
    await nextTick()
    await wrapper.find('[data-testid="help-search"] input').setValue('Registration')
    await nextTick()
    expect(wrapper.find('[data-testid="help-doc-register-login"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="help-doc-chat"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('无匹配时展示诚实的空结果提示', async () => {
    const { wrapper } = await mountPage()
    await wrapper.find('[data-testid="help-search"] input').setValue('不存在的关键词xyz')
    await nextTick()
    expect(wrapper.find('[data-testid="help-search-empty"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('没有匹配的文档')
    wrapper.unmount()
  })

  it('搜索过滤后上/下一篇跟随可见列表（只剩一篇时两端禁用）', async () => {
    const { wrapper } = await mountPage()
    await wrapper.find('[data-testid="help-search"] input').setValue('附件')
    await nextTick()
    expect(wrapper.find('[data-testid="help-prev"]').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-testid="help-next"]').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })
})

describe('帮助中心 · 页内锚点', () => {
  it('标题补 id，目录列出二级标题；点击目录项滚动到目标并写入 URL 锚点', async () => {
    const { wrapper, router } = await mountPage()
    const headings = wrapper.find('[data-testid="help-content"]').findAll('h2')
    expect(headings.length).toBe(2)
    const firstId = headings[0].attributes('id')
    expect(firstId).toBeTruthy()

    const toc = wrapper.find('[data-testid="help-toc"]')
    expect(toc.exists()).toBe(true)
    const tocLinks = toc.findAll('a')
    expect(tocLinks.length).toBe(2)
    expect(tocLinks[0].text()).toBe('第一步：注册')
    expect(decodeURIComponent(tocLinks[0].attributes('href') ?? '')).toContain(`anchor=${firstId}`)

    await tocLinks[1].trigger('click')
    await flushPromises()
    await nextTick()
    expect(router.currentRoute.value.query.anchor).toBe(headings[1].attributes('id'))
    // 必须滚动到第二个标题，而不是任意元素
    const contexts = (scrollSpy.mock as unknown as { contexts: unknown[] }).contexts
    expect(contexts.at(-1)).toBe(headings[1].element)
    wrapper.unmount()
  })

  it('通过 URL 深链带 doc + anchor 打开时选中对应文档并滚动到锚点', async () => {
    const { wrapper } = await mountPage(DOCS, '/system-help?doc=chat&anchor=快速问答')
    expect(wrapper.find('[data-testid="help-content"]').text()).toContain('上传附件')
    expect(wrapper.find('[data-testid="help-toc"]').text()).toContain('快速问答')
    expect(scrollSpy).toHaveBeenCalled()
    wrapper.unmount()
  })

  it('切换文档后重建目录；重复标题生成唯一 id（含 Foo/Foo/Foo-2 冲突）', async () => {
    const docs: FixtureDoc[] = [
      {
        id: 'register-login',
        group: 'gettingStarted',
        titleKey: 'help.center.docs.registerLogin.title',
        content: ['## Alpha', '', 'A', '', '## Beta', '', 'B'].join('\n'),
      },
      {
        id: 'chat',
        group: 'guides',
        titleKey: 'help.center.docs.chat.title',
        content: ['## Foo', '', '1', '', '## Foo', '', '2', '', '## Foo-2', '', '3'].join('\n'),
      },
    ]
    const { wrapper } = await mountPage(docs)
    expect(wrapper.find('[data-testid="help-toc"]').text()).toContain('Alpha')
    await wrapper.find('[data-testid="help-next"]').trigger('click')
    await flushPromises()
    await nextTick()
    await nextTick()
    const ids = wrapper
      .find('[data-testid="help-content"]')
      .findAll('h2')
      .map((heading) => heading.attributes('id'))
    expect(ids).toEqual(['foo', 'foo-2', 'foo-2-2'])
    expect(wrapper.find('[data-testid="help-toc"]').text()).toContain('Foo-2')
    expect(wrapper.find('[data-testid="help-toc"]').text()).not.toContain('Alpha')
    // 回到上一篇，确认 Markdown 缓存路径下仍会重新标注标题
    await wrapper.find('[data-testid="help-prev"]').trigger('click')
    await flushPromises()
    await nextTick()
    await nextTick()
    const alphaId = wrapper.find('[data-testid="help-content"] h2').attributes('id')
    expect(alphaId).toBe('alpha')
    wrapper.unmount()
  })

  it('三级标题进入目录并缩进', async () => {
    const docs: FixtureDoc[] = [
      {
        id: 'chat',
        group: 'guides',
        titleKey: 'help.center.docs.chat.title',
        content: ['## 一级', '', '### 二级', '', '正文'].join('\n'),
      },
    ]
    const { wrapper } = await mountPage(docs)
    const items = wrapper.find('[data-testid="help-toc"]').findAll('li')
    expect(items.length).toBe(2)
    expect(items[0].attributes('style') ?? '').not.toContain('1rem')
    expect(items[1].attributes('style') ?? '').toContain('padding-left: 1rem')
    wrapper.unmount()
  })
})

describe('帮助中心 · 上一篇/下一篇', () => {
  it('首篇上一篇禁用；下一篇切到第二篇并同步 URL', async () => {
    const { wrapper, router } = await mountPage()
    expect(wrapper.find('[data-testid="help-prev"]').attributes('disabled')).toBeDefined()
    await wrapper.find('[data-testid="help-next"]').trigger('click')
    await flushPromises()
    await nextTick()
    expect(wrapper.find('[data-testid="help-content"]').text()).toContain('上传附件')
    expect(router.currentRoute.value.query.doc).toBe('chat')
    expect(wrapper.find('[data-testid="help-next"]').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })
})

describe('帮助中心 · 移动端目录', () => {
  it('点击目录按钮打开抽屉，抽屉内点选文档后关闭并切换内容', async () => {
    const { wrapper } = await mountPage()
    expect(wrapper.find('[data-testid="help-mobile-nav"]').exists()).toBe(false)
    await wrapper.find('[data-testid="help-nav-toggle"]').trigger('click')
    await nextTick()
    const drawer = wrapper.find('[data-testid="help-mobile-nav"]')
    expect(drawer.exists()).toBe(true)
    await drawer.find('[data-testid="help-doc-chat"]').trigger('click')
    await nextTick()
    expect(wrapper.find('[data-testid="help-mobile-nav"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="help-content"]').text()).toContain('上传附件')
    wrapper.unmount()
  })

  it('抽屉可通过关闭按钮、遮罩与 Escape 关闭；打开时锁定背景滚动并聚焦对话框', async () => {
    const { wrapper } = await mountPage(DOCS, '/system-help', true)
    const toggle = wrapper.find('[data-testid="help-nav-toggle"]')
    expect(toggle.attributes('aria-expanded')).toBe('false')

    await toggle.trigger('click')
    await nextTick()
    expect(toggle.attributes('aria-expanded')).toBe('true')
    expect(document.body.style.overflow).toBe('hidden')
    // 真正的滚动容器（页面根节点）也要被锁住，body 锁在 h-screen 布局里不生效
    expect(wrapper.find('[data-testid="help-center"]').attributes('style') ?? '').toContain('overflow: hidden')
    expect(wrapper.find('[data-testid="help-main"]').classes()).toContain('lg:col-span-2')
    expect(document.activeElement).toBe(wrapper.find('[data-testid="help-mobile-nav"]').element)

    // 初始焦点在对话框容器上时，Shift+Tab 不能逃到背景
    const drawerEl = wrapper.find('[data-testid="help-mobile-nav"]')
    await drawerEl.trigger('keydown', { key: 'Tab', shiftKey: true })
    expect(drawerEl.element.contains(document.activeElement)).toBe(true)

    await wrapper.find('[data-testid="help-mobile-nav"]').trigger('keydown', { key: 'Escape' })
    await nextTick()
    expect(wrapper.find('[data-testid="help-mobile-nav"]').exists()).toBe(false)
    expect(document.body.style.overflow).toBe('')
    expect(wrapper.find('[data-testid="help-center"]').attributes('style') ?? '').not.toContain('overflow: hidden')
    expect(wrapper.find('[data-testid="help-main"]').classes()).not.toContain('lg:col-span-2')

    await toggle.trigger('click')
    await nextTick()
    await wrapper.find('[data-testid="help-nav-close"]').trigger('click')
    await nextTick()
    expect(wrapper.find('[data-testid="help-mobile-nav"]').exists()).toBe(false)

    await toggle.trigger('click')
    await nextTick()
    await wrapper.find('[data-testid="help-nav-overlay"]').trigger('click')
    await nextTick()
    expect(wrapper.find('[data-testid="help-mobile-nav"]').exists()).toBe(false)
    wrapper.unmount()
  })
  it('窗口跨到桌面断点时自动关闭抽屉', async () => {
    const listeners: Array<(event: MediaQueryListEvent) => void> = []
    const original = window.matchMedia
    window.matchMedia = ((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: (_type: string, listener: (event: MediaQueryListEvent) => void) => {
        listeners.push(listener)
      },
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn()
    })) as unknown as typeof window.matchMedia
    try {
      const { wrapper } = await mountPage()
      await wrapper.find('[data-testid="help-nav-toggle"]').trigger('click')
      await nextTick()
      expect(wrapper.find('[data-testid="help-mobile-nav"]').exists()).toBe(true)
      for (const listener of listeners) listener({ matches: true } as MediaQueryListEvent)
      await nextTick()
      expect(wrapper.find('[data-testid="help-mobile-nav"]').exists()).toBe(false)
      wrapper.unmount()
    } finally {
      window.matchMedia = original
    }
  })
})

describe('帮助中心 · 空态与错误态', () => {
  it('没有任何文档时展示空态，不伪造内容', async () => {
    const { wrapper } = await mountPage([])
    expect(wrapper.find('[data-testid="help-empty"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="help-content"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="help-nav-toggle"]').exists()).toBe(false)
    // 无侧栏时正文占满两列，避免空态被挤进 280px 侧栏列
    expect(wrapper.find('[data-testid="help-main"]').classes()).toContain('lg:col-span-2')
    wrapper.unmount()
  })

  it('文档内容为空时展示「内容不可用」错误态', async () => {
    const broken: FixtureDoc[] = [
      { id: 'chat', group: 'guides', titleKey: 'help.center.docs.chat.title', content: '' },
    ]
    const { wrapper } = await mountPage(broken)
    expect(wrapper.find('[data-testid="help-content-missing"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('无效 ?doc= 回退首篇并清理 URL', async () => {
    const { wrapper, router } = await mountPage(DOCS, '/system-help?doc=bogus')
    await flushPromises()
    await nextTick()
    expect(wrapper.find('[data-testid="help-content"]').text()).toContain('前往登录页')
    expect(router.currentRoute.value.query.doc).toBe('register-login')
    wrapper.unmount()
  })
})

describe('帮助中心 · Markdown XSS 消毒', () => {
  it('原始 HTML 与 javascript: 链接不进入 DOM，且渲染经过 DOMPurify', async () => {
    const sanitizeSpy = vi.spyOn(DOMPurify, 'sanitize')
    const hostile: FixtureDoc[] = [
      {
        id: 'chat',
        group: 'guides',
        titleKey: 'help.center.docs.chat.title',
        content: [
          '## 安全',
          '',
          '<script>window.__xss = 1</script>',
          '',
          '[点我](javascript:alert(1))',
          '',
          '<img src=x onerror="window.__xss = 2">',
        ].join('\n'),
      },
    ]
    const { wrapper } = await mountPage(hostile)
    const html = wrapper.find('[data-testid="help-content"]').html()
    expect(html).not.toMatch(/<script/i)
    // markdown-it 会把非法链接降级为纯文本；关键是 DOM 里不能出现可点击的 javascript: 链接
    expect(wrapper.find('[data-testid="help-content"] a[href^="javascript:"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="help-content"] img[onerror]').exists()).toBe(false)
    expect((window as unknown as { __xss?: number }).__xss).toBeUndefined()
    expect(sanitizeSpy).toHaveBeenCalled()
    sanitizeSpy.mockRestore()
    wrapper.unmount()
  })
})
