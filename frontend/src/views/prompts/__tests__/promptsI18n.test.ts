/**
 * 提示词管理页 i18n：整页此前硬编码中文（45 行）。
 *
 * 背景：`views/prompts/index.vue` 的副标题、按钮、表单标签/占位符、
 * 分类筛选、版本历史、回滚确认框全部写死中文；`prompts.*` 命名空间
 * 里其实已有大量词条，只是页面没用。切到 en/ja/fr 后整页中文。
 *
 * 覆盖边界（如实）：本文件只覆盖「页头/搜索/新建入口/分类标签」的
 * 模板与 computed；抽屉内表单 label、回滚 dialog、版本历史、校验消息
 * **不被本文件保护**——它们由 hardcodedCjk 棘轮（中文）与人工评审兜底。
 * 变异点（已实测）：页头「新增模板」退回硬编码 → en 与切语言两例红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import { createPinia } from 'pinia'
import naive, { NMessageProvider, NDialogProvider } from 'naive-ui'
import { createRouter, createMemoryHistory } from 'vue-router'

const { messageSpies } = vi.hoisted(() => ({
  messageSpies: { error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn() },
}))
vi.mock('@/utils/message', () => ({ useDedupedMessage: () => messageSpies }))

vi.mock('@/api/prompt', () => ({
  getPrompts: vi.fn().mockResolvedValue({ data: [] }),
  createPrompt: vi.fn(),
  updatePrompt: vi.fn(),
  deletePrompt: vi.fn(),
  seedDefaultPrompts: vi.fn(),
  getPromptVersions: vi.fn().mockResolvedValue({ data: [] }),
  restorePromptVersion: vi.fn(),
}))

import PromptsView from '../index.vue'
import zh from '@/locales/zh'
import en from '@/locales/en'

const Host = defineComponent({
  render: () => h(NDialogProvider, null, { default: () => h(NMessageProvider, null, { default: () => h(PromptsView) }) }),
})

async function mountAt(locale: 'zh' | 'en') {
  const i18n = createI18n({ legacy: false, locale, missingWarn: false, fallbackWarn: false, messages: { zh, en } })
  const router = createRouter({ history: createMemoryHistory(), routes: [] })
  const w = mount(Host, {
    attachTo: document.body,
    global: { plugins: [naive, i18n, createPinia(), router] },
  })
  await flushPromises()
  await nextTick()
  return { w, i18n }
}

beforeEach(() => {
  vi.clearAllMocks()
  document.body.innerHTML = ''
})

describe('提示词页 · i18n', () => {
  it('zh：副标题/新建按钮/搜索占位为中文', async () => {
    const { w } = await mountAt('zh')
    const text = w.text()
    expect(text).toContain('提示词模板将直接影响AI的回答方式和风格')
    expect(text).toContain('新增模板')
    const input = w.find('input')
    expect(input.attributes('placeholder')).toBe('搜索模板...')
    w.unmount()
  })

  it('en：整页文案为英文且不留中文', async () => {
    const { w } = await mountAt('en')
    const text = w.text()
    expect(text).toContain('Prompt templates directly shape how the AI answers')
    expect(text).toContain('New Template')
    expect(w.find('input').attributes('placeholder')).toBe('Search templates...')
    // 硬约束：正文不得残留 CJK（页面标题走 menu.prompts，en 亦为英文）
    expect(text).not.toMatch(/[㐀-䶿一-鿿]/)
    w.unmount()
  })

  it('挂载后切 en → 文案实时更新', async () => {
    const { w, i18n } = await mountAt('zh')
    expect(w.text()).toContain('新增模板')
    ;(i18n.global.locale as unknown as { value: string }).value = 'en'
    await nextTick()
    await flushPromises()
    expect(w.text()).toContain('New Template')
    expect(w.text()).not.toMatch(/[㐀-䶿一-鿿]/)
    w.unmount()
  })
})
