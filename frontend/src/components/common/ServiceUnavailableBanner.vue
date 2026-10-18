<template>
  <div
    v-if="userStore.authServiceUnavailable"
    class="service-banner"
    role="alert"
    data-testid="service-unavailable-banner"
  >
    <span class="service-banner__text">{{ t('error.offlineBanner.message') }}</span>
    <button
      type="button"
      class="service-banner__retry"
      :disabled="retrying"
      data-testid="service-unavailable-retry"
      @click="retry"
    >
      {{ t('error.offlineBanner.retry') }}
    </button>
  </div>
</template>

<script setup lang="ts">
/**
 * 服务不可用横幅：/users/me 遇 5xx/网络错误（会话被保留）时全局展示。
 * 重试成功 → store 复位 authServiceUnavailable，横幅自动消失。
 */
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useUserStore } from '@/stores/user'

const { t } = useI18n()
const userStore = useUserStore()
const retrying = ref(false)

const retry = async () => {
  if (retrying.value) return
  retrying.value = true
  try {
    await userStore.getUserInfo()
  } catch {
    // 仍不可用：横幅保持，等待用户再次重试
  } finally {
    retrying.value = false
  }
}
</script>

<style scoped>
.service-banner {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 9998;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-wrap: wrap;
  gap: 12px;
  padding: 8px 16px;
  font-size: 13px;
  color: #92400e;
  background: #fef3c7;
  border-bottom: 1px solid #fde68a;
}

.service-banner__retry {
  border: 1px solid #d97706;
  background: transparent;
  color: #92400e;
  border-radius: 6px;
  padding: 2px 10px;
  font-size: 12px;
  cursor: pointer;
  transition: opacity 0.2s ease;
}

.service-banner__retry:disabled {
  opacity: 0.6;
  cursor: default;
}

html.dark .service-banner {
  color: #fcd34d;
  background: #451a03;
  border-bottom-color: #78350f;
}

html.dark .service-banner__retry {
  color: #fcd34d;
  border-color: #b45309;
}
</style>
