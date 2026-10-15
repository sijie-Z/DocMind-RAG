/**
 * 体验第四波 斜杠命令 —— AgentInput 命令菜单判别：
 * 输入 / 触发；前缀过滤；选中本地展开为输入模板（映射既有能力）；
 * /clear 清空输入；/help 展开全量说明；无匹配时 Enter 如实发送原文。
 *
 * 变异点：handleEnter 去掉 cmd 分支 → 「Enter 展开模板」用例红；
 * applyCommand 不替换 inputText → 模板展开断言红。
 */
import { describe, it, expect, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import zh from '@/locales/zh'
import AgentInput from '@/components/agent/AgentInput.vue'

vi.mock('@/api/knowledge', () => ({
  getKnowledgeBases: vi.fn().mockResolvedValue({ data: { data: { data: [] } } }),
}))

const i18n = createI18n({ legacy: false, locale: 'zh', fallbackLocale: 'zh', messages: { zh } })

function mountInput(props: Record<string, unknown> = {}) {
  return mount(AgentInput, {
    props: { placeholder: '输入', ...props },
    global: { plugins: [i18n] },
  })
}

async function typeCmd(wrapper: ReturnType<typeof mountInput>, text: string) {
  await wrapper.find('textarea').setValue(text)
  await flushPromises()
}

describe('AgentInput · 斜杠命令菜单', () => {
  it('输入 / → 菜单打开（5 条命令，含名称与描述）', async () => {
    const wrapper = mountInput()
    await typeCmd(wrapper, '/')
    expect(wrapper.find('[data-testid="cmd-menu"]').exists()).toBe(true)
    const options = wrapper.findAll('[data-testid="cmd-option"]')
    expect(options).toHaveLength(5)
    expect(options[0].text()).toContain('/search')
    expect(options[0].text()).toContain('web_search')
  })

  it('前缀过滤：/se → 仅 search；/doc → 仅 doc', async () => {
    const wrapper = mountInput()
    await typeCmd(wrapper, '/se')
    let options = wrapper.findAll('[data-testid="cmd-option"]')
    expect(options).toHaveLength(1)
    expect(options[0].text()).toContain('/search')

    await typeCmd(wrapper, '/doc')
    options = wrapper.findAll('[data-testid="cmd-option"]')
    expect(options).toHaveLength(1)
    expect(options[0].text()).toContain('/doc')
  })

  it('Enter 选中 search → 展开为输入模板且参数保留（不发送）', async () => {
    const wrapper = mountInput()
    await typeCmd(wrapper, '/search 预算')
    await wrapper.find('textarea').trigger('keydown.enter')
    await flushPromises()

    expect(wrapper.emitted('send')).toBeUndefined()
    expect((wrapper.find('textarea').element as HTMLTextAreaElement).value)
      .toBe('联网搜索并总结：预算')
    expect(wrapper.find('[data-testid="cmd-menu"]').exists()).toBe(false)
  })

  it('点击选中 doc → 模板展开（含空格参数）', async () => {
    const wrapper = mountInput()
    await typeCmd(wrapper, '/doc 合同条款')
    await wrapper.find('[data-testid="cmd-option"]').trigger('mousedown')
    await flushPromises()
    expect((wrapper.find('textarea').element as HTMLTextAreaElement).value)
      .toBe('基于知识库回答：合同条款')
  })

  it('/clear + Enter → 清空输入框，不发送', async () => {
    const wrapper = mountInput()
    await typeCmd(wrapper, '/clear')
    await wrapper.find('textarea').trigger('keydown.enter')
    await flushPromises()
    expect((wrapper.find('textarea').element as HTMLTextAreaElement).value).toBe('')
    expect(wrapper.emitted('send')).toBeUndefined()
  })

  it('/help + Enter → 展开全量命令与说明并保持打开', async () => {
    const wrapper = mountInput()
    await typeCmd(wrapper, '/help')
    let options = wrapper.findAll('[data-testid="cmd-option"]')
    expect(options).toHaveLength(1)

    await wrapper.find('textarea').trigger('keydown.enter')
    await flushPromises()
    options = wrapper.findAll('[data-testid="cmd-option"]')
    expect(options).toHaveLength(5)
    expect(options.find((o) => o.text().includes('/help'))?.text()).toContain('查看全部命令与说明')
    expect(wrapper.find('[data-testid="cmd-menu"]').exists()).toBe(true)
  })

  it('键盘 ↓ 切换高亮，Enter 选中高亮项', async () => {
    const wrapper = mountInput()
    await typeCmd(wrapper, '/')
    await wrapper.find('textarea').trigger('keydown.down')
    const options = wrapper.findAll('[data-testid="cmd-option"]')
    expect(options[1].classes()).toContain('active')

    await wrapper.find('textarea').trigger('keydown.enter')
    await flushPromises()
    // 第二项是 /doc → doc 模板
    expect((wrapper.find('textarea').element as HTMLTextAreaElement).value)
      .toBe('基于知识库回答：')
  })

  it('负例：/xyz 无匹配 → Enter 发送原文（不拦截）', async () => {
    const wrapper = mountInput()
    await typeCmd(wrapper, '/xyz 随便')
    expect(wrapper.find('[data-testid="cmd-empty"]').exists()).toBe(true)
    await wrapper.find('textarea').trigger('keydown.enter')
    await flushPromises()
    const emitted = wrapper.emitted('send')
    expect(emitted).toHaveLength(1)
    expect(emitted![0][0]).toBe('/xyz 随便')
  })

  it('负例：不以 / 开头的文本不触发菜单；Escape 关菜单', async () => {
    const wrapper = mountInput()
    await typeCmd(wrapper, '普通消息')
    expect(wrapper.find('[data-testid="cmd-menu"]').exists()).toBe(false)

    await typeCmd(wrapper, '/sea')
    expect(wrapper.find('[data-testid="cmd-menu"]').exists()).toBe(true)
    await wrapper.find('textarea').trigger('keydown.esc')
    expect(wrapper.find('[data-testid="cmd-menu"]').exists()).toBe(false)
  })

  it('负例：steering 模式不开放命令菜单', async () => {
    const wrapper = mountInput({ steering: true })
    await typeCmd(wrapper, '/')
    expect(wrapper.find('[data-testid="cmd-menu"]').exists()).toBe(false)
  })
})
