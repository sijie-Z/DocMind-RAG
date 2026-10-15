<template>
  <div class="replay-dock">
    <div class="replay-header">
      <span class="replay-icon">🕘</span>
      <span class="replay-title">{{ t('agentReplay.title') }}</span>
      <code class="replay-task-id">{{ timeline?.task_id || taskId }}</code>
      <span v-if="timeline" class="replay-chips">
        <span class="replay-chip">{{ timeline.steps_completed }}/{{ timeline.steps.length }} {{ t('agentReplay.steps') }}</span>
        <span class="replay-chip">{{ fmtMs(timeline.duration_ms) }}</span>
        <span class="replay-chip">{{ timeline.total_tokens }} {{ t('agentReplay.tokens') }}</span>
      </span>
      <button class="replay-close" :title="t('agentReplay.close')" @click="emit('close')">✕</button>
    </div>

    <div class="replay-body">
      <div v-if="loading" class="replay-state">{{ t('agentReplay.loading') }}</div>

      <!-- 诚实空态：无数据不编造轨迹（后端 404 REPLAY_NOT_FOUND 的如实映射） -->
      <div v-else-if="error === 'not_found'" class="replay-state">
        <p class="replay-state-title">{{ t('agentReplay.emptyTitle') }}</p>
        <p class="replay-state-hint">{{ t('agentReplay.emptyHint') }}</p>
      </div>
      <div v-else-if="error === 'failed'" class="replay-state">
        <p class="replay-state-title">{{ t('agentReplay.loadFailed') }}</p>
      </div>

      <template v-else-if="timeline">
        <div v-if="timeline.failures.length > 0" class="replay-failures">
          <span class="failures-badge">{{ t('agentReplay.failures') }} ×{{ timeline.failures.length }}</span>
          <span class="failures-text">{{ timeline.failures.join('；') }}</span>
        </div>

        <div v-if="timeline.steps.length === 0" class="replay-state">{{ t('agentReplay.noSteps') }}</div>
        <div v-else class="replay-steps">
          <div
            v-for="(step, i) in timeline.steps"
            :key="step.step_id || i"
            class="replay-step"
            :class="{ 'step-failed': step.status === 'failed', 'step-idle': step.status === 'skipped' || step.status === 'pending' }"
          >
            <span class="step-index">{{ i + 1 }}</span>
            <span class="step-icon">{{ statusIcon(step.status) }}</span>
            <div class="step-main">
              <div class="step-desc-line">
                <span class="step-desc">{{ step.description }}</span>
                <code v-if="step.tool_used" class="step-tool">{{ step.tool_used }}</code>
              </div>
              <div class="step-meta">
                <span class="step-status-text">{{ statusLabel(step.status) }}</span>
                <span v-if="step.duration_ms" class="step-metric">{{ fmtMs(step.duration_ms) }}</span>
                <span v-if="step.tokens_used" class="step-metric">{{ step.tokens_used }} {{ t('agentReplay.tokens') }}</span>
              </div>
              <div v-if="step.result_summary" class="step-result">{{ step.result_summary }}</div>
              <div v-if="step.error" class="step-error">❌ {{ step.error }}</div>
            </div>
          </div>
        </div>

        <div v-if="timeline.findings.length > 0" class="replay-section">
          <div class="section-title">🔎 {{ t('agentReplay.findings') }}</div>
          <ul class="section-list">
            <li v-for="(f, i) in timeline.findings" :key="'f' + i">
              {{ f.content }}
              <span class="item-src">[{{ f.source_step }}]</span>
            </li>
          </ul>
        </div>

        <div v-if="timeline.decisions.length > 0" class="replay-section">
          <div class="section-title">🧭 {{ t('agentReplay.decisions') }}</div>
          <ul class="section-list">
            <li v-for="(d, i) in timeline.decisions" :key="'d' + i">
              <span class="item-src">{{ d.phase }} · {{ d.action }}</span>
              {{ d.reasoning }}
            </li>
          </ul>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { ReplayTimeline } from '@/types/agent'

defineProps<{
  /** 请求的任务 id（timeline 未到时也要显示）。 */
  taskId: string
  /** 结构化回放数据（loading/not_found 时为 null/undefined）。 */
  timeline?: ReplayTimeline | null
  loading?: boolean
  /** '' = 无错误；'not_found' = 诚实空态；'failed' = 加载失败。 */
  error?: '' | 'not_found' | 'failed'
}>()

const emit = defineEmits<{ close: [] }>()

const { t } = useI18n()

