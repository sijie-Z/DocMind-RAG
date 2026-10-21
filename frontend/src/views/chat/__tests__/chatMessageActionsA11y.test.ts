/**
 * T11 契约回归：消息操作排键盘 / 触屏可达性。
 *
 * 背景：操作排此前只有 hover 显隐（opacity-0 + group-hover/bubble:opacity-100）：
 *  - 键盘用户 Tab 进的是「看不见的按钮」（透明度 0，但仍在焦点链里）；
 *  - 触屏没有 hover，操作排完全不可达。
 *
 * 修复契约（本文件钉住）：
 *  1) 操作排带语义类 msg-actions，按钮保持原生 <button>（不进负 tabindex）；
 *  2) hover 显隐只在支持 hover 的设备上生效（@media (hover: hover) 内）；
 *  3) 键盘聚焦 :has(:focus-visible) 在媒体查询之外常显；
 *  4) 无 hover 能力的设备（@media (hover: none)）常显；
 *  5) 不得用 :focus-within 做显隐——鼠标点击按钮同样会获得焦点，移开鼠标后
 *     操作排滞留（旧 bug 防回退）。
 *
 * 变异点（任一发生 → 本文件红）：
 *  - 恢复 opacity-0 / group-hover/bubble:opacity-100 工具类控制显隐；
 *  - 把 hover 规则挪出 @media (hover: hover)；
 *  - 用 :focus-within 替代 :has(:focus-visible)；
 *  - 去掉 @media (hover: none) 的常显规则。
 */
import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createI18n } from 'vue-i18n'
import ChatMessages from '../components/ChatMessages.vue'
import chatMessagesRaw from '../components/ChatMessages.vue?raw'
import type { ChatMessage } from '@/types/chat'

// E5 起虚拟滚动为 ChatMessages 局部引入（不再经 main.ts 全局注册），测试侧
// 用 vi.mock 把模块替换为直通 stub（按 items 逐项渲染 default slot，语义同旧 stub）。
vi.mock('vue-virtual-scroller', async () => {
  const stubs = await import('@/test-utils/virtualScroller')
  return { DynamicScroller: stubs.DynamicScrollerStub, DynamicScrollerItem: stubs.DynamicScrollerItemStub }
})

const MarkdownStub = defineComponent({
  name: 'Markdown',
  props: { content: { type: String, default: '' } },
  setup(props) {
    return () => h('div', props.content)
  },
})

// n-button 真实按钮替身：键盘可达性断言需要原生 <button>（真 naive 渲染的也是
// <button>；直接 stub:true 会变成不可聚焦的自定义元素，测不到焦点链）
const NButtonReal = defineComponent({
  name: 'NButtonReal',
  emits: ['click'],
  setup(_, { slots, emit }) {
    return () =>
      h(
        'button',
        { type: 'button', onClick: (e: MouseEvent) => emit('click', e) },
        [slots.icon?.(), slots.default?.()],
      )
  },
})

// n-tooltip 直通替身：默认插槽（提示文案）与 trigger 插槽都渲染
const NTooltipPassthrough = defineComponent({
  name: 'Tooltip',
  setup(_, { slots }) {
    return () => h('span', { class: 'tooltip-passthrough' }, [slots.trigger?.(), slots.default?.()])
  },
})

const i18n = createI18n({
  legacy: false,
  locale: 'zh',
  messages: {
    zh: {
      chat: {
        useful: '有用',
        notUseful: '没用',
        regenerate: '重新生成',
      },
    },
  },
})

function makeAssistant(): ChatMessage {
  return {
    id: 1,
    content: '回答正文',
    messageType: 'assistant',
    conversationId: 1,
    sources: [],
  } as unknown as ChatMessage
}

