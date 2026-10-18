/**
 * E6 · SessionSelector 删除按钮可访问名（判别用例）。
 *
 * 事故面：emoji 删除按钮只有 title 提示、无可访问名（无 aria-label），
 * 读屏与键盘用户无法识别按钮用途。判别：按钮必须带 aria-label，且随界面
 * 语言取 i18n 词条（zh/en 各验一次）；修复前 aria-label 缺失即红。
 */
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'

import SessionSelector from '../SessionSelector.vue'
import zh from '@/locales/zh'
import en from '@/locales/en'
import type { AgentSession } from '@/types/agent'

const sessions: AgentSession[] = [
  { id: 's1', title: '示例会话', message_count: 2, created_at: '', updated_at: '' },
]

function mountSelector(locale: 'zh' | 'en') {
  const i18n = createI18n({ legacy: false, locale, messages: { zh, en } })
  return mount(SessionSelector, {
    props: { sessions, modelValue: 's1' },
    global: { plugins: [i18n] },
  })
}

describe('SessionSelector · 删除按钮可访问名（先红）', () => {
  it('emoji 删除按钮带 aria-label（zh 词条）', () => {
    const wrapper = mountSelector('zh')
    const btn = wrapper.find('.delete-session-btn')
    expect(btn.exists()).toBe(true)
    expect(btn.text()).toContain('🗑')
    expect(btn.attributes('aria-label')).toBe('删除当前会话')
  })

  it('en 语言下 aria-label 走词条（不落中文）', () => {
    const wrapper = mountSelector('en')
    const btn = wrapper.find('.delete-session-btn')
    expect(btn.attributes('aria-label')).toBe('Delete current session')
  })
})
