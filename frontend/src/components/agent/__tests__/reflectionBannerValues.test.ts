/**
 * ReflectionBanner 六值域判别用例（UI 验收 round2 · B28）。
 *
 * 后端 ReflectionResult 六态（backend/app/agent/events.py:39-42），降级两态
 * unavailable/unparsed 实际可发射（reflector.py:197-203/226-232）。旧组件只
 * 认 pass/retry/replan/escalate 四值 → 两降级值落 default 分支：图标 '❓'
 * 且 class 'reflection-unavailable' 无任何 CSS 规则 → 横幅退化成无底色裸文本。
 *
 * 变异点：删 case 'unavailable' / case 'unparsed'（或 CSS 类）→ 对应用例红。
 */
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'

import ReflectionBanner from '../ReflectionBanner.vue'

const mountBanner = (result: string) =>
  mount(ReflectionBanner, {
    props: { result: result as never, text: '本轮未获得完成度判定' },
  })

describe('ReflectionBanner · 六值域（B28）', () => {
  it('unavailable（评估服务不可用）：专属图标 + 专属 class（不再无样式裸文本）', () => {
    const wrapper = mountBanner('unavailable')
    expect(wrapper.find('.reflection-banner').classes()).toContain('reflection-unavailable')
    expect(wrapper.find('.reflection-icon').text()).toBe('⏸️')
    expect(wrapper.text()).toContain('本轮未获得完成度判定')
  })

  it('unparsed（模型输出无可用判定）：专属图标 + 专属 class', () => {
    const wrapper = mountBanner('unparsed')
    expect(wrapper.find('.reflection-banner').classes()).toContain('reflection-unparsed')
    expect(wrapper.find('.reflection-icon').text()).toBe('❔')
  })

  it('四主态回归：pass/retry/replan/escalate 图标与 class 不变', () => {
    const cases: Array<[string, string]> = [
      ['pass', '✅'],
      ['retry', '🔄'],
      ['replan', '📝'],
      ['escalate', '⚠️'],
    ]
    for (const [result, icon] of cases) {
      const wrapper = mountBanner(result)
      expect(wrapper.find('.reflection-icon').text()).toBe(icon)
      expect(wrapper.find('.reflection-banner').classes()).toContain(`reflection-${result}`)
    }
  })
})
