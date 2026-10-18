/**
 * 首屏瘦身（E5）· 语言包按需加载判别用例。
 *
 * 现状（改前）：locales/index.ts 静态 import 四语包 → zh/en/ja/fr 全部进入
 * 首屏入口 chunk，用户只用一个语言却为四个付费。
 * 改后：仅默认语言 zh 同步内置；en/ja/fr 拆为动态 chunk，由 ensureLocale /
 * setLanguage 在需要时拉取（ja/fr 同时加载 en 兜底包——两语各缺 53 键）。
 *
 * 变异点：
 *  - 退回全量静态装载 → 「非当前语言包不随首屏同步可读」红；
 *  - ensureLocale 忘记加载 en 兜底 → 「ja 缺口回退英文而非裸键」红；
 *  - setLanguage 不先载包就切 locale → 「四语切换词条正确」红（裸键）。
 */
import { describe, it, expect } from 'vitest'
import i18n, { ensureLocale, setLanguage } from '@/locales'

describe('E5 · locales 按需加载', () => {
  it('判别：非当前语言包不随首屏同步可读（仅默认 zh 内置）', () => {
    expect(i18n.global.locale.value).toBe('zh')
    expect(i18n.global.availableLocales).toEqual(['zh'])
    expect(i18n.global.getLocaleMessage('en')).toEqual({})
    expect(i18n.global.getLocaleMessage('ja')).toEqual({})
    expect(i18n.global.getLocaleMessage('fr')).toEqual({})
  })

  it('ensureLocale(ja)：ja 词条与 en 兜底包一并就绪，缺口回退英文而非裸键', async () => {
    await ensureLocale('ja')
    expect(i18n.global.getLocaleMessage('ja')).not.toEqual({})
    // ja 缺 53 键（completeness 基线）——兜底包必须同批就绪
    expect(i18n.global.getLocaleMessage('en')).not.toEqual({})

    await setLanguage('ja')
    // audit.actionFilter 为 ja 缺口之一：取 en 译文 'Action Type'，而不是键名本身
    expect(i18n.global.t('audit.actionFilter')).toBe('Action Type')
  })

  it('回归：setLanguage 四语切换，词条按语言正确渲染且持久化', async () => {
    await setLanguage('en')
    expect(i18n.global.locale.value).toBe('en')
    expect(i18n.global.t('common.save')).toBe('Save')
    expect(localStorage.getItem('language')).toBe('en')

    await setLanguage('ja')
    expect(i18n.global.locale.value).toBe('ja')
    expect(i18n.global.t('common.save')).toBe('保存')
    expect(i18n.global.t('common.cancel')).toBe('キャンセル')

    await setLanguage('fr')
    expect(i18n.global.locale.value).toBe('fr')
    expect(i18n.global.t('common.save')).toBe('Enregistrer')
    expect(localStorage.getItem('language')).toBe('fr')

    await setLanguage('zh')
    expect(i18n.global.locale.value).toBe('zh')
    expect(i18n.global.t('common.save')).toBe('保存')
    expect(localStorage.getItem('language')).toBe('zh')
  })
})
