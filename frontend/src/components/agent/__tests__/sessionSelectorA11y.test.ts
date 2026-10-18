/**
 * E6 · SessionSelector 删除按钮可访问名（判别用例）。
 *
 * 事故面：emoji 删除按钮只有 title 提示、无可访问名（无 aria-label），
 * 读屏与键盘用户无法识别按钮用途。判别：按钮必须带 aria-label，且随界面
 * 语言取 i18n 词条（zh/en 各验一次）；修复前 aria-label 缺失即红。
 *
 * E14/P2 追加：<select> 本身无可访问名；「+ 新建会话」与兜底「Untitled」
 * 硬编码未词条化（en/ja/fr 界面仍显示中文/英文）。判别：select 具名、
 * 两处文案四语均走词条。
 */
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'

import SessionSelector from '../SessionSelector.vue'
import zh from '@/locales/zh'
import en from '@/locales/en'
import ja from '@/locales/ja'
import fr from '@/locales/fr'
import type { AgentSession } from '@/types/agent'

const sessions: AgentSession[] = [
  { id: 's1', title: '示例会话', message_count: 2, created_at: '', updated_at: '' },
]

function mountSelector(
  locale: 'zh' | 'en' | 'ja' | 'fr',
  sessionList: AgentSession[] = sessions,
) {
  const i18n = createI18n({ legacy: false, locale, messages: { zh, en, ja, fr } })
  return mount(SessionSelector, {
    props: { sessions: sessionList, modelValue: 's1' },
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

describe('SessionSelector · select 可访问名与新会话/未命名词条化（E14/P2）', () => {
  it('zh：select 有 aria-label，首项「+ 新建会话」走词条', () => {
    const wrapper = mountSelector('zh')
    const select = wrapper.find('.session-select')
    expect(select.attributes('aria-label')).toBe('选择 Agent 会话')
    const firstOption = select.findAll('option')[0]
    expect(firstOption.text()).toBe('+ 新建会话')
  })

  it('en：aria-label 与首项均走英文词条，不落中文', () => {
    const wrapper = mountSelector('en')
    const select = wrapper.find('.session-select')
    expect(select.attributes('aria-label')).toBe('Select agent session')
    expect(select.findAll('option')[0].text()).toBe('+ New session')
  })

  it('ja/fr：首项走各自词条（不落中文/英文硬编码）', () => {
    expect(mountSelector('ja').find('.session-select').findAll('option')[0].text()).toBe('+ 新規セッション')
    expect(mountSelector('fr').find('.session-select').findAll('option')[0].text()).toBe('+ Nouvelle session')
  })

  it('无标题会话：兜底名四语均走词条（zh「未命名」/ en「Untitled」/ ja「無題」/ fr「Sans titre」）', () => {
    const untitled: AgentSession[] = [
      { id: 's2', title: '', message_count: 0, created_at: '', updated_at: '' },
    ]
    const secondOption = (locale: 'zh' | 'en' | 'ja' | 'fr') =>
      mountSelector(locale, untitled).find('.session-select').findAll('option')[1].text()
    expect(secondOption('zh')).toContain('未命名')
    expect(secondOption('en')).toContain('Untitled')
    expect(secondOption('ja')).toContain('無題')
    expect(secondOption('fr')).toContain('Sans titre')
  })
})
