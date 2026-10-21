<template>
  <div v-if="visible" class="reflection-banner" :class="'reflection-' + result">
    <span class="reflection-icon">{{ icon }}</span>
    <span class="reflection-text">{{ text }}</span>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { ReflectionResult } from '@/types/agent'

const props = defineProps<{
  result: ReflectionResult
  text: string
}>()

const visible = computed(() => !!props.result && !!props.text)

const icon = computed(() => {
  switch (props.result) {
    case 'pass': return '✅'
    case 'retry': return '🔄'
    case 'replan': return '📝'
    case 'escalate': return '⚠️'
    // 反思降级两态（events.py 六值域；此前无分支 → '❓' 且无 CSS 类，
    // 横幅退化为无底色裸文本）
    case 'unavailable': return '⏸️'
    case 'unparsed': return '❔'
    default: return '❓'
  }
})
</script>

<style scoped>
.reflection-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: 10px;
  font-size: 12px;
  margin: 8px 0;
}

.reflection-pass {
  background: rgba(16, 185, 129, 0.08);
  color: #065f46;
  border: 1px solid #86efac;
}

.reflection-retry {
  background: rgba(245, 158, 11, 0.08);
  color: #92400e;
  border: 1px solid #fcd34d;
}

.reflection-replan {
  background: rgba(239, 68, 68, 0.08);
  color: #991b1b;
  border: 1px solid #fca5a5;
}

.reflection-escalate {
  background: rgba(107, 114, 128, 0.08);
  color: #374151;
  border: 1px solid #d1d5db;
}

/* 反思降级两态（unavailable / unparsed）：中性灰，与四主态同构的底色/边框，
   避免"无 CSS 类名 → 裸文本"退化 */
.reflection-unavailable,
.reflection-unparsed {
  background: rgba(107, 114, 128, 0.08);
  color: #374151;
  border: 1px solid #d1d5db;
}

.dark .reflection-pass {
  background: rgba(16, 185, 129, 0.12);
  color: #6ee7b7;
}

.dark .reflection-retry {
  background: rgba(245, 158, 11, 0.12);
  color: #fcd34d;
}

.dark .reflection-replan {
  background: rgba(239, 68, 68, 0.12);
  color: #fca5a5;
}

.dark .reflection-escalate {
  background: rgba(156, 163, 175, 0.12);
  color: #d1d5db;
}

.dark .reflection-unavailable,
.dark .reflection-unparsed {
  background: rgba(156, 163, 175, 0.12);
  color: #d1d5db;
}

.reflection-icon {
  font-size: 16px;
}

.reflection-text {
  line-height: 1.4;
}
</style>
