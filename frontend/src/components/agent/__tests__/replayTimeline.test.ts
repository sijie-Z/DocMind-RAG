/**
 * 回放时间线组件（后端 timeline 的诚实渲染：步骤/工具/耗时/tokens + 失败醒目）。
 *
 * 变异点（前端判别）：
 * - 失败步骤不再醒目（丢 step-failed 类或错误行）→ 失败步骤用例红；
 * - 空数据时不渲染诚实空态（静默或编造）→ 空态用例红；
 * - 表头 chips 改回直接插值（total_tokens / duration_ms 为 0 时显示 0/0ms）
 *   → 「表头汇总未知值判别」红；
 * - isKnownMetric 删掉 Number.isFinite 或 value > 0 → 非数语义/占位 0 用例红；
 * - fmtMs 删掉 <1ms 分支 → 亚毫秒用例红（0.4ms 显示成 0ms）；
 * - step-idle 恢复整行 opacity → 源码级可读性契约用例红。
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import i18n from '@/locales'
import zh from '@/locales/zh'
import en from '@/locales/en'
import ja from '@/locales/ja'
import fr from '@/locales/fr'

import ReplayTimeline from '../ReplayTimeline.vue'
import type { ReplayTimeline as ReplayTimelineData } from '@/types/agent'

type Locale = 'zh' | 'en' | 'ja' | 'fr'
const MESSAGES = { zh, en, ja, fr }

function mountLocalized(props: {
  taskId: string
  timeline?: ReplayTimelineData | null
  loading?: boolean
  error?: '' | 'not_found' | 'failed'
}, locale: Locale = 'zh') {
  const i = createI18n({ legacy: false, locale, fallbackLocale: 'en', messages: MESSAGES })
  return mount(ReplayTimeline, { props, global: { plugins: [i] } })
}

function withSteps(steps: ReplayTimelineData['steps']): ReplayTimelineData {
  return { ...TIMELINE, steps }
}

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

describe('ReplayTimeline · 每步 token / 耗时可见性', () => {
  const stepWithMetrics = {
    step_id: 's1', description: '检索资料', tool_used: null,
    status: 'completed', result_summary: null, error: null,
    duration_ms: 125.5, tokens_used: 42,
  }

  it('有真实数值时，每步渲染数字与标签（不依赖整轮汇总）', () => {
    const w = mountLocalized({ taskId: 'x', timeline: withSteps([stepWithMetrics]) })
    const step = w.find('.replay-step')
    expect(step.findAll('.step-metric')).toHaveLength(2)
    expect(step.text()).toContain('耗时')
    expect(step.text()).toContain('126ms')
    expect(step.text()).toContain('Token 数')
    expect(step.text()).toContain('42')
    // 缺字段的用例必须能判别：本步没有 — 占位
    expect(step.text()).not.toContain('—')
    w.unmount()
  })

  it('缺字段（undefined / null）时显示 — 而不是 0 或空白', () => {
    const w = mountLocalized({
      taskId: 'x',
      timeline: withSteps([{ ...stepWithMetrics, duration_ms: undefined as any, tokens_used: null as any }]),
    })
    const metrics = w.find('.replay-step').findAll('.metric-value')
    expect(metrics).toHaveLength(2)
    expect(metrics[0].text()).toContain('—')
    expect(metrics[1].text()).toContain('—')
    expect(w.find('.replay-step').text()).not.toMatch(/耗时\s*0ms/)
    expect(w.find('.replay-step').text()).not.toMatch(/Token 数\s*0(?:\D|$)/)
    w.unmount()
  })

  it('后端占位 0 视作未知：不伪造 0', () => {
    const w = mountLocalized({
      taskId: 'x',
      timeline: withSteps([{ ...stepWithMetrics, duration_ms: 0, tokens_used: 0 }]),
    })
    const metrics = w.find('.replay-step').findAll('.metric-value')
    expect(metrics[0].text()).toContain('—')
    expect(metrics[1].text()).toContain('—')
    w.unmount()
  })

  it('四语切换：token / 耗时 / 不可用文案随 i18n 变化', () => {
    const expected: Record<Locale, { tokens: string; latency: string; unavailable: string }> = {
      zh: { tokens: 'Token 数', latency: '耗时', unavailable: '数据不可用' },
      en: { tokens: 'Tokens', latency: 'Latency', unavailable: 'Unavailable' },
      ja: { tokens: 'トークン', latency: '所要時間', unavailable: 'データなし' },
      fr: { tokens: 'Jetons', latency: 'Durée', unavailable: 'Indisponible' },
    }
    const unknown = { ...stepWithMetrics, duration_ms: 0, tokens_used: undefined as any }
    for (const locale of ['zh', 'en', 'ja', 'fr'] as Locale[]) {
      const w = mountLocalized({ taskId: 'x', timeline: withSteps([unknown]) }, locale)
      const step = w.find('.replay-step').text()
      expect(step, `${locale} token 标签`).toContain(expected[locale].tokens)
      expect(step, `${locale} 耗时标签`).toContain(expected[locale].latency)
      expect(step, `${locale} 不可用文案`).toContain(expected[locale].unavailable)
      w.unmount()
    }
  })

  it('整轮汇总仍保留：步骤指标不吞掉头部 tokens / 总耗时', () => {
    const w = mountLocalized({ taskId: 'x', timeline: TIMELINE })
    expect(w.find('.replay-chips').text()).toContain('1.2s')
    expect(w.find('.replay-chips').text()).toContain('42')
    expect(w.findAll('.replay-step .step-metric')).toHaveLength(4)
    w.unmount()
  })

  it('表头汇总未知值判别：duration_ms=0 与 total_tokens=0 时 chips 必须含 —', () => {
    // 变异：chips 改回 {{ fmtMs(timeline.duration_ms) }} / {{ timeline.total_tokens }}
    // 时，下面两条 toContain('—') 即红（此前 11/11 全绿抓不到）。
    const w = mountLocalized({ taskId: 'x', timeline: { ...TIMELINE, duration_ms: 0, total_tokens: 0 } })
    const chips = w.findAll('.replay-chips .replay-chip')
    expect(chips, 'chips 顺序：步骤 / 耗时 / tokens').toHaveLength(3)
    expect(chips[1].text()).toContain('—')
    expect(chips[2].text()).toContain('—')
    expect(chips[1].text()).not.toMatch(/0ms|<1ms/)
    expect(chips[2].text()).not.toMatch(/(?:^|\s)0(?:\s|$)/)
    w.unmount()
  })

  it('非数语义：NaN / Infinity 都按未知显示 —（isFinite 与 > 0 双重拦截）', () => {
    for (const bad of [Number.NaN, Number.POSITIVE_INFINITY]) {
      const w = mountLocalized({
        taskId: 'x',
        timeline: withSteps([{ ...stepWithMetrics, duration_ms: bad, tokens_used: bad }]),
      })
      const metrics = w.find('.replay-step').findAll('.metric-value')
      expect(metrics[0].text(), String(bad)).toContain('—')
      expect(metrics[1].text(), String(bad)).toContain('—')
      w.unmount()
    }
  })

  it('真实亚毫秒值显示 <1ms：既不为 0 也不误判为未知', () => {
    const w = mountLocalized({
      taskId: 'x',
      timeline: withSteps([{ ...stepWithMetrics, duration_ms: 0.4 }]),
    })
    const value = w.find('.replay-step').findAll('.metric-value')[0]
    expect(value.text()).toBe('<1ms')
    expect(value.text()).not.toContain('—')
    expect(w.find('.replay-step').text()).not.toContain('0ms')
    w.unmount()
  })
})

describe('ReplayTimeline · step-idle 可读性契约（源码级）', () => {
  const SRC = readFileSync(resolve(process.cwd(), 'src/components/agent/ReplayTimeline.vue'), 'utf-8')

  it('跳过/待执行行不整行压暗：opacity 只作用于非指标元素', () => {
    // 变异：恢复 `.replay-step.step-idle { opacity: .65 }` → 第一条红
    //（整行 opacity 会把指标 #6b7280 叠成 #9fa3ac，白底仅 2.53:1）。
    expect(SRC).not.toMatch(/\.replay-step\.step-idle\s*\{[^}]*opacity/)
    const dimBlock = SRC.match(/\.replay-step\.step-idle[^{]*\{[^}]*opacity[^}]*\}/)
    expect(dimBlock, '应存在按子元素压暗的 step-idle 规则').toBeTruthy()
    expect(dimBlock![0]).not.toContain('.step-metric')
    expect(dimBlock![0]).not.toContain('.metric-label')
  })
})
