/**
 * HITL 审批理由 i18n：reason_code 可映射时按 agent.approval.reasons.* 渲染；
 * 未知/缺失 code 回退 reason 原文（hook 人类文案 / 旧后端兼容）。
 *
 * 变异点：
 *  - 删 REASON_KEYS 映射 / 删 reasonText computed → en 断言红；
 *  - store 不透传 reason_code（pendingApproval 无该字段）→ 用例 2 红；
 *  - 后端事件不带 reason_code → 用例 2 的映射路径断言红（回退到中文原文）。
 */
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'

import ApprovalCard from '@/components/agent/ApprovalCard.vue'
import zh from '@/locales/zh'
import en from '@/locales/en'

const i18n = createI18n({ legacy: false, locale: 'zh', fallbackLocale: 'en', messages: { zh, en } })

function mountCard(props: Record<string, unknown>, locale: 'zh' | 'en' = 'zh') {
  const i = createI18n({ legacy: false, locale, fallbackLocale: 'en', messages: { zh, en } })
  return mount(ApprovalCard, { props, global: { plugins: [i] } })
}

const BASE = {
  toolName: 'execute_python',
  toolArgs: { code: 'print(1)' },
  riskLevel: 'high',
}

describe('ApprovalCard · 理由按 reason_code 本地化', () => {
  it('zh：high_risk_tool 渲染中文模板并插值 tool/risk', () => {
    const w = mountCard({
      ...BASE,
      reason: '高风险工具 execute_python（风险等级 high）执行前需人工审批',
      reasonCode: 'high_risk_tool',
      reasonParams: { tool: 'execute_python', risk: 'high' },
    })
    expect(w.find('.approval-reason').text()).toBe(
      '高风险工具 execute_python（风险等级 high）执行前需人工审批',
    )
    w.unmount()
  })

  it('en：同 code 渲染英文模板（回退路径不触发）', () => {
    const w = mountCard(
      {
        ...BASE,
        reason: '中文兜底',
        reasonCode: 'high_risk_tool',
        reasonParams: { tool: 'execute_python', risk: 'high' },
      },
      'en',
    )
    expect(w.find('.approval-reason').text()).toBe(
      'High-risk tool execute_python (risk high) requires human approval before execution',
    )
    expect(w.find('.approval-reason').text()).not.toContain('中文兜底')
    w.unmount()
  })

  it('terminal_forced / hook_ask / approval_mode_always 映射齐全（en）', () => {
    for (const [code, expected] of [
      ['terminal_forced', 'Terminal tools require approval (TERMINAL_REQUIRE_APPROVAL=true)'],
      ['hook_ask', 'A security hook requires human approval for this tool call'],
      ['approval_mode_always', 'Approval mode always: calling tool web_search (risk low) requires manual confirmation'],
    ] as Array<[string, string]>) {
      const w = mountCard(
        { ...BASE, reason: '中文兜底', reasonCode: code, reasonParams: { tool: 'web_search', risk: 'low' } },
        'en',
      )
      expect(w.find('.approval-reason').text()).toBe(expected)
      w.unmount()
    }
  })

  it('未知 code / 缺 code → 回退 reason 原文（hook 人类文案原样展示）', () => {
    for (const props of [
      { ...BASE, reason: '审计策略要求人工确认', reasonCode: 'mystery_code', reasonParams: {} },
      { ...BASE, reason: '审计策略要求人工确认' },
    ]) {
      const w = mountCard(props, 'en')
      expect(w.find('.approval-reason').text()).toBe('审计策略要求人工确认')
      w.unmount()
    }
  })

  it('无 reason 且无 code → 不渲染理由行', () => {
    const w = mountCard({ ...BASE }, 'en')
    expect(w.find('.approval-reason').exists()).toBe(false)
    w.unmount()
  })
})
