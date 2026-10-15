/**
 * issue #289 主项（渲染侧）：聊天页 Agent 模式事件的展示，与 Agent 页信息同构——
 * run_report 折叠块（含 Tokens 徽标）、retry_hint 提示行、计划树、reflection
 * 横幅，均复用 components/agent 下的同一组共享组件。
 *
 * 变异点：删 ChatMessages.vue 中任一 extras 节点的 v-if/v-for → 对应用例红；
 * 无条件渲染 → 负例红。
 */
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createI18n } from 'vue-i18n'
import ChatMessages from '../components/ChatMessages.vue'
import type { ChatMessage } from '@/types/chat'

// DynamicScroller 为全局注册（main.ts app.use(VueVirtualScroller)），测试侧用
// 直通 stub 复现「按 items 逐项渲染 default slot」的行为。
const DynamicScroller = defineComponent({
  props: { items: { type: Array, default: () => [] } },
  setup(props, { slots }) {
    return () =>
      h('div', props.items.map((item, index) => slots.default?.({ item, index, active: true })))
  },
})

const DynamicScrollerItem = defineComponent({
  setup(_, { slots }) {
    return () => h('div', slots.default?.())
  },
})

const i18n = createI18n({
  legacy: false,
  locale: 'zh',
  messages: {
    zh: {
      common: { copy: '复制', copied: '已复制' },
      chat: {
        sourcesLabel: '来源',
        sourceDoc: '文档',
        unknownDoc: '未知文档',
        noPreview: '无预览',
        relevanceScore: '相关度 {score}',
        useful: '有用',
        notUseful: '没用',
        regenerate: '重新生成',
        backToBottom: '回到底部',
        welcome: '欢迎',
        welcomeDesc: '描述',
      },
      // 体验第四波：RunReportBlock 的 RunReport 摘要与导出按钮走 artifacts.* 键
      artifacts: {
        runReport: '运行报告',
        exportMarkdown: '导出 Markdown',
        kind: { diff: '差异', json: 'JSON', markdown: 'Markdown', text: '文本' },
        download: '下载',
      },
    },
  },
})

// Markdown 桩改为透传（体验第四波：RunReportBlock 正文从 <pre> 升级为 Markdown
// 渲染——透传桩保留「报告正文文本可见」的原断言意图）
const MarkdownPassthrough = defineComponent({
  props: { content: { type: String, default: '' } },
  setup(props) {
    return () => h('pre', props.content)
  },
})

const stubs = {
  Markdown: MarkdownPassthrough,
  'n-icon': true,
  'n-avatar': true,
  'n-button': true,
  'n-tooltip': true,
  'n-tag': true,
  'n-popover': true,
}

function mountChat(messages: ChatMessage[]) {
  return mount(ChatMessages, {
    props: { messages, isLoading: false, showBackToBottom: false, userAvatar: '', suggestions: [] },
    global: {
      plugins: [i18n],
      components: { DynamicScroller, DynamicScrollerItem },
      stubs,
    },
  })
}

const baseMsg: ChatMessage = {
  id: 1,
  content: '最终回答',
  messageType: 'assistant',
  conversationId: 1,
}

describe('ChatMessages · Agent 模式事件展示（issue #289）', () => {
  it('run_report + execution_context → 折叠块与 Tokens 徽标（与 Agent 页同款结构）', () => {
    const wrapper = mountChat([{
      ...baseMsg,
      runReport: '步骤 2/2 完成，无失败步骤',
      executionContext: { total_tokens: 14176 },
    }])
    const report = wrapper.find('.run-report')
    expect(report.exists()).toBe(true)
    expect(report.find('summary').text()).toContain('运行报告')
    expect(report.find('summary').text()).toContain('Tokens: 14176')
    expect(report.find('pre').text()).toContain('步骤 2/2 完成')
  })

  it('retry_hint → 提示行（动作映射与 Agent 页共用同一 formatter）', () => {
    const wrapper = mountChat([{
      ...baseMsg,
      events: [{ type: 'retry_hint', content: 'retry_action=reset_transport' }],
    }])
    const hint = wrapper.find('.retry-hint')
    expect(hint.exists()).toBe(true)
    expect(hint.text()).toContain('重建连接后重试')
  })

  it('未知 retry_action → 回退原文（不吞信息）', () => {
    const wrapper = mountChat([{
      ...baseMsg,
      events: [{ type: 'retry_hint', content: 'retry_action=some_future_action' }],
    }])
    expect(wrapper.find('.retry-hint').text()).toContain('retry_action=some_future_action')
  })

  it('reflection → 横幅（按 reflection_result 着色）', () => {
    const wrapper = mountChat([{
      ...baseMsg,
      events: [{ type: 'reflection', content: '需要重试', reflection_result: 'retry' }],
    }])
    const banner = wrapper.find('.reflection-banner')
    expect(banner.exists()).toBe(true)
    expect(banner.classes()).toContain('reflection-retry')
    expect(banner.text()).toContain('需要重试')
  })

  it('planSteps → 计划树（与 Agent 页同一 PlanTree 组件）', () => {
    const wrapper = mountChat([{
      ...baseMsg,
      planSteps: [
        { id: 's1', description: '搜索资料', dependencies: [], tool_hint: 'search', status: 'completed', retry_count: 0 },
        { id: 's2', description: '整理结果', dependencies: [], tool_hint: null, status: 'running', retry_count: 0 },
      ],
      planProgress: 0.5,
    }])
    const tree = wrapper.find('.plan-tree')
    expect(tree.exists()).toBe(true)
    expect(tree.text()).toContain('搜索资料')
    expect(tree.text()).toContain('1/2 步')
  })

  it('负例：无任何 extras 的普通消息 → 一个 extra 节点都不渲染', () => {
    const wrapper = mountChat([{ ...baseMsg, content: 'RAG 模式回答' }])
    expect(wrapper.find('.run-report').exists()).toBe(false)
    expect(wrapper.find('.retry-hint').exists()).toBe(false)
    expect(wrapper.find('.reflection-banner').exists()).toBe(false)
    expect(wrapper.find('.plan-tree').exists()).toBe(false)
  })

  it('负例：有 run_report 但无 execution_context → summary 不带 Tokens 段', () => {
    const wrapper = mountChat([{ ...baseMsg, runReport: '仅有报告' }])
    expect(wrapper.find('.run-report').exists()).toBe(true)
    expect(wrapper.find('.run-report summary').text()).not.toContain('Tokens')
  })
})
