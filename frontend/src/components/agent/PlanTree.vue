<template>
  <div class="plan-tree" v-if="steps.length > 0">
    <div class="plan-header">
      <span class="plan-title">📋 执行计划</span>
      <span class="plan-stats">{{ completedCount }}/{{ steps.length }} 步</span>
    </div>

    <!-- Progress bar -->
    <div class="progress-bar">
      <div class="progress-fill" :style="{ width: progressPercent + '%' }"></div>
    </div>

    <!-- Steps -->
    <div class="step-list">
      <div
        v-for="step in steps"
        :key="step.id"
        class="step-item"
        :class="'step-' + step.status"
      >
        <div class="step-indicator">
          <span v-if="step.status === 'completed'" class="step-icon done">✓</span>
          <span v-else-if="step.status === 'running'" class="step-icon running">⏳</span>
          <span v-else-if="step.status === 'failed'" class="step-icon failed">✗</span>
          <span v-else-if="step.status === 'skipped'" class="step-icon skipped">⬜</span>
          <span v-else class="step-icon pending">○</span>
        </div>
        <div class="step-content">
          <span class="step-description">{{ step.description }}</span>
          <span v-if="step.tool_hint" class="step-tool-hint">🔧 {{ step.tool_hint }}</span>
          <span v-if="step.retry_count > 0" class="step-retry">重试 {{ step.retry_count }} 次</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { PlanStep } from '@/types/agent'

const props = defineProps<{
  steps: PlanStep[]
  progress?: number
}>()

const completedCount = computed(() =>
  props.steps.filter((s) => s.status === 'completed' || s.status === 'skipped').length,
)

const progressPercent = computed(() => {
  if (props.progress !== undefined) return Math.round(props.progress * 100)
  if (props.steps.length === 0) return 0
  return Math.round((completedCount.value / props.steps.length) * 100)
})
</script>

<style scoped>
.plan-tree {
  background: var(--card-bg, #fff);
  border-radius: 12px;
  padding: 16px;
  border: 1px solid var(--border-color, #e5e7eb);
}

.dark .plan-tree {
  background: var(--card-bg-dark, #1f2937);
  border-color: var(--border-color-dark, #374151);
}

.plan-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}

.plan-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary, #111827);
}

.dark .plan-title {
  color: var(--text-primary-dark, #f9fafb);
}

.plan-stats {
  font-size: 11px;
  color: var(--text-tertiary, #9ca3af);
  background: var(--bg-secondary, #f3f4f6);
  padding: 2px 8px;
  border-radius: 8px;
}

.dark .plan-stats {
  background: var(--bg-secondary-dark, #374151);
}

.progress-bar {
  height: 3px;
  background: var(--bg-tertiary, #e5e7eb);
  border-radius: 2px;
  margin-bottom: 12px;
  overflow: hidden;
}

.dark .progress-bar {
  background: var(--bg-tertiary-dark, #4b5563);
}

.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #3b82f6, #6366f1);
  border-radius: 2px;
  transition: width 0.3s ease;
}

.step-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.step-item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 8px;
  transition: background 0.2s;
}

.step-item.step-running {
  background: rgba(59, 130, 246, 0.08);
}

.step-item.step-failed {
  background: rgba(239, 68, 68, 0.08);
}

.step-indicator {
  flex-shrink: 0;
  width: 20px;
  height: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.step-icon {
  font-size: 12px;
  line-height: 1;
}

.step-icon.done { color: #10b981; }
.step-icon.running { color: #3b82f6; animation: spin 1.5s linear infinite; }
.step-icon.failed { color: #ef4444; }
.step-icon.skipped { color: #9ca3af; }
.step-icon.pending { color: #d1d5db; }

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.step-content {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.step-description {
  font-size: 12px;
  color: var(--text-primary, #111827);
  line-height: 1.4;
}

.dark .step-description {
  color: var(--text-primary-dark, #e5e7eb);
}

.step-tool-hint {
  font-size: 10px;
  color: var(--text-tertiary, #9ca3af);
  font-family: monospace;
}

.step-retry {
  font-size: 10px;
  color: #f59e0b;
}
</style>
