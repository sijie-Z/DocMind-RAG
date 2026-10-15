import { createI18n } from 'vue-i18n'
import zh from './zh'
import en from './en'
import ja from './ja'
import fr from './fr'
import { FALLBACK_LANGUAGE, isLanguageCode } from '@/utils/languages'

const stored = localStorage.getItem('language')

const i18n = createI18n({
  legacy: false, // 使用 Composition API
  // 默认中文；localStorage 中的非法值（历史脏数据/手改）不注入 locale
  locale: isLanguageCode(stored) ? stored : 'zh',
  // 诚实降级（ISS-4）：ja/fr 存在词条缺口（主要 chat 域），缺键统一回退 en
  // ——en 100% 完备（locales/__tests__/completeness.test.ts 有硬闸+棘轮保护）。
  // 此前回退 zh：ja/fr 界面缺键处冒出中文，与假名/拉丁界面混杂，观感最差。
  fallbackLocale: FALLBACK_LANGUAGE,
  messages: {
    zh,
    en,
    ja,
    fr
  }
})

export default i18n
