/**
 * I1 契约回归：知识库卡片 / 列表行操作排键盘 / 触屏可达性。
 *
 * 背景：卡片（grid）与列表（list）两种视图的操作排此前只有 hover 显隐
 * （opacity-0 + group-hover:opacity-100）：
 *  - 键盘用户 Tab 进的是「看不见的按钮」（透明度 0，但仍在焦点链里）；
 *  - 触屏（iPad 等 hover:none 设备）没有 hover，操作排完全不可达；
 *  - 窄屏降级依赖 .knowledge-page [class*="opacity-0 group-hover:opacity-100"]
 *    属性子串选择器——工具类一改即静默失效。
 *
 * 修复契约（本文件钉住，照 ChatMessages.vue 的 T11 模式）：
 *  1) 操作排/选择框带语义类 kb-card-actions / kb-card-select / kb-row-actions，
 *     不再用透明度工具类控制显隐；
 *  2) 键盘聚焦 :has(:focus-visible) 在媒体查询之外常显（任何设备生效）；
 *  3) hover 显隐只在支持 hover 的设备上生效（@media (hover: hover) 内）；
 *  4) 无 hover 能力的设备（@media (hover: none)）常显；
 *  5) 不得用 :focus-within（鼠标点击聚焦同样命中 → 移开鼠标后滞留）；
 *  6) 不得再用 [class*=] 属性子串选择器兜底窄屏。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import { NMessageProvider, NDialogProvider, NCheckbox, NTooltip, NRadioGroup, NRadioButton } from 'naive-ui'

import KnowledgeView from '../index.vue'
import knowledgeRaw from '../index.vue?raw'
import { getKnowledgeBases } from '@/api/knowledge'
import { useUserStore } from '@/stores/user'
import type { UserInfo } from '@/types/user'
import zh from '@/locales/zh'

vi.mock('@/api/knowledge', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/knowledge')>()
  return {
    ...actual,
    getKnowledgeBases: vi.fn(),
    getDocumentDetail: vi.fn(),
    getDocumentContent: vi.fn(),
    batchDeleteKnowledgeBases: vi.fn(),
    deleteKnowledgeBase: vi.fn(),
    rebuildKnowledgeBase: vi.fn(),
    uploadKnowledgeBase: vi.fn(),
  }
})
vi.mock('@/api/rag', () => ({
  getRagMetrics: vi.fn().mockResolvedValue({ data: { data: null } }),
}))

const mockedGetKnowledgeBases = vi.mocked(getKnowledgeBases)

const REAL_DOC = {
  id: 'doc-1',
  title: 'architecture.md',
  file_name: 'architecture.md',
  file_type: 'word',
  file_size: 1578,
  tags: ['技术文档'],
  description: null,
  parse_error: null,
  created_at: '2026-10-08T05:29:00',
  updated_at: '2026-10-08T05:30:00',
  status: 'completed',
}

const listResponse = (docs: unknown[], total?: number) => ({
  data: {
    success: true,
    message: '获取成功',
    data: { data: docs, total: total ?? docs.length, page: 1, page_size: 12 },
  },
})

const SlotStub = (name: string) =>
  defineComponent({ name, setup(_, { slots }) { return () => h('div', slots.default?.()) } })

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

const Host = defineComponent({
  render: () =>
    h(NDialogProvider, null, { default: () => h(NMessageProvider, null, { default: () => h(KnowledgeView) }) }),
})

function mountView() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const user = useUserStore()
  user.userInfo = {
    id: 1, username: 'tester', email: 't@t', isAdmin: false,
    organization_id: 3, organizations: [], createdAt: '', updatedAt: '',
  } as UserInfo

  return mount(Host, {
    attachTo: document.body,
    global: {
      plugins: [pinia, i18n],
      components: { NCheckbox, NTooltip, NRadioGroup, NRadioButton },
      stubs: {
        KnowledgeUploadPanel: true,
        KnowledgeDetailModals: true,
        Pagination: true,
        Select: true,
        Input: true,
        Grid: SlotStub('Grid'),
        Gi: SlotStub('Gi'),
        Spin: SlotStub('Spin'),
        Tag: SlotStub('Tag'),
      },
    },
  })
}

/** SFC 源码分段：模板段（含 script）与样式段；样式段去注释后再做规则断言 */
const styleText =
  knowledgeRaw.match(/<style[^>]*>([\s\S]*?)<\/style>/)?.[1]?.replace(/\/\*[\s\S]*?\*\//g, '') ?? ''
const templateText = knowledgeRaw.split('<style')[0]

/** 按花括号配对截取 @media 块内容（不依赖「块内只有一条规则」） */
function mediaBlock(css: string, query: string): string | null {
  const idx = css.indexOf(`@media (${query})`)
  if (idx < 0) return null
  const open = css.indexOf('{', idx)
  if (open < 0) return null
  let depth = 0
  for (let i = open; i < css.length; i += 1) {
    if (css[i] === '{') depth += 1
    else if (css[i] === '}') {
      depth -= 1
      if (depth === 0) return css.slice(open + 1, i)
    }
  }
  return null
}

beforeEach(() => {
  mockedGetKnowledgeBases.mockReset()
  mockedGetKnowledgeBases.mockResolvedValue(listResponse([REAL_DOC]) as never)
})

afterEach(() => {
  document.body.innerHTML = ''
})

describe('KnowledgeView · 操作排键盘/触屏可达（I1）', () => {
  it('grid 卡片操作排带语义类 kb-card-actions，按钮在焦点链内且可聚焦', async () => {
    const wrapper = mountView()
    await flushPromises()
    await nextTick()

    const card = wrapper.findAll('.group').find((g) => g.text().includes('architecture.md'))
    expect(card, '文档卡应在屏').toBeTruthy()

    const actions = card!.find('.kb-card-actions')
    expect(actions.exists(), '卡片操作排应带语义类 kb-card-actions').toBe(true)
    // 不再用透明度 / hover 工具类控制显隐（显隐交给语义样式）
    expect(actions.classes()).not.toContain('opacity-0')
    expect(actions.classes().some((c) => c.includes('group-hover:'))).toBe(false)

    // completed 状态：查看 + 删除 = 2 个原生按钮
    const buttons = actions.findAll('button')
    expect(buttons.length).toBe(2)
    for (const btn of buttons) {
      // 未被移出 tab 序（naive 真实按钮可能显式 tabindex="0"，同样可聚焦）
      expect(btn.attributes('tabindex')).not.toBe('-1')
      expect(btn.attributes('disabled')).toBeUndefined()
    }

    // 程序化聚焦落到按钮本身（浏览器 Tab 可达性的 jsdom 侧等价断言）
    ;(buttons[0].element as HTMLButtonElement).focus()
    expect(document.activeElement).toBe(buttons[0].element)

    wrapper.unmount()
  })

  it('grid 卡片选择框带语义类 kb-card-select（不再被属性子串选择器兜底）', async () => {
    const wrapper = mountView()
    await flushPromises()
    await nextTick()

    const card = wrapper.findAll('.group').find((g) => g.text().includes('architecture.md'))
    const select = card!.find('.kb-card-select')
    expect(select.exists(), '卡片选择框应带语义类 kb-card-select').toBe(true)
    expect(select.classes()).not.toContain('opacity-0')
    expect(select.classes().some((c) => c.includes('group-hover:'))).toBe(false)

    wrapper.unmount()
  })

  it('list 行操作排带语义类 kb-row-actions，按钮在焦点链内且可聚焦', async () => {
    const wrapper = mountView()
    await flushPromises()
    await nextTick()

    // 切到列表视图（n-radio-button value="list"）
    const radios = wrapper.findAll('input[type="radio"]')
    expect(radios.length, '视图切换单选应渲染').toBe(2)
    await radios[1].setValue(true)
    await nextTick()

    const actions = wrapper.find('.kb-row-actions')
    expect(actions.exists(), '列表行操作排应带语义类 kb-row-actions').toBe(true)
    expect(actions.classes()).not.toContain('opacity-0')
    expect(actions.classes().some((c) => c.includes('group-hover:'))).toBe(false)

    const buttons = actions.findAll('button')
    expect(buttons.length).toBe(2)
    ;(buttons[0].element as HTMLButtonElement).focus()
    expect(document.activeElement).toBe(buttons[0].element)

    wrapper.unmount()
  })

  it('CSS：默认隐藏，键盘 :has(:focus-visible) 在 hover 媒体查询之外常显', () => {
    expect(styleText).toMatch(/\.kb-card-actions\s*\{[^}]*opacity:\s*0/)
    expect(styleText).toMatch(/\.kb-row-actions\s*\{[^}]*opacity:\s*0/)
    expect(styleText).toMatch(/\.kb-card-actions:has\(:focus-visible\)/)
    expect(styleText).toMatch(/\.kb-row-actions:has\(:focus-visible\)/)
    expect(styleText).toMatch(/\.kb-card-select:has\(:focus-visible\)/)
  })

  it('CSS：hover 显隐只在 @media (hover: hover) 内（触屏不依赖 hover）', () => {
    const hoverMedia = mediaBlock(styleText, 'hover: hover')
    expect(hoverMedia).not.toBeNull()
    expect(hoverMedia!).toMatch(/\.group:hover\s+\.kb-card-actions/)
    expect(hoverMedia!).toMatch(/\.group:hover\s+\.kb-card-select/)
    expect(hoverMedia!).toMatch(/\.group:hover\s+\.kb-row-actions/)
  })

  it('CSS：无 hover 能力的触屏设备（@media (hover: none)）操作排常显', () => {
    const noneMedia = mediaBlock(styleText, 'hover: none')
    expect(noneMedia).not.toBeNull()
    expect(noneMedia!).toMatch(/\.kb-card-actions\s*\{[^}]*opacity:\s*1/)
    expect(noneMedia!).toMatch(/\.kb-card-select\s*\{[^}]*opacity:\s*1/)
    expect(noneMedia!).toMatch(/\.kb-row-actions\s*\{[^}]*opacity:\s*1/)
  })

  it('防回退：不得用 :focus-within 显隐，也不得用 [class*=] 属性子串选择器', () => {
    expect(styleText).not.toMatch(/:focus-within/)
    expect(styleText).not.toMatch(/\[class\*=/)
  })

  it('防回退：模板不再用 hover-only 工具类控制操作排/选择框显隐', () => {
    expect(templateText).not.toContain('opacity-0 group-hover:opacity-100')
  })
})
