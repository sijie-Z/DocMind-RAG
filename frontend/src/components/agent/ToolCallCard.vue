<template>
  <div class="tool-call-card" :class="'tool-' + status">
    <!-- Header -->
    <div class="tool-header" @click="collapsed = !collapsed">
      <div class="tool-left">
        <span class="tool-icon">{{ statusIcon }}</span>
        <span class="tool-name">{{ toolName }}</span>
        <span v-if="duration" class="tool-duration">{{ duration }}</span>
        <span v-if="(retryAttempt ?? 0) > 0" class="tool-retry">重试 {{ retryAttempt }}</span>
      </div>
      <span class="tool-toggle">{{ collapsed ? '▶' : '▼' }}</span>
    </div>

    <!-- Args -->
    <div v-if="!collapsed" class="tool-body">
      <div v-if="argsStr" class="tool-args">
        <span class="args-label">参数:</span>
        <code>{{ argsStr }}</code>
      </div>

      <!-- Result -->
      <div v-if="resultText" class="tool-result" :class="{ 'is-error': status === 'error' }">
        <div class="result-content" ref="resultEl">
          <pre>{{ displayResult }}</pre>
        </div>
        <button
          v-if="resultText.length > 300"
          class="expand-btn"
          @click="expanded = !expanded"
        >
          {{ expanded ? '收起' : '展开全部' }}
        </button>
      </div>

      <div v-if="status === 'loading'" class="tool-loading">
        <span class="loading-spinner"></span>
        执行中...
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'

const props = defineProps<{
  toolName: string
  toolArgs?: Record<string, any>
  resultText?: string
  duration?: number
  status: 'loading' | 'success' | 'error'
  retryAttempt?: number
}>()

const collapsed = ref(false)
const expanded = ref(false)
const resultEl = ref<HTMLElement>()

const statusIcon = computed(() => {
  switch (props.status) {
    case 'loading': return '⏳'
    case 'success': return '✅'
    case 'error': return '❌'
  }
})

const argsStr = computed(() => {
  if (!props.toolArgs || Object.keys(props.toolArgs).length === 0) return ''
  return Object.entries(props.toolArgs)
    .map(([k, v]) => {
      const val = typeof v === 'string' ? `"${v.length > 60 ? v.slice(0, 60) + '...' : v}"` : JSON.stringify(v)
      return `${k}=${val}`
    })
    .join(', ')
})

const displayResult = computed(() => {
  if (!props.resultText) return ''
  if (expanded.value || props.resultText.length <= 300) return props.resultText
  return props.resultText.slice(0, 300) + '...'
})

const duration = computed(() => {
  if (!props.duration) return ''
  if (props.duration < 1000) return `${Math.round(props.duration)}ms`
  return `${(props.duration / 1000).toFixed(1)}s`
})
</script>

<style scoped>
.tool-call-card {
  margin: 6px 0;
  border-radius: 10px;
  border: 1px solid var(--border-color, #e5e7eb);
  overflow: hidden;
  transition: border-color 0.2s;
}

.dark .tool-call-card {
  border-color: var(--border-color-dark, #374151);
}

.tool-call-card.tool-loading {
  border-color: #93c5fd;
}

.tool-call-card.tool-success {
  border-color: #86efac;
}

.tool-call-card.tool-error {
  border-color: #fca5a5;
}

.tool-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 10px;
  cursor: pointer;
  user-select: none;
  background: var(--bg-secondary, #f9fafb);
}

.dark .tool-header {
  background: var(--bg-secondary-dark, #111827);
}

.tool-left {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.tool-icon {
  font-size: 14px;
}

.tool-name {
  font-size: 12px;
  font-weight: 600;
  font-family: monospace;
  color: var(--text-primary, #111827);
}

.dark .tool-name {
  color: var(--text-primary-dark, #f9fafb);
}

.tool-duration {
  font-size: 10px;
  color: var(--text-tertiary, #9ca3af);
  background: var(--bg-tertiary, #e5e7eb);
  padding: 1px 6px;
  border-radius: 6px;
}

.tool-retry {
  font-size: 10px;
  color: #f59e0b;
  font-weight: 500;
}

.tool-toggle {
  font-size: 10px;
  color: var(--text-tertiary, #9ca3af);
}

.tool-body {
  padding: 8px 10px;
}

.tool-args {
  margin-bottom: 6px;
}

.args-label {
  font-size: 11px;
  color: var(--text-tertiary, #9ca3af);
  margin-right: 4px;
}

.tool-args code {
  font-size: 11px;
  color: var(--text-secondary, #4b5563);
  background: var(--bg-tertiary, #f3f4f6);
  padding: 1px 4px;
  border-radius: 4px;
  word-break: break-all;
}

.dark .tool-args code {
  color: var(--text-secondary-dark, #d1d5db);
  background: var(--bg-tertiary-dark, #374151);
}

.tool-result {
  background: var(--bg-tertiary, #f9fafb);
  border-radius: 8px;
  padding: 8px;
  font-size: 11px;
}

.dark .tool-result {
  background: var(--bg-tertiary-dark, #1f2937);
}

.tool-result.is-error {
  background: rgba(239, 68, 68, 0.06);
}

.result-content {
  max-height: 150px;
  overflow-y: auto;
}

.result-content pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--text-secondary, #4b5563);
  font-family: inherit;
}

.dark .result-content pre {
  color: var(--text-secondary-dark, #d1d5db);
}

.expand-btn {
  margin-top: 4px;
  font-size: 11px;
  color: #3b82f6;
  background: none;
  border: none;
  cursor: pointer;
  padding: 2px 0;
}

.expand-btn:hover {
  color: #2563eb;
}

.tool-loading {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: var(--text-tertiary, #9ca3af);
  padding: 4px 0;
}

.loading-spinner {
  width: 12px;
  height: 12px;
  border: 2px solid var(--border-color, #e5e7eb);
  border-top-color: #3b82f6;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
