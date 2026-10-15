<template>
  <div
    class="min-h-screen flex items-center justify-center bg-slate-50 dark:bg-slate-950 px-4 transition-colors duration-300"
    data-testid="not-found-page"
  >
    <div class="text-center max-w-md">
      <div class="error-code text-[96px] md:text-[120px] font-extrabold leading-none bg-gradient-to-r from-blue-600 to-indigo-600 dark:from-blue-400 dark:to-indigo-400 bg-clip-text text-transparent select-none">
        404
      </div>
      <h1 class="mt-6 text-2xl font-bold text-slate-900 dark:text-white">
        {{ t('error.404.title') }}
      </h1>
      <p class="mt-3 text-sm text-slate-500 dark:text-slate-400">
        {{ t('error.404.subtitle') }}
      </p>
      <p class="mt-2 text-xs text-slate-400 dark:text-slate-600 break-all">
        {{ route.fullPath }}
      </p>
      <div class="mt-8 flex items-center justify-center gap-3 flex-wrap">
        <n-button type="primary" size="large" class="!rounded-xl" @click="goPrimary">
          {{ isLoggedIn ? t('error.backToWorkbench') : t('error.backToHome') }}
        </n-button>
        <n-button size="large" class="!rounded-xl" @click="goBack">
          {{ t('error.back') }}
        </n-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useUserStore } from '@/stores/user'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const userStore = useUserStore()

const isLoggedIn = computed(() => userStore.isLoggedIn)

// 主按钮按登录态分流：已登录回工作台，未登录回营销首页
const goPrimary = () => {
  router.push(isLoggedIn.value ? '/dashboard' : '/firsthome')
}

const goBack = () => {
  if (window.history.length > 1) {
    router.back()
  } else {
    // 直达无历史可回时，退化为主按钮目的地
    router.push(isLoggedIn.value ? '/dashboard' : '/firsthome')
  }
}
</script>
