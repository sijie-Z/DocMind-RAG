/**
 * User Commands (#475) · 非 Agent 模式的如实提示判别。
 *
 * 后端契约：斜杠命令只在 Agent 通道执行；普通 RAG 聊天没有命令入口
 * （chat.py 仅在 useAgent 分支拦截 "/" 前缀）。因此聊天页在「快速问答」下
 * 输入 / 开头文本时，必须如实提示「命令仅在 Agent 模式可用」，而不是让用户
 * 以为命令会被执行。
 *
 * 变异点：
 * - 删除提示分支 → 快速问答用例红；
 * - 提示不区分 useAgent → Agent 模式用例红（提示应隐藏）。
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
      useAgent: false,
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
        'n-radio-group': PassthroughStub,
        'n-radio-button': PassthroughStub,
      },
    },
  })
}

describe('ChatInput · 非 Agent 模式的斜杠命令提示', () => {
  it('快速问答下输入 / 开头文本 → 展示「仅 Agent 模式可用」提示', () => {
    const wrapper = mountInput({ inputMessage: '/help' })
    const hint = wrapper.find('[data-testid="slash-agent-only-hint"]')
    expect(hint.exists()).toBe(true)
    expect(hint.text()).toContain('Agent')
  })

  it('Agent（深度分析）模式下不显示该提示（命令由服务端执行）', () => {
    const wrapper = mountInput({ inputMessage: '/help', useAgent: true })
    expect(wrapper.find('[data-testid="slash-agent-only-hint"]').exists()).toBe(false)
  })

  it('普通文本不显示提示', () => {
    const wrapper = mountInput({ inputMessage: '你好' })
    expect(wrapper.find('[data-testid="slash-agent-only-hint"]').exists()).toBe(false)
  })
})
