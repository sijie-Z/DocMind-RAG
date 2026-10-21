/**
 * I1 契约回归：聊天侧栏会话删除按钮键盘 / 触屏可达性。
 *
 * 背景：删除按钮容器此前只有 hover 显隐（opacity-0 + group-hover:opacity-100）：
 * 键盘 Tab 到的是透明按钮，触屏（hover:none）永远不出现。
 *
 * 修复契约（同 ChatMessages.vue 的 T11 模式）：
 *  1) 容器带语义类 sidebar-conv-actions，不再用透明度工具类控制显隐；
 *  2) 键盘 :has(:focus-visible) 在媒体查询之外常显；
 *  3) hover 显隐只在 @media (hover: hover) 内；
 *  4) @media (hover: none) 常显；
 *  5) 不得用 :focus-within。
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createI18n } from 'vue-i18n'
import ChatSidebar from '../components/ChatSidebar.vue'
import chatSidebarRaw from '../components/ChatSidebar.vue?raw'
import zh from '@/locales/zh'

vi.mock('@/api/health', () => ({
  getHealth: vi.fn().mockResolvedValue({ version: '9.9.9' }),
}))

// n-button 真实按钮替身：焦点链断言需要原生 <button>
const NButtonNative = defineComponent({
  name: 'NButtonNative',
  emits: ['click'],
  setup(_, { slots, emit }) {
    return () =>
      h('button', { type: 'button', onClick: (e: MouseEvent) => emit('click', e) }, [
        slots.icon?.(),
        slots.default?.(),
      ])
  },
})

// n-popconfirm 直通替身：只渲染触发按钮与确认文案
const NPopconfirmPassthrough = defineComponent({
  name: 'PopconfirmPassthrough',
  setup(_, { slots }) {
    return () => h('span', { class: 'popconfirm-passthrough' }, [slots.trigger?.(), slots.default?.()])
  },
})

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

const CONV = {
  id: 'conv-1',
  title: '架构讨论',
  created_at: '2026-10-08T05:00:00',
  updated_at: '2026-10-08T06:00:00',
  message_count: 4,
  user_id: 1,
}

function mountSidebar() {
  return mount(ChatSidebar, {
    attachTo: document.body,
    props: {
      sidebarOpen: true,
      conversations: [CONV],
      isListLoading: false,
      convLoadError: false,
      currentConversationId: undefined,
    },
    global: {
      plugins: [i18n],
      stubs: {
        'n-button': NButtonNative,
        'n-popconfirm': NPopconfirmPassthrough,
        'n-tooltip': NPopconfirmPassthrough,
        'n-icon': true,
        'n-skeleton': true,
        'n-result': true,
      },
    },
  })
}

const styleText =
  chatSidebarRaw.match(/<style[^>]*>([\s\S]*?)<\/style>/)?.[1]?.replace(/\/\*[\s\S]*?\*\//g, '') ?? ''
const templateText = chatSidebarRaw.split('<style')[0]

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

afterEach(() => {
  document.body.innerHTML = ''
})

describe('ChatSidebar · 会话删除入口键盘/触屏可达（I1）', () => {
  it('容器带语义类 sidebar-conv-actions，删除按钮在焦点链内且可聚焦', () => {
    const wrapper = mountSidebar()

    const actions = wrapper.find('.sidebar-conv-actions')
    expect(actions.exists(), '侧栏删除容器应带语义类 sidebar-conv-actions').toBe(true)
    expect(actions.classes()).not.toContain('opacity-0')
    expect(actions.classes().some((c) => c.includes('group-hover:'))).toBe(false)

    const button = actions.find('button')
    expect(button.exists()).toBe(true)
    expect(button.attributes('tabindex')).toBeUndefined()
    expect(button.attributes('disabled')).toBeUndefined()

    ;(button.element as HTMLButtonElement).focus()
    expect(document.activeElement).toBe(button.element)

    wrapper.unmount()
  })

  it('CSS：默认隐藏，键盘 :has(:focus-visible) 在 hover 媒体查询之外常显', () => {
    expect(styleText).toMatch(/\.sidebar-conv-actions\s*\{[^}]*opacity:\s*0/)
    expect(styleText).toMatch(/\.sidebar-conv-actions:has\(:focus-visible\)/)
  })

  it('CSS：hover 显隐只在 @media (hover: hover) 内（触屏不依赖 hover）', () => {
    const hoverMedia = mediaBlock(styleText, 'hover: hover')
    expect(hoverMedia).not.toBeNull()
    expect(hoverMedia!).toMatch(/\.group:hover\s+\.sidebar-conv-actions/)
  })

  it('CSS：无 hover 能力的触屏设备（@media (hover: none)）常显', () => {
    const noneMedia = mediaBlock(styleText, 'hover: none')
    expect(noneMedia).not.toBeNull()
    expect(noneMedia!).toMatch(/\.sidebar-conv-actions\s*\{[^}]*opacity:\s*1/)
  })

  it('防回退：不得用 :focus-within，模板不得保留 hover-only 工具类', () => {
    expect(styleText).not.toMatch(/:focus-within/)
    expect(templateText).not.toContain('opacity-0 group-hover:opacity-100')
  })
})
