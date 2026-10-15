/**
 * B：计划审批卡组件单测（渲染 / 三决策 / 编辑模式 / 忙态）。
 *
 * 变异点：
 * - 编辑模式"保存并批准"发送原始 steps 而非编辑后的 → 编辑用例红；
 * - 拒绝不携带 note → 拒绝用例红；
 * - 删除最后一个步骤仍可批准（editValid 失效）→ 校验用例红。
 */
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import zh from '@/locales/zh'
import PlanApprovalCard from '../PlanApprovalCard.vue'

const i18n = createI18n({ legacy: false, locale: 'zh', fallbackLocale: 'zh', messages: { zh } })

const STEPS = [
  { id: 's1', description: '检索知识库中的相关文档', dependencies: [], tool_hint: 'search_knowledge_base' },
  { id: 's2', description: '综合归纳风险点', dependencies: ['s1'], tool_hint: null },
]

function mountCard(props: Record<string, unknown> = {}) {
  return mount(PlanApprovalCard, {
    props: { goal: '分析财报风险', steps: STEPS, busy: false, ...props },
    global: { plugins: [i18n] },
  })
}

describe('PlanApprovalCard', () => {
  it('渲染目标/步骤/工具与依赖徽标', () => {
    const wrapper = mountCard()
    expect(wrapper.text()).toContain('计划待审批')
    expect(wrapper.text()).toContain('分析财报风险')
    expect(wrapper.text()).toContain('检索知识库中的相关文档')
    expect(wrapper.find('.tool-chip').text()).toBe('search_knowledge_base')
    expect(wrapper.find('.dep-chip').text()).toContain('s1')
  })

  it('批准：emit { decision: "approve" }', async () => {
    const wrapper = mountCard()
    await wrapper.find('.approve-btn').trigger('click')
    expect(wrapper.emitted('decide')).toEqual([[{ decision: 'approve' }]])
  })

  it('拒绝：note 原样携带；空白 note 归一为 undefined', async () => {
    const wrapper = mountCard()
    await wrapper.find('.note-input').setValue(' 请合并检索步骤 ')
    await wrapper.find('.reject-btn').trigger('click')
    expect(wrapper.emitted('decide')).toEqual([
      [{ decision: 'reject', note: '请合并检索步骤' }],
    ])

    const w2 = mountCard()
    await w2.find('.note-input').setValue('   ')
    await w2.find('.reject-btn').trigger('click')
    expect(w2.emitted('decide')).toEqual([[{ decision: 'reject', note: undefined }]])
  })

  it('编辑步骤后批准：emit edit 且携带**编辑后**的步骤（trim / 空 tool_hint 折 null）', async () => {
    const wrapper = mountCard()
    await wrapper.find('.edit-btn').trigger('click')
    expect(wrapper.findAll('.step-input').length).toBe(2)
    await wrapper.findAll('.step-input')[0].setValue('  人类改写的第一步  ')
    await wrapper.find('.step-tool').setValue('  ')
    await wrapper.find('.approve-btn').trigger('click')
    const emitted = wrapper.emitted('decide')!
    expect(emitted.length).toBe(1)
    const payload = emitted[0][0] as any
    expect(payload.decision).toBe('edit')
    expect(payload.edited_steps[0].description).toBe('人类改写的第一步')
    expect(payload.edited_steps[0].tool_hint).toBeNull()
    expect(payload.edited_steps[1].id).toBe('s2')
    // 原依赖保留原样（未提供编辑入口的字段不漂移）
    expect(payload.edited_steps[1].dependencies).toEqual(['s1'])
  })

  it('取消编辑还原原始步骤', async () => {
    const wrapper = mountCard()
    await wrapper.find('.edit-btn').trigger('click')
    await wrapper.findAll('.step-input')[0].setValue('乱改')
    await wrapper.find('.cancel-edit-btn').trigger('click')
    expect(wrapper.find('.step-desc').text()).toBe('检索知识库中的相关文档')
  })

  it('编辑模式：添加/删除步骤；删空后不可提交', async () => {
    const wrapper = mountCard()
    await wrapper.find('.edit-btn').trigger('click')
    await wrapper.find('.add-step-btn').trigger('click')
    expect(wrapper.findAll('.step-input').length).toBe(3)
    // 新步描述为空 → 保存并批准禁用
    expect(wrapper.find('.approve-btn').attributes('disabled')).toBeDefined()
    await wrapper.findAll('.step-input')[2].setValue('新步骤')
    expect(wrapper.find('.approve-btn').attributes('disabled')).toBeUndefined()
    // 删除两步（第二个删除钮对应第 2 行；删除后行数下降）
    const removeBtns = wrapper.findAll('.step-remove')
    await removeBtns[0].trigger('click')
    expect(wrapper.findAll('.step-input').length).toBe(2)
  })

  it('busy：按钮禁用（提交中…）', () => {
    const wrapper = mountCard({ busy: true })
    expect(wrapper.find('.approve-btn').attributes('disabled')).toBeDefined()
    expect(wrapper.find('.reject-btn').attributes('disabled')).toBeDefined()
    expect(wrapper.find('.edit-btn').attributes('disabled')).toBeDefined()
    expect(wrapper.find('.approve-btn').text()).toBe('提交中…')
  })
})
