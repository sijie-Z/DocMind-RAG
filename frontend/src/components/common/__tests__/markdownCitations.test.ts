/**
 * 聊天引用角标：Markdown 渲染后的 [n] 必须可点击/可键盘触发，
 * 未开启 citations 时不得改变原有文本渲染（帮助页/Agent 正文不受影响）。
 */
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import Markdown from '../Markdown.vue'

function mountMarkdown(content: string, props: Record<string, unknown> = {}) {
  return mount(Markdown, { props: { content, ...props } })
}

describe('Markdown · 聊天引用角标', () => {
  it('开启 citations：正文 [n] 渲染为可点击 sup，点击 emit 对应序号', async () => {
    const wrapper = mountMarkdown('正文[1]，另见[2]。', { citations: true })

    const sups = wrapper.findAll('.chat-citation')
    expect(sups).toHaveLength(2)
    expect(sups[0].attributes('data-citation-index')).toBe('1')
    expect(sups[0].attributes('role')).toBe('button')
    expect(sups[0].attributes('tabindex')).toBe('0')

    await sups[0].trigger('click')
    await sups[1].trigger('keydown', { key: 'Enter' })

    const emitted = wrapper.emitted('citation')
    expect(emitted?.[0]).toEqual([1])
    expect(emitted?.[1]).toEqual([2])
  })

  it('空格键同样触发引用（键盘可达）', async () => {
    const wrapper = mountMarkdown('结论[3]', { citations: true })

    await wrapper.find('.chat-citation').trigger('keydown', { key: ' ' })

    expect(wrapper.emitted('citation')?.[0]).toEqual([3])
  })

  it('未开启 citations：不生成角标，[n] 保持纯文本（帮助页/Agent 不受影响）', () => {
    const wrapper = mountMarkdown('正文[1]')

    expect(wrapper.find('.chat-citation').exists()).toBe(false)
    expect(wrapper.text()).toContain('正文[1]')
  })
})
