<template>
  <!-- DocMind 品牌标识：文档（知识载体）+ 校验透镜（检索与核验）。
       单一图形在侧栏、登录页与 favicon/PWA 图标复用，避免字母块拼贴。 -->
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
      d="M6.5 2.5h5l3 3V13a2 2 0 0 1-2 2H6.5a2 2 0 0 1-2-2V4.5a2 2 0 0 1 2-2Z"
      stroke="currentColor"
      stroke-width="1.8"
      stroke-linejoin="round"
    />
    <path d="M11.5 2.5v3h3" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" />
    <path d="M7 8.2h4.4M7 11.2h2.8" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" />
    <circle cx="17.1" cy="17.1" r="3.35" stroke="currentColor" stroke-width="1.8" />
    <path
      d="m15.65 17.15 1.05 1.05 2-2.25"
      stroke="currentColor"
      stroke-width="1.8"
      stroke-linecap="round"
      stroke-linejoin="round"
    />
    <path d="m19.5 19.5 1.8 1.8" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" />
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
