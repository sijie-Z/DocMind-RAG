/**
 * User Commands (#475) · Chat 斜杠命令提示（issue #508 修订）。
 *
 * 后端契约：斜杠命令只在 Agent 通道执行；Chat 的普通 RAG 聊天没有命令入口。
 * 方案 B 后 Chat 内不再有 Agent 模式开关——凡 "/" 开头文本一律如实提示
 * 「命令仅在 Agent 工作台可用」，不再存在「Agent 模式下隐藏提示」的分支。
 */
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createI18n } from 'vue-i18n'
import ChatInput from '../components/ChatInput.vue'
import zh from '@/locales/zh'

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

const NButtonStub = defineComponent({
  name: 'NButton',
  setup(_, { slots }) {
    return () => h('button', { type: 'button' }, [slots.icon?.(), slots.default?.()])
  },
})
const PassthroughStub = defineComponent({
  name: 'Passthrough',
  setup(_, { slots }) {
    return () => h('span', [slots.trigger?.(), slots.default?.()])
  },
})

function mountInput(props: Record<string, unknown> = {}) {
  return mount(ChatInput, {
    props: {
      isLoading: false,
      attachedFiles: [],
      attachedFileIds: [],
      connectionStatus: 'connected',
      connectionStatusText: '已连接',
      inputMessage: '',
      strictMode: false,
      privacyMode: true,
      useSSE: true,
      useStream: true,
      ...props,
    },
    global: {
      plugins: [i18n],
      stubs: {
        'n-button': NButtonStub,
        'n-input': PassthroughStub,
        'n-tooltip': PassthroughStub,
        'n-popover': PassthroughStub,
        'n-switch': PassthroughStub,
        'n-icon': true,
      },
    },
  })
}

describe('ChatInput · 斜杠命令提示（issue #508 后始终适用）', () => {
  it('输入 / 开头文本 → 展示「仅 Agent 可用」提示', () => {
    const wrapper = mountInput({ inputMessage: '/help' })
    const hint = wrapper.find('[data-testid="slash-agent-only-hint"]')
    expect(hint.exists()).toBe(true)
    expect(hint.text()).toContain('Agent')
  })

  it('普通文本不显示提示', () => {
    const wrapper = mountInput({ inputMessage: '你好' })
    expect(wrapper.find('[data-testid="slash-agent-only-hint"]').exists()).toBe(false)
  })
})
