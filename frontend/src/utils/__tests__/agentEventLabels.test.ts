/**
 * retry_hint 文案必须跟随界面语言（不再固定中文）。
 *
 * 修前：RETRY_ACTION_LABELS 是模块级中文常量，en/ja/fr 界面仍显示
 * 「连接中断，正在重建连接后重试」。修复后映射到 agent.retryHints.* 词条。
 *
 * 变异点：退回中文常量 / 删除映射 → 对应语言断言红。
 */
import { describe, it, expect, beforeAll, afterEach } from 'vitest'
import i18n from '@/locales'
import en from '@/locales/en'
import { formatRetryHint } from '../agentEventLabels'

beforeAll(() => {
  i18n.global.setLocaleMessage('en', en as never)
})

afterEach(() => {
  i18n.global.locale.value = 'zh'
})

describe('formatRetryHint · 语言切换', () => {
  it('zh：已知动作映射为中文说明，未知动作回退原文，空值给默认文案', () => {
    expect(formatRetryHint('retry_action=reset_transport')).toBe('连接中断，正在重建连接后重试')
    expect(formatRetryHint('retry_action=refresh_auth')).toBe('凭据失效，正在刷新凭据后重试')
    expect(formatRetryHint('retry_action=escalate_custom')).toBe('retry_action=escalate_custom')
    expect(formatRetryHint('')).toBe('正在重试')
  })

  it('en：已知动作与默认文案均为英文', () => {
    (i18n.global.locale as unknown as { value: string }).value = 'en'
    expect(formatRetryHint('retry_action=reset_transport')).toBe(
      'Connection lost, reconnecting before retrying',
    )
    expect(formatRetryHint('retry_action=refresh_auth')).toBe(
      'Credentials expired, refreshing before retrying',
    )
    expect(formatRetryHint('')).toBe('Retrying')
  })
})
