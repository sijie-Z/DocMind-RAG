/**
 * issue #289 G3/G7：PlanTree 的「重试 N 次」徽标与计划语义徽标。
 *
 * - G3：retry_count 此前恒 0（store 建步时不写、无任何 case 更新），徽标永不
 *   显示；修复后由 thinking/tool_error/retry_hint 的 retry_attempt 抬升
 *   （store 侧用例见 stores/__tests__/agent.test.ts，本文件钉渲染）。
 * - G7：parallel_group / risk_level / retry_strategy / fallback_tool 由
 *   plan_step 事件落账后按非默认值出徽标；默认值（low / auto_retry / 无分组）
 *   不出徽标（负例）。
 *
 * 变异点：删对应 v-if 徽标 → 用例红；去掉默认值 gating → 负例红。
 */
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import PlanTree from '../PlanTree.vue'
import type { PlanStep } from '@/types/agent'

function makeStep(overrides: Partial<PlanStep> = {}): PlanStep {
  return {
    id: 's1',
    description: '搜索资料',
    dependencies: [],
    tool_hint: 'search',
    status: 'running',
    retry_count: 0,
    ...overrides,
  }
}

describe('PlanTree · 重试计数与计划语义徽标（issue #289 G3/G7）', () => {
  it('retry_count > 0 → 「重试 N 次」徽标（G3：此前恒 0 永不显示）', () => {
    const wrapper = mount(PlanTree, { props: { steps: [makeStep({ retry_count: 2 })] } })
    expect(wrapper.find('.step-retry').text()).toContain('重试 2 次')
  })

  it('retry_count = 0 → 无重试徽标（负例）', () => {
    const wrapper = mount(PlanTree, { props: { steps: [makeStep()] } })
    expect(wrapper.find('.step-retry').exists()).toBe(false)
  })

  it('risk_level=high/medium → 风险徽标；low/缺省 → 无（G7）', () => {
    const high = mount(PlanTree, { props: { steps: [makeStep({ risk_level: 'high' })] } })
    expect(high.find('.step-risk').text()).toContain('高风险')

    const medium = mount(PlanTree, { props: { steps: [makeStep({ risk_level: 'medium' })] } })
    expect(medium.find('.step-risk').text()).toContain('中风险')

    const low = mount(PlanTree, { props: { steps: [makeStep({ risk_level: 'low' })] } })
    expect(low.find('.step-risk').exists()).toBe(false)
  })

  it('retry_strategy 非默认值 → 文案徽标；auto_retry → 无（G7）', () => {
    const skip = mount(PlanTree, { props: { steps: [makeStep({ retry_strategy: 'skip' })] } })
    expect(skip.find('.step-retry-strategy').text()).toContain('失败时跳过')

    const ask = mount(PlanTree, { props: { steps: [makeStep({ retry_strategy: 'ask_user' })] } })
    expect(ask.find('.step-retry-strategy').text()).toContain('失败时询问')

    const auto = mount(PlanTree, { props: { steps: [makeStep({ retry_strategy: 'auto_retry' })] } })
    expect(auto.find('.step-retry-strategy').exists()).toBe(false)
  })

  it('fallback_tool / parallel_group → 徽标；缺省 → 无（G7）', () => {
    const withBoth = mount(PlanTree, {
      props: { steps: [makeStep({ fallback_tool: 'web_search', parallel_group: 2 })] },
    })
    expect(withBoth.find('.step-fallback').text()).toContain('web_search')
    expect(withBoth.find('.step-parallel').text()).toContain('并行组 2')

    const bare = mount(PlanTree, { props: { steps: [makeStep()] } })
    expect(bare.find('.step-fallback').exists()).toBe(false)
    expect(bare.find('.step-parallel').exists()).toBe(false)
  })
})
