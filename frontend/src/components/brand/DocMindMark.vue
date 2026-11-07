<template>
  <!-- DocMind 品牌标识：证据菱章 + M 负形。
       单一 currentColor，M 为负形（透出承载背景）；16–48px 均可辨识。 -->
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
      fill="currentColor"
      fill-rule="evenodd"
      clip-rule="evenodd"
      d="M12 1.5 22.5 12 12 22.5 1.5 12 12 1.5Zm-5.2 14.3V8.2h2.5l3.2 2.55 3.2-2.55h2.5v7.6h-2.5v-4.3L12 14.1l-3.2-2.6v4.3H6.8Z"
    />
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