function fmtMs(ms: number): string {
  if (!ms) return ''
  if (ms < 1000) return `${Math.round(ms)}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function statusIcon(status: string): string {
  return (
    { completed: '✅', failed: '❌', skipped: '⏭️', pending: '⏳', running: '⏳' } as Record<string, string>
  )[status] || '❓'
}

function statusLabel(status: string): string {
  const key =
    { completed: 'stateCompleted', failed: 'stateFailed', skipped: 'stateSkipped', pending: 'statePending', running: 'stateRunning' } as Record<string, string>
  return key[status] ? t(`agentReplay.${key[status]}`) : status
}
</script>

<style scoped>
.replay-dock {
  margin: 0 16px;
  border: 1px solid var(--border-color, #e5e7eb);
  border-left: 4px solid #2563eb;
  border-radius: 8px;
  background: var(--card-bg, #f8fafc);
  flex-shrink: 0;
}

.replay-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--border-color, #e5e7eb);
}

.replay-icon {
  font-size: 15px;
}

.replay-title {
  font-weight: 600;
  font-size: 13px;
  color: var(--text-primary, #111827);
}

.replay-task-id {
  font-size: 11px;
  color: #2563eb;
  background: rgba(37, 99, 235, 0.08);
  border-radius: 4px;
  padding: 1px 6px;
}

.replay-chips {
  display: flex;
  gap: 6px;
  margin-left: auto;
}

.replay-chip {
  font-size: 11px;
  color: var(--text-secondary, #6b7280);
  background: var(--bg-secondary, #f1f5f9);
  border-radius: 10px;
  padding: 1px 8px;
}

.replay-close {
  border: none;
  background: transparent;
  cursor: pointer;
  font-size: 13px;
  color: var(--text-secondary, #6b7280);
  padding: 2px 6px;
  border-radius: 4px;
}

.replay-close:hover {
  background: var(--bg-secondary, #f1f5f9);
}

.replay-body {
  max-height: 38vh;
  overflow-y: auto;
  padding: 10px 12px;
}

.replay-state {
  padding: 14px 4px;
  text-align: center;
  color: var(--text-secondary, #6b7280);
  font-size: 13px;
}

.replay-state-title {
  font-weight: 600;
  color: var(--text-primary, #111827);
  margin: 0 0 6px;
}

.replay-state-hint {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
}

.replay-failures {
  display: flex;
  gap: 8px;
  align-items: baseline;
  background: rgba(220, 38, 38, 0.06);
  border: 1px solid rgba(220, 38, 38, 0.25);
  border-radius: 6px;
  padding: 6px 10px;
  margin-bottom: 10px;
  font-size: 12px;
}

.failures-badge {
  color: #dc2626;
  font-weight: 600;
  white-space: nowrap;
}

.failures-text {
  color: var(--text-secondary, #6b7280);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.replay-steps {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.replay-step {
  display: flex;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 6px;
  background: var(--card-bg, #fff);
  border: 1px solid var(--border-color, #e5e7eb);
}

.replay-step.step-failed {
  border-color: rgba(220, 38, 38, 0.5);
  background: rgba(220, 38, 38, 0.05);
}

.replay-step.step-idle {
  opacity: 0.65;
}

.step-index {
  font-size: 11px;
  color: var(--text-secondary, #9ca3af);
  min-width: 16px;
  padding-top: 2px;
}

.step-icon {
  font-size: 13px;
  padding-top: 1px;
}

.step-main {
  flex: 1;
  min-width: 0;
}

.step-desc-line {
  display: flex;
  gap: 8px;
  align-items: baseline;
  flex-wrap: wrap;
}

.step-desc {
  font-size: 13px;
  color: var(--text-primary, #111827);
}

.step-tool {
  font-size: 11px;
  color: #7c3aed;
  background: rgba(124, 58, 237, 0.08);
  border-radius: 4px;
  padding: 0 6px;
}

.step-meta {
  display: flex;
  gap: 10px;
  margin-top: 2px;
  font-size: 11px;
  color: var(--text-secondary, #6b7280);
}

.step-status-text {
  font-weight: 600;
}

.step-failed .step-status-text,
.step-failed .step-error {
  color: #dc2626;
}

.step-failed .step-error {
  margin-top: 4px;
  font-size: 12px;
  word-break: break-all;
}

.step-result {
  margin-top: 4px;
  font-size: 12px;
  color: var(--text-secondary, #6b7280);
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
}

.replay-section {
  margin-top: 12px;
}

.section-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary, #111827);
  margin-bottom: 4px;
}

.section-list {
  margin: 0;
  padding-left: 18px;
  font-size: 12px;
  color: var(--text-secondary, #6b7280);
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.item-src {
  color: #2563eb;
  font-size: 11px;
  margin-right: 4px;
}

/* ── 暗色适配（与 ApprovalCard/PlanTree 同款 .dark 覆写惯例）── */
.dark .replay-dock {
  background: var(--card-bg-dark, #1f2937);
  border-color: var(--border-color-dark, #374151);
  border-left-color: #3b82f6;
}

.dark .replay-header {
  border-bottom-color: var(--border-color-dark, #374151);
}

.dark .replay-title,
.dark .replay-state-title,
.dark .section-title {
  color: var(--text-primary-dark, #f9fafb);
}

.dark .replay-task-id {
  color: #93c5fd;
  background: rgba(59, 130, 246, 0.15);
}

.dark .replay-chip {
  color: var(--text-secondary-dark, #9ca3af);
  background: var(--bg-secondary-dark, #111827);
}

.dark .replay-step {
  background: var(--bg-secondary-dark, #111827);
  border-color: var(--border-color-dark, #374151);
}

.dark .replay-step.step-failed {
  border-color: rgba(248, 113, 113, 0.5);
  background: rgba(220, 38, 38, 0.12);
}

.dark .step-desc {
  color: var(--text-primary-dark, #f9fafb);
}

.dark .step-tool {
  color: #c4b5fd;
  background: rgba(124, 58, 237, 0.2);
}

.dark .step-meta,
.dark .step-result,
.dark .section-list,
.dark .replay-state {
  color: var(--text-secondary-dark, #9ca3af);
}

.dark .replay-close {
  color: var(--text-secondary-dark, #9ca3af);
}

.dark .replay-close:hover {
  background: var(--bg-secondary-dark, #111827);
}
</style>
