/**
 * AgentInput · 初始提示词：从聊天页接力来的文本应填进 textarea（不自动发送），
 * prop 变化时同步更新。
 */
import { describe, it, expect, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import naive from 'naive-ui'
import zh from '@/locales/zh'

vi.mock('@/api/commands', () => ({
  commandsApi: {
    list: vi.fn().mockResolvedValue({ data: { success: true, data: { commands: [] } } }),
  },
}))

import AgentInput from '../AgentInput.vue'

const i18n = createI18n({
  legacy: false,
  locale: 'zh',
  fallbackLocale: 'zh',
  messages: { zh },
})

async function mountInput(initialText?: string) {
  const wrapper = mount(AgentInput, {
    props: { initialText },
    global: { plugins: [naive, i18n] },
  })
  await flushPromises()
  return wrapper
}

describe('AgentInput · 初始提示词', () => {
  it('挂载时把 initialText 填入输入框（但不发送）', async () => {
    const wrapper = await mountInput('请核验这两份合同')
    const textarea = wrapper.find('textarea').element as HTMLTextAreaElement
    expect(textarea.value).toBe('请核验这两份合同')
    expect(wrapper.emitted('send')).toBeUndefined()
  })

  it('prop 变化时同步更新（再次从聊天页接力）', async () => {
    const wrapper = await mountInput('第一条')
    await wrapper.setProps({ initialText: '第二条' })
    await flushPromises()
    expect((wrapper.find('textarea').element as HTMLTextAreaElement).value).toBe('第二条')
  })

  it('initialText 变为空时不覆盖用户已输入内容', async () => {
    // 先以非空值挂载（真正触发 watcher），再改空：直接删掉 `if (!text) return`
    // 守卫会让空串写入输入框，本用例即红。
    const wrapper = await mountInput('初始接力文本')
    const textarea = wrapper.find('textarea').element as HTMLTextAreaElement
    textarea.value = '用户自己的草稿'
    textarea.dispatchEvent(new Event('input'))
    await wrapper.setProps({ initialText: '' })
    await flushPromises()
    expect((wrapper.find('textarea').element as HTMLTextAreaElement).value).toBe('用户自己的草稿')
  })
})
