<template>
  <!-- DocMind 品牌标识：折页 M + 证据菱形。
       M 的两道折线像折起的纸页，谷底压接的实心菱形代表被钉住、可核验的证据；
       单色 currentColor，可复用于侧栏、登录页与 favicon/PWA。 -->
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    :role="label ? 'img' : undefined"
    :aria-label="label || undefined"
    :aria-hidden="label ? undefined : 'true'"
    focusable="false"
  >
    <path
      d="M3.6 19.4V6.9L12 13.2 20.4 6.9v12.5"
      stroke="currentColor"
      stroke-width="2.1"
      stroke-linecap="round"
      stroke-linejoin="round"
    />
    <path d="M12 12.8l2.4 2.4-2.4 2.4-2.4-2.4z" fill="currentColor" />
  </svg>
</template>

<script setup lang="ts">
withDefaults(defineProps<{
  size?: number | string
  /** 提供文本时作为 img 暴露给辅助技术；默认装饰性（aria-hidden）。 */
  label?: string
}>(), {
  size: 24,
  label: '',
})
</script>
