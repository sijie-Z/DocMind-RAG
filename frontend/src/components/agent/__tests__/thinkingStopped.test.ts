/**
 * F4：取消（run_cancelled / cancelled 终态）的可见标识 —— 思考流停止态 + 四语键。
 *
 * 旧病（浏览器验收实拍）：取消后思考面板残留"💭 思考中…"，用户无法分辨
 * "停住了"。stopped 属性让标签切到终态文案；hitl.* 键在 zh/en/ja/fr 四语齐备。
 *
 * 变异点：删 ThinkingStream 的 stopped 分支 → 本文件红；某一语缺键 → 红。
 */
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'

import ThinkingStream from '../ThinkingStream.vue'
import zh from '@/locales/zh'
import en from '@/locales/en'
import ja from '@/locales/ja'
import fr from '@/locales/fr'

function mkI18n(locale: string, messages: Record<string, any>) {
  return createI18n({ legacy: false, locale, fallbackLocale: 'zh', messages: { [locale]: messages } })
}

function mountStream(props: Record<string, unknown>, locale = 'zh', messages: Record<string, any> = zh) {
  return mount(ThinkingStream, {
    props: { text: '执行步骤: 一', active: false, ...props },
    global: { plugins: [mkI18n(locale, messages)] },
  })
}

describe('ThinkingStream · 取消后的停止态（F4）', () => {
  it('stopped=true：标签切到"已停止"，不再残留"思考中"', () => {
    const w = mountStream({ stopped: true })
    expect(w.text()).toContain('已停止')
    expect(w.text()).not.toContain('思考中')
    expect(w.text()).not.toContain('评估中')
  })

  it('默认（未取消）标签不变（不是恒显停止态）', () => {
    const w = mountStream({})
    expect(w.text()).toContain('思考中')
    expect(w.text()).not.toContain('已停止')
  })

  it('运行中（active）优先于类型标签、且非 stopped 不显示停止文案', () => {
    const w = mountStream({ active: true, thinkingType: 'evaluation' })
    expect(w.text()).toContain('评估中')
    expect(w.text()).not.toContain('已停止')
  })

  it('en locale：Stopped', () => {
    const w = mountStream({ stopped: true }, 'en', en)
    expect(w.text()).toContain('Stopped')
  })

  it('四语键齐备：hitl.cancelled / hitl.stopped', () => {
    const locales: Array<[string, Record<string, any>]> = [
      ['zh', zh], ['en', en], ['ja', ja], ['fr', fr],
    ]
    for (const [code, msg] of locales) {
      const hitl = (msg as any).hitl
      expect(hitl, `${code} 缺 hitl 段`).toBeTruthy()
      expect(hitl.cancelled, `${code} 缺 hitl.cancelled`).toBeTruthy()
      expect(hitl.stopped, `${code} 缺 hitl.stopped`).toBeTruthy()
    }
  })
})
