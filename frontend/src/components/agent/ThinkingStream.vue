<template>
  <div v-if="visible" class="thinking-stream" :class="'thinking-' + thinkingType">
    <div class="thinking-header" @click="collapsed = !collapsed">
      <span class="thinking-label">
        <span class="thinking-dot"></span>
        {{ thinkingLabel }}
      </span>
      <span class="thinking-collapse">{{ collapsed ? '▶' : '▼' }}</span>
    </div>
    <div v-if="!collapsed" class="thinking-content" ref="contentEl">
      {{ text }}
      <span v-if="active" class="thinking-cursor">|</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, nextTick } from 'vue'
import type { ThinkingType } from '@/types/agent'

const props = defineProps<{
  text: string
  thinkingType?: ThinkingType
  active?: boolean
}>()

const collapsed = ref(false)
const contentEl = ref<HTMLElement>()

const visible = computed(() => props.text.length > 0)

const thinkingLabel = computed(() => {
  switch (props.thinkingType) {
    case 'planning': return '🧠 规划中...'
    case 'evaluation': return '🔍 评估中...'
    case 'correction': return '🔄 纠正中...'
    default: return '💭 思考中...'
  }
})

// Auto-scroll to bottom when new text arrives
watch(
  () => props.text,
  async () => {
    await nextTick()
    if (contentEl.value) {
      contentEl.value.scrollTop = contentEl.value.scrollHeight
    }
  },
)
</script>

<style scoped>
.thinking-stream {
  margin: 8px 0;
  border-radius: 10px;
  border: 1px solid var(--border-color, #e5e7eb);
  overflow: hidden;
  font-size: 12px;
}

.dark .thinking-stream {
  border-color: var(--border-color-dark, #374151);
}

.thinking-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 10px;
  background: var(--bg-tertiary, #f9fafb);
  cursor: pointer;
  user-select: none;
}

.dark .thinking-header {
  background: var(--bg-tertiary-dark, #1f2937);
}

.thinking-label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 500;
  color: var(--text-secondary, #6b7280);
}

.dark .thinking-label {
  color: var(--text-secondary-dark, #9ca3af);
}

.thinking-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #3b82f6;
  animation: pulse 1.5s ease-in-out infinite;
}

.thinking-stream.thinking-evaluation .thinking-dot {
  background: #f59e0b;
}

.thinking-stream.thinking-correction .thinking-dot {
  background: #ef4444;
}

@keyframes pulse {
  0%, 100% { opacity: 0.4; }
  50% { opacity: 1; }
}

.thinking-collapse {
  font-size: 10px;
  color: var(--text-tertiary, #9ca3af);
}

.thinking-content {
  padding: 8px 10px;
  line-height: 1.6;
  color: var(--text-secondary, #4b5563);
  max-height: 200px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-word;
}

.dark .thinking-content {
  color: var(--text-secondary-dark, #d1d5db);
}

.thinking-cursor {
  color: #3b82f6;
  animation: blink 1s step-end infinite;
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}
</style>
