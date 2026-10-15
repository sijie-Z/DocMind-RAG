/**
 * 回放时间线组件（后端 timeline 的诚实渲染：步骤/工具/耗时/tokens + 失败醒目）。
 *
 * 变异点（前端判别）：
 * - 失败步骤不再醒目（丢 step-failed 类或错误行）→ 失败步骤用例红；
 * - 空数据时不渲染诚实空态（静默或编造）→ 空态用例红。
 */
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import i18n from '@/locales'

import ReplayTimeline from '../ReplayTimeline.vue'
import type { ReplayTimeline as ReplayTimelineData } from '@/types/agent'

const TIMELINE: ReplayTimelineData = {
  task_id: 'abc123def456',
  query: '分析文档',
  goal: '产出结论',
  plan_summary: '检索 → 汇总',
  steps_completed: 1,
  steps: [
    {
      step_id: 's1', description: '检索资料', tool_used: 'search_knowledge_base',
      status: 'completed', result_summary: '找到 3 条', error: null,
      duration_ms: 125.5, tokens_used: 42,
    },
    {
      step_id: 's2', description: '汇总输出', tool_used: 'generate_report',
      status: 'failed', result_summary: null, error: 'boom',
      duration_ms: 30, tokens_used: 0,
    },
  ],
  findings: [
    { source_step: 's1', content: '关键事实', source_tool: 'search_knowledge_base', confidence: 'high' },
  ],
  decisions: [
    { phase: 'execute', action: 'retry_step', reasoning: '第一次超时', triggered_by: null },
  ],
  failures: ['[s2] boom'],
  duration_ms: 1234.5,
  total_tokens: 42,
  progress: 1,
  start_time: 0,
}

function mountC(props: {
  taskId: string
  timeline?: ReplayTimelineData | null
  loading?: boolean
  error?: '' | 'not_found' | 'failed'
}) {
  return mount(ReplayTimeline, { props, global: { plugins: [i18n] } })
}

describe('ReplayTimeline · 时间线渲染', () => {
  it('步骤序列：描述/工具/耗时/tokens 齐全', () => {
    const w = mountC({ taskId: 'abc123def456', timeline: TIMELINE })
    expect(w.findAll('.replay-step')).toHaveLength(2)
    expect(w.text()).toContain('检索资料')
    expect(w.text()).toContain('search_knowledge_base')
    expect(w.text()).toContain('126ms') // 125.5ms 四舍五入
    expect(w.text()).toContain('42') // 步骤 tokens
    expect(w.text()).toContain('1.2s') // 头部总耗时徽标
    w.unmount()
  })

  it('失败步骤醒目：step-failed 类 + 错误文本 + failures 汇总条', () => {
    const w = mountC({ taskId: 'abc123def456', timeline: TIMELINE })
    const failed = w.findAll('.replay-step.step-failed')
    expect(failed).toHaveLength(1)
    expect(failed[0].text()).toContain('汇总输出')
    expect(failed[0].text()).toContain('boom')
    expect(w.find('.replay-failures').exists()).toBe(true)
    w.unmount()
  })

  it('空回放：诚实空态，不渲染任何编造步骤', () => {
    const w = mountC({ taskId: 'gone-task', timeline: null, error: 'not_found' })
    expect(w.text()).toContain('该任务没有可回放的数据')
    expect(w.findAll('.replay-step')).toHaveLength(0)
    expect(w.find('.replay-failures').exists()).toBe(false)
    w.unmount()
  })

  it('loading / failed 两态文案', () => {
    const loading = mountC({ taskId: 'x', timeline: null, loading: true })
    expect(loading.text()).toContain('正在加载回放')
    loading.unmount()
    const failed = mountC({ taskId: 'x', timeline: null, error: 'failed' })
    expect(failed.text()).toContain('回放加载失败')
    failed.unmount()
  })

  it('findings / decisions 分区渲染', () => {
    const w = mountC({ taskId: 'abc123def456', timeline: TIMELINE })
    expect(w.text()).toContain('关键发现')
    expect(w.text()).toContain('关键事实')
    expect(w.text()).toContain('关键决策')
    expect(w.text()).toContain('retry_step')
    w.unmount()
  })

  it('关闭按钮发出 close 事件', async () => {
    const w = mountC({ taskId: 'abc123def456', timeline: TIMELINE })
    await w.find('.replay-close').trigger('click')
    expect(w.emitted('close')).toBeTruthy()
    w.unmount()
  })
})
