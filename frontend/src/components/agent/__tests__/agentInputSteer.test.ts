/**
 * A：运行中转向——AgentInput 组件单元（按钮/Enter/Emit 语义）。
 *
 * 变异点：
 * - steering 模式仍 emit('send') → 转向用例红；
 * - waiting_approval（loading=false + steering=true）时仍显示"发送"按钮 → 红；
 * - steering 时 Enter 不走 steer → Enter 用例红。
 */
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import zh from '@/locales/zh'
import AgentInput from '../AgentInput.vue'

const i18n = createI18n({ legacy: false, locale: 'zh', fallbackLocale: 'zh', messages: { zh } })

function mountInput(props: Record<string, unknown> = {}) {
  return mount(AgentInput, {
    props: { placeholder: 'p', disabled: false, ...props },
    global: { plugins: [i18n] },
  })
}

describe('AgentInput · 运行中转向', () => {
  it('running（loading+steering）：显示"发送指导"与"停止"，点击 emit steer', async () => {
    const wrapper = mountInput({ loading: true, steering: true })
    const steerBtn = wrapper.find('[data-testid="steer-btn"]')
    expect(steerBtn.exists()).toBe(true)
    expect(steerBtn.text()).toBe('发送指导')
    expect(wrapper.find('.stop-btn').exists()).toBe(true)
    expect(wrapper.find('.send-btn').exists()).toBe(false)

    await wrapper.find('.agent-textarea').setValue('只查 2024 年后的文档')
    await steerBtn.trigger('click')
    expect(wrapper.emitted('steer')).toEqual([['只查 2024 年后的文档']])
    expect(wrapper.emitted('send')).toBeUndefined()
    // 发送后清空输入
    expect((wrapper.find('.agent-textarea').element as HTMLTextAreaElement).value).toBe('')
  })

  it('running 时 Enter 发送指导（而非 send）', async () => {
    const wrapper = mountInput({ loading: true, steering: true })
    const ta = wrapper.find('.agent-textarea')
    await ta.setValue('调整方向')
    await ta.trigger('keydown.enter')
    expect(wrapper.emitted('steer')).toEqual([['调整方向']])
    expect(wrapper.emitted('send')).toBeUndefined()
  })

  it('waiting_approval（loading=false + steering=true）：发送被"发送指导"替换（无停止钮）', async () => {
    const wrapper = mountInput({ loading: false, steering: true })
    expect(wrapper.find('[data-testid="steer-btn"]').exists()).toBe(true)
    expect(wrapper.find('.send-btn').exists()).toBe(false)
    expect(wrapper.find('.stop-btn').exists()).toBe(false)
    // 提示行切换为转向语义
    expect(wrapper.find('.input-hint').text()).toContain('不打断当前步骤')
  })

  it('非 steering：维持既有"发送"按钮与 Enter→send 契约', async () => {
    const wrapper = mountInput({ loading: false, steering: false })
    expect(wrapper.find('.send-btn').exists()).toBe(true)
    expect(wrapper.find('[data-testid="steer-btn"]').exists()).toBe(false)
    await wrapper.find('.agent-textarea').setValue('新任务')
    await wrapper.find('.agent-textarea').trigger('keydown.enter')
    expect(wrapper.emitted('send')).toEqual([['新任务']])
    expect(wrapper.emitted('steer')).toBeUndefined()
  })

  it('空文本不提交（steer 按钮禁用语义）', async () => {
    const wrapper = mountInput({ loading: true, steering: true })
    const steerBtn = wrapper.find('[data-testid="steer-btn"]')
    expect(steerBtn.attributes('disabled')).toBeDefined()
    await wrapper.find('.agent-textarea').setValue('   ')
    await steerBtn.trigger('click')
    expect(wrapper.emitted('steer')).toBeUndefined()
  })

  it('评审 MINOR-B：disabled + hintOverride —— 输入禁用、发送禁用、提示替换', async () => {
    // 场景：B 计划审批等待中——视图层用 disabled 闸门防"发消息丢任务指针"，
    // 提示行说明该先处理计划卡。
    const wrapper = mountInput({ disabled: true, hintOverride: '任务正在等待计划审批：请先处理计划卡' })
    expect(wrapper.find('.agent-textarea').attributes('disabled')).toBeDefined()
    expect(wrapper.find('.send-btn').attributes('disabled')).toBeDefined()
    expect(wrapper.find('.input-hint').text()).toBe('任务正在等待计划审批：请先处理计划卡')

    // 即便尝试输入并回车：disabled 闸门拦下，不 emit send/steer
    await wrapper.find('.agent-textarea').setValue('x')
    await wrapper.find('.agent-textarea').trigger('keydown.enter')
    expect(wrapper.emitted('send')).toBeUndefined()
    expect(wrapper.emitted('steer')).toBeUndefined()
  })
})
