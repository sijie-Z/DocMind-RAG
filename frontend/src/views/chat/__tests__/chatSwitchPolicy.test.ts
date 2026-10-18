/**
 * B4：聊天三开关合并后的策略 UI。
 *
 * 契约（先红：当前 ChatInput 没有这些 testid / 组织策略说明）：
 * 1. Agent 模式开启时，传输开关（SSE/WS）被锁定，并给出「Agent 固定 SSE」解释；
 * 2. 组织策略禁用隐私模式时，隐私开关置灰，并注明来源是组织策略；
 * 3. 默认（Agent 关、组织允许）时两个开关都可用，且不出现解释文案。
 *
 * 说明：Naive 的 popover 内容在未悬浮时不渲染（teleport）；测试用同名
 * Popover/Tooltip SlotStub 内联渲染，断言直接落在真实 n-switch 的
 * `n-switch--disabled` 类上（不是只断言外层 data-disabled 标记）。
 */
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import naive from 'naive-ui'
import ChatInput from '../components/ChatInput.vue'
import zh from '@/locales/zh'

// Naive 的 popover 内容未悬浮时不渲染（teleport）。用同名 SlotStub 内联渲染，
// 让断言落在真实 n-switch 的 disabled 类上。
const PopoverStub = defineComponent({
  setup(_, { slots }) {
    return () => h('div', [slots.trigger?.(), slots.default?.()])
  },
})

const i18n = createI18n({
  legacy: false,
  locale: 'zh',
  missingWarn: false,
  fallbackWarn: false,
  messages: { zh },
})

function mountInput(overrides: Record<string, unknown> = {}) {
  return mount(ChatInput, {
    props: {
      inputMessage: '',
      strictMode: false,
      privacyMode: true,
      useSSE: true,
      useStream: true,
      useAgent: false,
      isLoading: false,
      attachedFiles: [],
      attachedFileIds: [],
      connectionStatus: 'connected',
      connectionStatusText: '已连接',
      orgPrivacyAllowed: true,
      ...overrides,
    },
    global: {
      plugins: [naive, i18n],
      stubs: { Popover: PopoverStub, Tooltip: PopoverStub },
    },
  })
}

describe('chat switch policy', () => {
  it('Agent 模式锁定传输开关并解释原因', async () => {
    const wrapper = mountInput({ useAgent: true })
    await nextTick()

    const transport = wrapper.find('[data-testid="transport-lock"]')
    expect(transport.exists()).toBe(true)
    expect(transport.attributes('data-disabled')).toBe('true')
    expect(wrapper.find('[data-testid="transport-switch"]').classes()).toContain(
      'n-switch--disabled',
    )

    expect(wrapper.find('[data-testid="agent-sse-note-inline"]').text()).toContain(
      zh.chat.agentRequiresSse,
    )
  })

  it('组织禁用隐私模式时开关置灰并注明组织策略来源', async () => {
    const wrapper = mountInput({ orgPrivacyAllowed: false, privacyMode: false })
    await nextTick()

    const privacy = wrapper.find('[data-testid="privacy-lock"]')
    expect(privacy.exists()).toBe(true)
    expect(privacy.attributes('data-disabled')).toBe('true')
    expect(wrapper.find('[data-testid="privacy-switch"]').classes()).toContain(
      'n-switch--disabled',
    )

    expect(wrapper.find('[data-testid="privacy-policy-note-inline"]').text()).toContain(
      zh.chat.orgPrivacyDisabled,
    )
  })

  it('默认状态下两个开关可用且无锁定说明', async () => {
    const wrapper = mountInput()
    await nextTick()

    expect(
      wrapper.find('[data-testid="transport-lock"]').attributes('data-disabled'),
    ).toBe('false')
    expect(
      wrapper.find('[data-testid="privacy-lock"]').attributes('data-disabled'),
    ).toBe('false')
    expect(wrapper.find('[data-testid="transport-switch"]').classes()).not.toContain(
      'n-switch--disabled',
    )
    expect(wrapper.find('[data-testid="privacy-switch"]').classes()).not.toContain(
      'n-switch--disabled',
    )
    expect(wrapper.find('[data-testid="agent-sse-note-inline"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="privacy-policy-note-inline"]').exists()).toBe(false)
  })

  it('Agent 开关仍是可切换的独立控件（合并后的模式区）', async () => {
    const wrapper = mountInput()
    await nextTick()
    expect(wrapper.find('[data-testid="agent-mode-control"]').exists()).toBe(true)
  })
})
