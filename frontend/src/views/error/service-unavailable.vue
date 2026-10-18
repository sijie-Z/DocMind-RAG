<template>
  <div
    class="min-h-screen flex items-center justify-center bg-slate-50 dark:bg-slate-950 px-4 transition-colors duration-300"
    data-testid="service-unavailable-page"
  >
    <div class="text-center max-w-md">
      <div class="error-code text-[96px] md:text-[120px] font-extrabold leading-none bg-gradient-to-r from-amber-500 to-orange-600 dark:from-amber-400 dark:to-orange-400 bg-clip-text text-transparent select-none">
        503
      </div>
      <h1 class="mt-6 text-2xl font-bold text-slate-900 dark:text-white">
        {{ t('error.serviceUnavailable.title') }}
      </h1>
      <p class="mt-3 text-sm text-slate-500 dark:text-slate-400">
        {{ t('error.serviceUnavailable.subtitle') }}
      </p>
      <div class="mt-8 flex items-center justify-center gap-3 flex-wrap">
        <button
          type="button"
          class="retry-button"
          :disabled="retrying"
          data-testid="service-unavailable-page-retry"
          @click="retry"
        >
          {{ t('error.serviceUnavailable.retry') }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 服务暂时不可用页：/users/me 5xx/网络错误且无本地缓存时的兜底落点。
 * 登录态保留，重试即重新走守卫校验；不因服务端故障把用户踢回登录页。
 */
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const retrying = ref(false)

const retry = async () => {
  if (retrying.value) return
  retrying.value = true
  const redirect = route.query.redirect
  const target = typeof redirect === 'string' && redirect.startsWith('/') && !redirect.startsWith('//')
    ? redirect
    : '/dashboard'
  try {
    await router.replace(target)
  } catch {
    // 导航被守卫重定向（如服务仍不可用 → 回到本页）时无需额外处理
  } finally {
    retrying.value = false
  }
}
</script>

<style scoped>
.retry-button {
  padding: 10px 28px;
  border-radius: 12px;
  border: none;
  font-size: 15px;
  font-weight: 500;
  color: #ffffff;
  background: linear-gradient(90deg, #f59e0b, #ea580c);
  cursor: pointer;
  transition: opacity 0.2s ease, transform 0.2s ease;
}

.retry-button:hover:not(:disabled) {
  transform: translateY(-1px);
  opacity: 0.92;
}

.retry-button:disabled {
  opacity: 0.6;
  cursor: default;
}
</style>
