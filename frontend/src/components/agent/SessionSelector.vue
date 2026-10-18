<template>
  <div class="session-selector">
    <select
      :value="modelValue"
      :aria-label="t('agent.sessionSelectorLabel')"
      @change="$emit('update:modelValue', ($event.target as HTMLSelectElement).value)"
      class="session-select"
    >
      <option value="">{{ t('agent.newSessionOption') }}</option>
      <option v-for="s in sessions" :key="s.id" :value="s.id">
        {{ s.title || t('agent.untitledSession') }} ({{ s.message_count }})
      </option>
    </select>
    <button
      v-if="modelValue"
      class="delete-session-btn"
      :aria-label="t('agent.deleteSessionConfirmTitle')"
      :title="t('agent.deleteSessionConfirmTitle')"
      @click="$emit('delete', modelValue)"
    >
      🗑
    </button>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { AgentSession } from '@/types/agent'

const { t } = useI18n()

defineProps<{
  sessions: AgentSession[]
  modelValue: string | null
}>()

defineEmits<{
  'update:modelValue': [value: string | null]
  'delete': [sessionId: string]
}>()
</script>

<style scoped>
.session-selector {
  display: flex;
  gap: 4px;
  align-items: center;
}

.session-select {
  padding: 4px 8px;
  border-radius: 8px;
  border: 1px solid var(--border-color, #d1d5db);
  background: var(--bg-primary, #fff);
  color: var(--text-primary, #111827);
  font-size: 12px;
  max-width: 200px;
}

.dark .session-select {
  background: var(--bg-primary-dark, #1f2937);
  border-color: var(--border-color-dark, #4b5563);
  color: var(--text-primary-dark, #f9fafb);
}

.delete-session-btn {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 14px;
  padding: 2px 4px;
  border-radius: 4px;
  opacity: 0.6;
}

.delete-session-btn:hover {
  opacity: 1;
  background: rgba(239, 68, 68, 0.1);
}
</style>
