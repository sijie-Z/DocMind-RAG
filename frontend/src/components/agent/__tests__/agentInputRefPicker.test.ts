/**
 * 体验第四波 @引用 —— AgentInput 选择层判别：
 * 输入 @ 触发弹层 → 搜索知识库（防抖）→ 多选成 chips → 发送时随
 * referenced_document_ids 上送；Enters 在弹层打开时优先选中而非发送。
 *
 * 变异点：updatePicker 的"@ 前须行首/空白"守卫删掉 → 邮箱负例红；
 * handleEnter 去掉弹层分支 → 「Enter 选中不发送」用例红；
 * handleSend 不清理 selectedDocs → chips 残留用例红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import zh from '@/locales/zh'
import AgentInput from '@/components/agent/AgentInput.vue'

const getKnowledgeBases = vi.fn()
vi.mock('@/api/knowledge', () => ({
  getKnowledgeBases: (...args: unknown[]) => getKnowledgeBases(...args),
}))

const i18n = createI18n({ legacy: false, locale: 'zh', fallbackLocale: 'zh', messages: { zh } })

function mountInput(props: Record<string, unknown> = {}) {
  return mount(AgentInput, {
    props: { placeholder: '输入', ...props },
    global: { plugins: [i18n] },
  })
}

function docList(items: Array<{ id: string; title: string }>) {
  return { data: { data: { data: items, total: items.length } } }
}

async function typeAndSettle(wrapper: ReturnType<typeof mountInput>, text: string) {
  await wrapper.find('textarea').setValue(text)
  // 真实计时等过 250ms 防抖窗口（不用 fake timers：VTU flushPromises 依赖
  // 真实 setTimeout，假时钟下会悬挂）
  await new Promise((r) => setTimeout(r, 320))
  await flushPromises()
}

beforeEach(() => {
  getKnowledgeBases.mockReset()
  getKnowledgeBases.mockResolvedValue(docList([
    { id: 'd1', title: '预算报告' },
    { id: 'd2', title: '销售手册' },
  ]))
})

describe('AgentInput · @引用选择层', () => {
  it('输入 @ → 打开弹层并搜索（search 参数为 @ 后的词）', async () => {
    const wrapper = mountInput()
    await typeAndSettle(wrapper, '@')
    expect(wrapper.find('[data-testid="ref-picker"]').exists()).toBe(true)
    expect(getKnowledgeBases).toHaveBeenCalledTimes(1)
    expect(getKnowledgeBases.mock.calls[0][0]).toMatchObject({ search: undefined })

    await typeAndSettle(wrapper, '@预')
    expect(wrapper.find('[data-testid="ref-option"]').exists()).toBe(true)
    expect(getKnowledgeBases.mock.calls[1][0]).toMatchObject({ search: '预' })
    const options = wrapper.findAll('[data-testid="ref-option"]')
    expect(options).toHaveLength(2)
    expect(options[0].text()).toContain('预算报告')
  })

  it('点击选项 → 注入 chip，输入框中的 @token 被移除', async () => {
    const wrapper = mountInput()
    await typeAndSettle(wrapper, '请总结 @预')
    await wrapper.findAll('[data-testid="ref-option"]')[0].trigger('mousedown')
    await flushPromises()

    expect(wrapper.find('[data-testid="ref-picker"]').exists()).toBe(false)
    const chips = wrapper.findAll('[data-testid="ref-chip"]')
    expect(chips).toHaveLength(1)
    expect(chips[0].text()).toContain('预算报告')
    expect((wrapper.find('textarea').element as HTMLTextAreaElement).value).toBe('请总结 ')
  })

  it('多选 + 逐个移除', async () => {
    const wrapper = mountInput()
    await typeAndSettle(wrapper, '@')
    await wrapper.findAll('[data-testid="ref-option"]')[0].trigger('mousedown')
    await typeAndSettle(wrapper, '再看看 @')
    await wrapper.findAll('[data-testid="ref-option"]')[1].trigger('mousedown')
    await flushPromises()
    expect(wrapper.findAll('[data-testid="ref-chip"]')).toHaveLength(2)

    await wrapper.findAll('[data-testid="ref-chip-remove"]')[0].trigger('click')
    const chips = wrapper.findAll('[data-testid="ref-chip"]')
    expect(chips).toHaveLength(1)
    expect(chips[0].text()).toContain('销售手册')
  })

  it('弹层打开时 Enter 选中当前项而非发送', async () => {
    const wrapper = mountInput()
    await typeAndSettle(wrapper, '@')
    await wrapper.find('textarea').trigger('keydown.enter')
    await flushPromises()

    expect(wrapper.emitted('send')).toBeUndefined()
    expect(wrapper.findAll('[data-testid="ref-chip"]')).toHaveLength(1)
  })

  it('键盘 ↓ 切换高亮 + Escape 关层', async () => {
    const wrapper = mountInput()
    await typeAndSettle(wrapper, '@')
    await wrapper.find('textarea').trigger('keydown.down')
    const options = wrapper.findAll('[data-testid="ref-option"]')
    expect(options[1].classes()).toContain('active')

    await wrapper.find('textarea').trigger('keydown.esc')
    expect(wrapper.find('[data-testid="ref-picker"]').exists()).toBe(false)
  })

  it('发送：send 事件携带 refs，且 chips 清空', async () => {
    const wrapper = mountInput()
    await typeAndSettle(wrapper, '请总结 @预')
    await wrapper.findAll('[data-testid="ref-option"]')[0].trigger('mousedown')
    await typeAndSettle(wrapper, '请总结 这段')

    await wrapper.find('.send-btn').trigger('click')
    const emitted = wrapper.emitted('send')
    expect(emitted).toHaveLength(1)
    expect(emitted![0][0]).toBe('请总结 这段')
    expect(emitted![0][1]).toEqual([{ id: 'd1', title: '预算报告' }])
    expect(wrapper.find('[data-testid="ref-chips"]').exists()).toBe(false)
  })

  it('负例：邮箱样 a@b 不触发；token 后带空格即关层', async () => {
    const wrapper = mountInput()
    await typeAndSettle(wrapper, '联系 a@b')
    expect(wrapper.find('[data-testid="ref-picker"]').exists()).toBe(false)

    await typeAndSettle(wrapper, '@预 ')
    expect(wrapper.find('[data-testid="ref-picker"]').exists()).toBe(false)
    expect(getKnowledgeBases).not.toHaveBeenCalled()
  })

  it('负例：转向模式（steering）不开放选择层', async () => {
    const wrapper = mountInput({ steering: true })
    await typeAndSettle(wrapper, '@预')
    expect(wrapper.find('[data-testid="ref-picker"]').exists()).toBe(false)
    expect(getKnowledgeBases).not.toHaveBeenCalled()
  })

  it('搜索失败 → 诚实报错行（不静默）', async () => {
    getKnowledgeBases.mockRejectedValue(new Error('boom'))
    const wrapper = mountInput()
    await typeAndSettle(wrapper, '@预')
    expect(wrapper.find('[data-testid="ref-picker-error"]').exists()).toBe(true)
  })
})
