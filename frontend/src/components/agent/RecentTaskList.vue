<template>
  <div class="task-list">
    <div v-if="loading" class="task-state">{{ t('agentTasks.loading') }}</div>
    <div v-else-if="tasks.length === 0" class="task-state">{{ t('agentTasks.empty') }}</div>
    <div v-else class="task-rows">
      <div v-for="task in tasks" :key="task.task_id" class="task-row">
        <span class="task-state-dot" :class="`state-${task.state}`"></span>
        <div class="task-main">
          <div class="task-query" :title="task.query">{{ task.query || '—' }}</div>
          <div class="task-meta">
            <span class="task-state-label">{{ stateLabel(task.state) }}</span>
            <span class="task-time">{{ fmtTime(task.created_at) }}</span>
          </div>
        </div>
        <button
          v-if="task.has_replay"
          class="task-replay-btn"
          @click="emit('view-replay', task.task_id)"
        >
          {{ t('agentTasks.replay') }}
        </button>
        <span v-else class="task-no-replay">{{ t('agentTasks.noReplay') }}</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { AgentTaskListItem } from '@/types/agent'

defineProps<{
  tasks: AgentTaskListItem[]
  loading?: boolean
}>()

const emit = defineEmits<{ 'view-replay': [taskId: string] }>()

const { t } = useI18n()

function stateLabel(state: string): string {
  const key =
    {
      completed: 'stateCompleted',
      failed: 'stateFailed',
      cancelled: 'stateCancelled',
      running: 'stateRunning',
      waiting_tool: 'stateRunning',
      waiting_approval: 'stateWaitingApproval',
      waiting_human: 'stateWaitingApproval',
      pending: 'statePending',
      abandoned: 'stateCancelled',
    } as Record<string, string>
  return key[state] ? t(`agentTasks.${key[state]}`) : state
}

function fmtTime(iso: string): string {
  if (!iso) return ''
  // ISO → MM-DD HH:mm（后端时间戳；解析失败原样透传）
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/)
  return m ? `${m[2]}-${m[3]} ${m[4]}:${m[5]}` : iso
}
</script>

<style scoped>
.task-list {
  width: 340px;
  max-height: 360px;
  overflow-y: auto;
}

.task-state {
  padding: 16px 4px;
  text-align: center;
  font-size: 13px;
  color: var(--text-secondary, #6b7280);
}

.task-rows {
  display: flex;
  flex-direction: column;
}

.task-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 6px;
  border-bottom: 1px solid var(--border-color, #f1f5f9);
}

.task-row:last-child {
  border-bottom: none;
}

.task-state-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
  background: #9ca3af;
}

.task-state-dot.state-completed {
  background: #16a34a;
}

.task-state-dot.state-failed {
  background: #dc2626;
}

.task-state-dot.state-cancelled,
.task-state-dot.state-abandoned {
  background: #9ca3af;
}

.task-state-dot.state-running,
.task-state-dot.state-waiting_tool {
  background: #2563eb;
}

.task-state-dot.state-waiting_approval,
.task-state-dot.state-waiting_human {
  background: #f59e0b;
}

.task-main {
  flex: 1;
  min-width: 0;
}

.task-query {
  font-size: 13px;
  color: var(--text-primary, #111827);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-meta {
  display: flex;
  gap: 8px;
  font-size: 11px;
  color: var(--text-secondary, #6b7280);
  margin-top: 1px;
}

.task-replay-btn {
  flex-shrink: 0;
  border: 1px solid rgba(37, 99, 235, 0.4);
  color: #2563eb;
  background: rgba(37, 99, 235, 0.06);
  border-radius: 6px;
  font-size: 12px;
  padding: 2px 10px;
  cursor: pointer;
}

.task-replay-btn:hover {
  background: rgba(37, 99, 235, 0.14);
}

.task-no-replay {
  flex-shrink: 0;
  font-size: 11px;
  color: var(--text-secondary, #9ca3af);
}

.dark .task-query {
  color: var(--text-primary-dark, #f9fafb);
}

.dark .task-meta,
.dark .task-state,
.dark .task-no-replay {
  color: var(--text-secondary-dark, #9ca3af);
}

.dark .task-row {
  border-bottom-color: var(--border-color-dark, #374151);
}

.dark .task-replay-btn {
  color: #93c5fd;
  border-color: rgba(59, 130, 246, 0.5);
  background: rgba(59, 130, 246, 0.12);
}
</style>
