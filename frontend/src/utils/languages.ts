/**
 * 语言清单单一事实源（ISS-4）。
 *
 * - value 与 locales/index.ts 的 locale 取值、localStorage['language']
 *   持久化值、后端 user settings.language 一致；
 * - label 用语言自称（不随界面语言翻译）；
 * - beta 字段保留给「词条不完备语言」：缺口在 fallbackLocale（en）下回退英文，
 *   切换器据此展示 Beta 徽标 + 说明（词条 profile.languageBeta）。F 波起四语均
 *   false —— ja/fr 已随 T13 补齐至零缺口（completeness 棘轮 0/0/0）；渲染机制
 *   保留，供未来新增语言使用。
 *
 * 新增语言时三处补齐：本清单 + locales/ 四语词条 + App.vue 的 naive 映射
 * （后者是 Record<LanguageCode, …>，vue-tsc 会强制补全，漏一处即编译红）。
 */
export type LanguageCode = 'zh' | 'en' | 'ja' | 'fr'

export interface LanguageOption {
  value: LanguageCode
  /** 语言自称（始终原样展示，不做翻译） */
  label: string
  /** 词条完备度：true = 存在缺口，缺键由 fallback（en）兜底；当前四语均 false */
  beta: boolean
}

export const LANGUAGES: readonly LanguageOption[] = [
  { value: 'zh', label: '简体中文', beta: false },
  { value: 'en', label: 'English', beta: false },
  { value: 'ja', label: '日本語', beta: false },
  { value: 'fr', label: 'Français', beta: false },
]

export const LANGUAGE_VALUES: readonly LanguageCode[] = LANGUAGES.map((l) => l.value)

/** i18n 兜底语言：en 必须 100% 完备（棘轮硬闸见 locales/__tests__/completeness.test.ts） */
export const FALLBACK_LANGUAGE: LanguageCode = 'en'

/** 守卫：localStorage / 后端 settings 可能携带任意字符串，非法值不得注入 locale */
export function isLanguageCode(value: unknown): value is LanguageCode {
  return typeof value === 'string' && (LANGUAGE_VALUES as readonly string[]).includes(value)
}
