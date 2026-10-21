/**
 * HITL 审批卡组件测试：工具名 / 参数 / risk 徽标 / 批准 / 拒绝 / 改指令输入框。
 *
 * 变异点：
 * - 去掉 argsText 渲染或 risk 徽标 → 对应用例红；
 * - emitDecision 不带 modifiedInstruction → "改指令"用例红；
 * - expired/busy 不禁用按钮 → 对应用例红。
 */
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ApprovalCard from '../ApprovalCard.vue'

function mountCard(props: Record<string, unknown> = {}) {
  return mount(ApprovalCard, {
    props: {
      toolName: 'execute_python',
      toolArgs: { code: 'print(1)' },
      riskLevel: 'high',
      reason: '高风险工具 execute_python（风险等级 high）执行前需人工审批',
      ...props,
    },
  })
}

describe('ApprovalCard · HITL 审批卡', () => {
  it('渲染工具名 / 参数 / 高风险徽标 / 审批理由', () => {
    const w = mountCard()
    expect(w.text()).toContain('execute_python')
    expect(w.text()).toContain('"code": "print(1)"')
    expect(w.find('.risk-badge').text()).toBe('高风险')
    expect(w.find('.risk-badge').classes()).toContain('risk-high')
    expect(w.text()).toContain('执行前需人工审批')
  })

  it('risk_level=medium 显示中风险徽标', () => {
    const w = mountCard({ riskLevel: 'medium' })
    expect(w.find('.risk-badge').text()).toBe('中风险')
  })

  it('点批准 → decide(approve)（无改指令时不携带 instruction）', async () => {
    const w = mountCard()
    await w.find('.approve-btn').trigger('click')
    const events = w.emitted('decide')
    expect(events).toBeTruthy()
    expect(events![0][0]).toEqual({ decision: 'approve' })
  })

  it('输入改指令后点批准 → decide(approve, modifiedInstruction)', async () => {
    const w = mountCard()
    await w.find('.instruction-input').setValue('  只允许查询 2024 年数据  ')
    await w.find('.approve-btn').trigger('click')
    expect(w.emitted('decide')![0][0]).toEqual({
      decision: 'approve',
      modifiedInstruction: '只允许查询 2024 年数据',
    })
  })

  it('点拒绝 → decide(deny)；输入内容作为 note', async () => {
    const w = mountCard()
    await w.find('.deny-btn').trigger('click')
    expect(w.emitted('decide')![0][0]).toEqual({ decision: 'deny' })

    const w2 = mountCard()
    await w2.find('.instruction-input').setValue('生产环境禁止')
    await w2.find('.deny-btn').trigger('click')
    expect(w2.emitted('decide')![0][0]).toEqual({ decision: 'deny', note: '生产环境禁止' })
  })

  it('busy 时按钮禁用并显示提交中', () => {
    const w = mountCard({ busy: true })
    expect(w.find('.approve-btn').attributes('disabled')).toBeDefined()
    expect(w.find('.deny-btn').attributes('disabled')).toBeDefined()
    expect(w.find('.approve-btn').text()).toContain('提交中')
  })

  it('expired 时显示已超时徽标且按钮禁用', () => {
    const w = mountCard({ expired: true })
    expect(w.find('.expired-badge').text()).toBe('已超时')
    expect(w.find('.approve-btn').attributes('disabled')).toBeDefined()
  })
})
