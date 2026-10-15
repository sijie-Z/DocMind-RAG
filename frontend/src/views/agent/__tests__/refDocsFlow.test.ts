/**
 * 体验第四波 @引用 —— Agent 页接线判别：
 * - 输入组件 send(text, refs) → agentApi.chat 收到 referencedDocumentIds；
 * - 用户气泡渲染引用 chips（回溯）；
 * - warning 事件有视图分支（@引用失效 / 落库失败等不再"发而无收"）。
 *
 * 变异点：handleSend 不透传 refs → chat 载荷用例红；warning 分支删除 →
 * warning 渲染用例红。
 */
import { describe, it, expect, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { NMessageProvider, NSpin, NButton, NTooltip, NIcon } from 'naive-ui'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import zh from '@/locales/zh'

import AgentView from '../index.vue'
import { useAgentStore } from '@/stores/agent'

const chatMock = vi.fn()
vi.mock('@/api/agent', () => ({
  agentApi: {
    listTools: vi.fn().mockResolvedValue({ data: [] }),
    listSessions: vi.fn().mockResolvedValue({ data: { sessions: [] } }),
    getConfig: vi.fn().mockResolvedValue({ data: {} }),
    chat: (...args: unknown[]) => chatMock(...args),
    getSession: vi.fn().mockResolvedValue({ data: {} }),
    deleteSession: vi.fn().mockResolvedValue({}),
    createSession: vi.fn().mockResolvedValue({ data: null }),
    updateConfig: vi.fn().mockResolvedValue({}),
    submitFeedback: vi.fn().mockResolvedValue({}),
  },
}))

const i18n = createI18n({ legacy: false, locale: 'zh', fallbackLocale: 'zh', messages: { zh } })

const Host = defineComponent({
  render: () => h(NMessageProvider, null, { default: () => h(AgentView) }),
})

// 输入组件替换为直通桩：点击即带 refs 发 send（避开真实选择层，本文件只测视图接线）
const FakeInput = defineComponent({
  emits: ['send', 'stop', 'steer'],
  setup(_, { emit }) {
    return () => h('button', {
      class: 'fake-send',
      onClick: () => emit('send', '查预算', [{ id: 'd1', title: '预算报告' }]),
    }, 'send')
  },
})

const stubs = {
  PlanTree: true,
  ThinkingStream: true,
  ReflectionBanner: true,
  RetryHintLine: true,
  RunReportBlock: true,
  AgentConfigPanel: true,
  SessionSelector: true,
  AgentInput: FakeInput,
  NSpin: true,
  NButton: true,
  NTooltip: true,
  NIcon: true,
}

async function setupView() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const wrapper = mount(Host, {
    global: {
      plugins: [pinia, i18n],
      components: { NSpin, NButton, NTooltip, NIcon },
      stubs,
    },
  })
  await flushPromises()
  return wrapper
}

describe('Agent 页 · @引用接线与 warning 渲染', () => {
  it('send(text, refs) → chat 载荷带 referencedDocumentIds，用户气泡出 chips', async () => {
    chatMock.mockReset()
    chatMock.mockImplementation(async () => ({ abort: () => {} }))
    const wrapper = await setupView()

    await wrapper.find('.fake-send').trigger('click')
    await flushPromises()

    expect(chatMock).toHaveBeenCalledTimes(1)
    const options = chatMock.mock.calls[0][2] as { referencedDocumentIds?: string[] }
    expect(options.referencedDocumentIds).toEqual(['d1'])

    const refs = wrapper.find('[data-testid="user-refs"]')
    expect(refs.exists()).toBe(true)
    expect(refs.text()).toContain('预算报告')
  })

  it('无 refs 时 chat 载荷不携带 referencedDocumentIds（负例）', async () => {
    chatMock.mockReset()
    chatMock.mockImplementation(async () => ({ abort: () => {} }))
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useAgentStore()
    // 直接走 store 造一条用户消息并触发视图内的 chat 路径不现实——
    // 这里改从 send 桩的同一入口出发：用无 refs 的输入桩替换后再挂载
    const NoRefInput = defineComponent({
      emits: ['send'],
      setup(_, { emit }) {
        return () => h('button', { class: 'fake-send2', onClick: () => emit('send', '无引用任务') }, 's')
      },
    })
    const wrapper = mount(Host, {
      global: {
        plugins: [pinia, i18n],
        components: { NSpin, NButton, NTooltip, NIcon },
        stubs: { ...stubs, AgentInput: NoRefInput },
      },
    })
    await flushPromises()
    await wrapper.find('.fake-send2').trigger('click')
    await flushPromises()

    const options = chatMock.mock.calls[0][2] as { referencedDocumentIds?: string[] }
    expect(options.referencedDocumentIds).toBeUndefined()
    // store 用户消息无 references → 不渲染 chips
    expect(store.messages[0]?.references).toBeUndefined()
  })

  it('warning 事件 → 琥珀时间线条目（含徽标与内容）', async () => {
    const wrapper = await setupView()
    const store = useAgentStore()
    store.addMessage({
      role: 'assistant',
      content: '',
      events: [
        { type: 'warning', content: '引用文档不可用（不存在），已忽略：ghost' },
      ],
      loading: false,
      currentTool: '',
      thinkingText: '',
      planSteps: [],
      planId: '',
      progress: 0,
    } as any)
    await flushPromises()

    const line = wrapper.find('[data-testid="warning-line"]')
    expect(line.exists()).toBe(true)
    expect(line.text()).toContain('提示')
    expect(line.text()).toContain('引用文档不可用（不存在）')
  })
})
