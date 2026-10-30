/**
 * 原因码注册表契约：side 前缀、'+' 组合码、未知码透传、歧义码复用既有文案。
 */
import { describe, it, expect } from 'vitest'
import { createI18n } from 'vue-i18n'
import zh from '@/locales/zh'
import en from '@/locales/en'
import { reasonLabel, reasonsLabel } from '../reviewReasons'

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })
const t = i18n.global.t as unknown as (key: string) => string
const i18nEn = createI18n({ legacy: false, locale: 'en', messages: { en } })
const tEn = i18nEn.global.t as unknown as (key: string) => string

describe('核验原因码本地化', () => {
  // 表驱动：后端全部原因码逐一断言（漏映射/错映射必须红）
  const CASES: Array<[string, string]> = [
    ['parse_failed', '文档解析失败'],
    ['not_indexed', '尚未完成索引'],
    ['no_chunks', '没有可用的文档分块'],
    ['no_original_text', '缺少 parser 原文'],
    ['legacy_unverified', '旧版分块未迁移'],
    ['missing_version_meta', '未登记或未补全核验身份'],
    ['wrong_org', '核验身份记录的组织不一致'],
    ['wrong_role', '文档角色'],
    ['family_mismatch', '版本族不一致'],
    ['version_order', '版本序'],
    ['undecided', '存在未决核验项'],
    ['not_analyzed', '尚未执行分析'],
    ['unit_unclear', '单位无法判定'],
    ['multiple_durations', '多个期限候选']
  ]

  it.each(CASES)('%s → 业务文案且不出现原始码', (code, expected) => {
    const text = reasonLabel(t, code)
    expect(text).toContain(expected)
    expect(text).not.toContain(code)
  })

  it('side 前缀渲染为文档侧标签 + 原因', () => {
    expect(reasonLabel(t, 'revised:no_original_text')).toBe(
      '合同修订版：缺少 parser 原文（需重建文档）'
    )
    expect(reasonLabel(t, 'base:missing_version_meta')).toContain('合同基准版：')
    expect(reasonLabel(t, 'policy:wrong_role')).toContain('制度/模板：')
    // 分隔符按 locale：en 用半角冒号+空格
    expect(reasonLabel(tEn, 'base:missing_version_meta')).toContain('Contract base: ')
  })

  it('跨文档约束与导出阻断原因本地化', () => {
    expect(reasonLabel(t, 'family_mismatch')).toContain('版本族不一致')
    expect(reasonLabel(t, 'version_order')).toContain('版本序')
    expect(reasonLabel(t, 'undecided')).toContain('未决')
    expect(reasonLabel(t, 'not_analyzed')).toContain('尚未执行分析')
  })

  it('组合码逐段映射并用 · 连接；未知码原样透传', () => {
    expect(reasonLabel(t, 'unit_unclear+multiple_durations')).toBe(
      '单位无法判定（自然日/工作日） · 同一核验项出现多个期限候选'
    )
    expect(reasonLabel(t, 'some_future_code')).toBe('some_future_code')
    expect(reasonLabel(t, 'base:some_future_code')).toBe('合同基准版：some_future_code')
    expect(reasonsLabel(t, ['family_mismatch', 'version_order'])).toContain(' · ')
    expect(reasonsLabel(t, [])).toBe('')
    expect(reasonsLabel(t, undefined)).toBe('')
  })
})
