import { createI18n } from 'vue-i18n'
import zh from './zh'
import en from './en'
import ja from './ja'
import fr from './fr'

const i18n = createI18n({
  legacy: false, // 使用 Composition API
  locale: localStorage.getItem('language') || 'zh', // 默认中文
  fallbackLocale: 'zh',
  messages: {
    zh,
    en,
    ja,
    fr
  }
})

export default i18n
