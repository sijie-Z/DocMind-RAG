/**
 * I1 契约回归：会话列表操作排键盘 / 触屏可达性。
 *
 * 背景：操作排（继续 / 删除）此前只有 hover 显隐
 * （opacity-0 + group-hover:opacity-100）：键盘 Tab 到的是透明按钮，
 * 触屏（hover:none）永远不出现。
 *
 * 修复契约（同 ChatMessages.vue 的 T11 模式）：
 *  1) 操作排带语义类 conv-actions，不再用透明度工具类控制显隐；
 *  2) 键盘 :has(:focus-visible) 在媒体查询之外常显；
 *  3) hover 显隐只在 @media (hover: hover) 内；
 *  4) @media (hover: none) 常显；
 *  5) 不得用 :focus-within。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import { NButton, NMessageProvider, NDialogProvider } from 'naive-ui'

import ConversationsView from '../index.vue'
import conversationsRaw from '../index.vue?raw'
import { getConversations } from '@/api/conversation'
import zh from '@/locales/zh'

vi.mock('@/api/conversation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/conversation')>()
  return {
    ...actual,
    getConversations: vi.fn(),
    deleteConversation: vi.fn(),
  }
})

const mockedGetConversations = vi.mocked(getConversations)

const CONV = {
  id: 'conv-1',
  title: '架构讨论',
  created_at: '2026-10-08T05:00:00',
  updated_at: '2026-10-08T06:00:00',
  message_count: 4,
}

const listResponse = (items: unknown[], total?: number) => ({
  data: {
    success: true,
    data: { data: items, total: total ?? items.length, page: 1, page_size: 10 },
  },
})

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

function mountView() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({ history: createMemoryHistory(), routes: [] })
  const Host = defineComponent({
    render: () =>
      h(NDialogProvider, null, { default: () => h(NMessageProvider, null, { default: () => h(ConversationsView) }) }),
  })
  return mount(Host, {
    attachTo: document.body,
    global: {
      plugins: [pinia, i18n, router],
      // n-button 生产由 app.use(naive) 全局注册；测试里注册真组件，
      // 原生 <button> 语义是焦点链断言的前提
      components: { NButton },
    },
  })
}

const styleText =
  conversationsRaw.match(/<style[^>]*>([\s\S]*?)<\/style>/)?.[1]?.replace(/\/\*[\s\S]*?\*\//g, '') ?? ''
const templateText = conversationsRaw.split('<style')[0]

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
  mockedGetConversations.mockReset()
  mockedGetConversations.mockResolvedValue(listResponse([CONV]) as never)
})

afterEach(() => {
  document.body.innerHTML = ''
})

describe('ConversationsView · 操作排键盘/触屏可达（I1）', () => {
  it('操作排带语义类 conv-actions，按钮在焦点链内且可聚焦', async () => {
    const wrapper = mountView()
    await flushPromises()
    await nextTick()

    const actions = wrapper.find('.conv-actions')
    expect(actions.exists(), '会话操作排应带语义类 conv-actions').toBe(true)
    expect(actions.classes()).not.toContain('opacity-0')
    expect(actions.classes().some((c) => c.includes('group-hover:'))).toBe(false)

    // 继续 + 删除 = 2 个原生按钮
    const buttons = actions.findAll('button')
    expect(buttons.length).toBe(2)
    for (const btn of buttons) {
      // 未被移出 tab 序（naive 真实按钮可能显式 tabindex="0"，同样可聚焦）
      expect(btn.attributes('tabindex')).not.toBe('-1')
      expect(btn.attributes('disabled')).toBeUndefined()
    }

    ;(buttons[0].element as HTMLButtonElement).focus()
    expect(document.activeElement).toBe(buttons[0].element)

    wrapper.unmount()
  })

  it('CSS：默认隐藏，键盘 :has(:focus-visible) 在 hover 媒体查询之外常显', () => {
    expect(styleText).toMatch(/\.conv-actions\s*\{[^}]*opacity:\s*0/)
    expect(styleText).toMatch(/\.conv-actions:has\(:focus-visible\)/)
  })

  it('CSS：hover 显隐只在 @media (hover: hover) 内（触屏不依赖 hover）', () => {
    const hoverMedia = mediaBlock(styleText, 'hover: hover')
    expect(hoverMedia).not.toBeNull()
    expect(hoverMedia!).toMatch(/\.group:hover\s+\.conv-actions/)
  })

  it('CSS：无 hover 能力的触屏设备（@media (hover: none)）操作排常显', () => {
    const noneMedia = mediaBlock(styleText, 'hover: none')
    expect(noneMedia).not.toBeNull()
    expect(noneMedia!).toMatch(/\.conv-actions\s*\{[^}]*opacity:\s*1/)
  })

  it('防回退：不得用 :focus-within，模板不得保留 hover-only 工具类', () => {
    expect(styleText).not.toMatch(/:focus-within/)
    expect(templateText).not.toContain('opacity-0 group-hover:opacity-100')
  })
})
