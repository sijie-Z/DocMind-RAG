<template>
  <n-config-provider
    :theme="theme"
    :theme-overrides="themeOverrides"
    :locale="localeConfig"
    :date-locale="dateLocaleConfig"
  >
    <n-dialog-provider>
      <n-notification-provider>
        <n-message-provider>
          <ErrorBoundary>
            <!-- 全局顶部进度条：位于 router-view 之外，Layout 挂载前即可见 -->
            <div
              v-if="routeProgressActive"
              class="route-progress"
              data-testid="route-progress-bar"
              aria-hidden="true"
            >
              <div class="route-progress-inner" />
            </div>
            <ServiceUnavailableBanner />
            <router-view />
          </ErrorBoundary>
        </n-message-provider>
      </n-notification-provider>
    </n-dialog-provider>
  </n-config-provider>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import {
  darkTheme,
  zhCN, dateZhCN,
  enUS, dateEnUS,
  jaJP, dateJaJP,
  frFR, dateFrFR,
  type NLocale, type NDateLocale,
} from 'naive-ui'
import { useI18n } from 'vue-i18n'
import { useAppStore } from '@/stores/app'
import { lightThemeOverrides, darkThemeOverrides } from '@/theme'
import ErrorBoundary from '@/components/common/ErrorBoundary.vue'
import ServiceUnavailableBanner from '@/components/common/ServiceUnavailableBanner.vue'
import { routeProgressActive } from '@/utils/routeProgress'
import { FALLBACK_LANGUAGE, isLanguageCode, type LanguageCode } from '@/utils/languages'

const appStore = useAppStore()
const { locale } = useI18n()

// Naive UI 组件级文案（分页器、日期选择器、空状态等）跟随界面语言。
// ISS-4：此前只挂 zhCN/enUS，ja/fr 界面下这些组件出英文；现四语全接。
// Record<LanguageCode, …> 是编译期完备性闸：新增语言漏映射 → vue-tsc 红。
const NAIVE_LOCALES: Record<LanguageCode, { locale: NLocale; dateLocale: NDateLocale }> = {
  zh: { locale: zhCN, dateLocale: dateZhCN },
  en: { locale: enUS, dateLocale: dateEnUS },
  ja: { locale: jaJP, dateLocale: dateJaJP },
  fr: { locale: frFR, dateLocale: dateFrFR },
}

const activeLanguage = computed<LanguageCode>(() =>
  isLanguageCode(locale.value) ? locale.value : FALLBACK_LANGUAGE,
)

const theme = computed(() => appStore.isDark ? darkTheme : null)
const themeOverrides = computed(() => appStore.isDark ? darkThemeOverrides : lightThemeOverrides)

const localeConfig = computed(() => NAIVE_LOCALES[activeLanguage.value].locale)
const dateLocaleConfig = computed(() => NAIVE_LOCALES[activeLanguage.value].dateLocale)
</script>

<style>
#app {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

/* 全局顶部进度条（自绘）：不确定态滑块，导航/首屏校验期间持续可见 */
.route-progress {
  position: fixed;
  inset: 0 0 auto 0;
  height: 2px;
  overflow: hidden;
  z-index: 9999;
  pointer-events: none;
}
.route-progress-inner {
  height: 100%;
  width: 40%;
  background: linear-gradient(90deg, #3b82f6, #6366f1);
  animation: route-progress-move 1.2s ease-in-out infinite;
}
@keyframes route-progress-move {
  from { transform: translateX(-110%); }
  to { transform: translateX(360%); }
}
</style>