function mountChat(messages: ChatMessage[]) {
  return mount(ChatMessages, {
    // 挂到 document 上，聚焦断言才落得到 document.activeElement
    attachTo: document.body,
    props: { messages, isLoading: false, showBackToBottom: false, userAvatar: '', suggestions: [] },
    global: {
      plugins: [i18n],
      stubs: {
        Markdown: MarkdownStub,
        'n-button': NButtonReal,
        'n-tooltip': NTooltipPassthrough,
        'n-icon': true,
        'n-avatar': true,
        'n-tag': true,
        'n-popover': true,
      },
    },
  })
}

/** SFC 源码分段：模板段（含 script）与样式段；样式段去注释后再做规则断言 */
const styleText =
  chatMessagesRaw.match(/<style[^>]*>([\s\S]*?)<\/style>/)?.[1]?.replace(/\/\*[\s\S]*?\*\//g, '') ?? ''
const templateText = chatMessagesRaw.split('<style')[0]

describe('ChatMessages · 操作排键盘/触屏可达（T11）', () => {
  it('操作排带语义类 msg-actions，动作保持原生 <button> 且在焦点链内', () => {
    const wrapper = mountChat([makeAssistant()])
    const actions = wrapper.find('[data-testid="message-actions"]')

    expect(actions.exists()).toBe(true)
    expect(actions.classes()).toContain('msg-actions')
    // 不再用透明度工具类隐藏（隐藏交给 msg-actions 的语义样式）
    expect(actions.classes()).not.toContain('opacity-0')

    // 最后一条 assistant 消息：赞/踩/复制/重新生成 = 4 个原生按钮
    const buttons = actions.findAll('button')
    expect(buttons.length).toBe(4)
    for (const btn of buttons) {
      // 原生 button 且未被移出 tab 序 → 键盘可 Tab 达
      expect(btn.attributes('tabindex')).toBeUndefined()
      expect(btn.attributes('disabled')).toBeUndefined()
    }

    // 程序化聚焦落到按钮本身（浏览器 Tab 可达性的 jsdom 侧等价断言）
    ;(buttons[0].element as HTMLButtonElement).focus()
    expect(document.activeElement).toBe(buttons[0].element)

    wrapper.unmount()
  })

  it('CSS：默认隐藏，键盘 :has(:focus-visible) 在 hover 媒体查询之外常显', () => {
    expect(styleText).toMatch(/\.msg-actions\s*\{[^}]*opacity:\s*0/)
    expect(styleText).toMatch(/\.msg-actions:has\(:focus-visible\)\s*\{[^}]*opacity:\s*1/)
  })

  it('CSS：hover 显隐被 @media (hover: hover) 包裹（触屏不依赖 hover）', () => {
    const hoverMedia = styleText.match(/@media\s*\(hover:\s*hover\)\s*\{([\s\S]*?)\n\}/)
    expect(hoverMedia).not.toBeNull()
    expect(hoverMedia![1]).toMatch(/\.group\\\/bubble:hover\s+\.msg-actions/)
    expect(hoverMedia![1]).toMatch(/opacity:\s*1/)
  })

  it('CSS：无 hover 能力的触屏设备（@media (hover: none)）操作排常显', () => {
    const noneMedia = styleText.match(/@media\s*\(hover:\s*none\)\s*\{([\s\S]*?)\n\}/)
    expect(noneMedia).not.toBeNull()
    expect(noneMedia![1]).toMatch(/\.msg-actions\s*\{[^}]*opacity:\s*1/)
  })

  it('防回退：不得用 :focus-within 显隐（鼠标点击聚焦后移开不滞留）', () => {
    // 鼠标点击按钮同样产生焦点；若用 :focus-within 显隐，点完移开鼠标操作排会滞留。
    // 键盘专用显隐必须用 :focus-visible 判别。
    expect(styleText).not.toMatch(/:focus-within/)
  })

  it('防回退：模板不再用 hover-only 工具类控制操作排显隐', () => {
    expect(templateText).not.toContain('group-hover/bubble:opacity-100')
  })
})
