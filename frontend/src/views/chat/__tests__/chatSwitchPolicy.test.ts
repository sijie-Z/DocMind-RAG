/**
 * issue #508：Chat 开关策略（B4 后第 508 号修订）。
 *
 * 原契约（B4）钉「Agent 模式开启 → 传输开关锁定 + 解释」。方案 B 后深度分析
 * 迁至 /agent 工作台，Chat 内不再有 Agent 模式：
 * 1. 传输开关（SSE/WS）始终可用，没有锁定态/锁定解释；
 * 2. 组织策略禁用隐私模式时，隐私开关置灰，并注明来源是组织策略（不变）；
 * 3. Agent 模式旧控件（agent-mode-control / agent-sse-note）不复存在。
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

describe('chat switch policy (issue #508)', () => {
  it('传输开关始终可用（不再有 Agent 模式锁定态）', async () => {
    const wrapper = mountInput()
    await nextTick()

    const transport = wrapper.find('[data-testid="transport-control"]')
    expect(transport.exists()).toBe(true)
    expect(wrapper.find('[data-testid="transport-switch"]').classes()).not.toContain(
      'n-switch--disabled',
    )
    expect(wrapper.find('[data-testid="agent-mode-control"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="agent-sse-note-inline"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="agent-sse-note"]').exists()).toBe(false)
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

  it('默认状态下隐私开关可用且无锁定说明', async () => {
    const wrapper = mountInput()
    await nextTick()

    expect(
      wrapper.find('[data-testid="privacy-lock"]').attributes('data-disabled'),
    ).toBe('false')
    expect(wrapper.find('[data-testid="privacy-switch"]').classes()).not.toContain(
      'n-switch--disabled',
    )
    expect(wrapper.find('[data-testid="privacy-policy-note-inline"]').exists()).toBe(false)
  })
})
