<template>
  <div class="approval-card" :class="{ expired }">
    <div class="approval-header">
      <span class="approval-shield">🛡</span>
      <span class="approval-title">工具执行前需人工审批</span>
      <span class="risk-badge" :class="`risk-${riskLevel}`">{{ riskLabel }}</span>
      <span v-if="expired" class="expired-badge">已超时</span>
    </div>

    <div class="approval-tool">
      <span class="tool-label">工具</span>
      <code class="tool-name">{{ toolName }}</code>
    </div>

    <pre class="tool-args">{{ argsText }}</pre>

    <p v-if="reason" class="approval-reason">{{ reason }}</p>

    <textarea
      v-model="instruction"
      class="instruction-input"
      rows="2"
      :placeholder="'改指令 / 备注（可选）：批准时作为人工指导注入后续步骤；拒绝时作为备注'"
      :disabled="busy || expired"
    ></textarea>

    <div class="approval-actions">
      <button
        class="approve-btn"
        :disabled="busy || expired"
        @click="emitDecision('approve')"
      >
        {{ busy ? '提交中…' : '✓ 批准' }}
      </button>
      <button
        class="deny-btn"
        :disabled="busy || expired"
        @click="emitDecision('deny')"
      >
        ✕ 拒绝
      </button>
    </div>
    <div class="approval-hint">
      批准 = 放行这一次调用（相同调用本任务内不再打扰）；拒绝 = 该调用不执行，
      Agent 会收到结构化失败并调整策略。
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'

const props = defineProps<{
  toolName: string
  toolArgs?: Record<string, any>
  riskLevel?: string
  reason?: string
  expired?: boolean
  busy?: boolean
}>()

const emit = defineEmits<{
  decide: [payload: { decision: 'approve' | 'deny'; modifiedInstruction?: string; note?: string }]
}>()

const instruction = ref('')

const riskLabel = computed(() => {
  switch (props.riskLevel) {
    case 'high':
      return '高风险'
    case 'medium':
      return '中风险'
    default:
      return '低风险'
  }
})

const argsText = computed(() => {
  try {
    return JSON.stringify(props.toolArgs || {}, null, 2)
  } catch {
    return String(props.toolArgs)
  }
})

function emitDecision(decision: 'approve' | 'deny') {
  const text = instruction.value.trim()
  if (decision === 'approve') {
    emit('decide', text ? { decision, modifiedInstruction: text } : { decision })
  } else {
    emit('decide', text ? { decision, note: text } : { decision })
  }
}
</script>

<style scoped>
.approval-card {
  border: 1px solid rgba(245, 158, 11, 0.5);
  border-left: 4px solid #f59e0b;
  border-radius: 12px;
  background: rgba(245, 158, 11, 0.06);
  padding: 12px 16px;
  margin: 8px 0;
}

.dark .approval-card {
  background: rgba(245, 158, 11, 0.1);
}

.approval-card.expired {
  border-color: rgba(148, 163, 184, 0.5);
  border-left-color: #94a3b8;
  background: rgba(148, 163, 184, 0.08);
}

.approval-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.approval-shield { font-size: 16px; }

.approval-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary, #111827);
}

.dark .approval-title { color: var(--text-primary-dark, #f9fafb); }

.risk-badge {
  font-size: 10px;
  padding: 1px 8px;
  border-radius: 6px;
  font-weight: 600;
}

.risk-high { background: #dc2626; color: #fff; }
.risk-medium { background: #f59e0b; color: #fff; }
.risk-low { background: #64748b; color: #fff; }

.expired-badge {
  font-size: 10px;
  padding: 1px 8px;
  border-radius: 6px;
  background: #94a3b8;
  color: #fff;
}

.approval-tool {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  margin-bottom: 4px;
}

.tool-label { color: var(--text-tertiary, #9ca3af); }

.tool-name {
  font-family: monospace;
  font-size: 12px;
  background: var(--bg-tertiary, #f3f4f6);
  padding: 1px 6px;
  border-radius: 4px;
  color: var(--text-primary, #111827);
}

.dark .tool-name {
  background: var(--bg-tertiary-dark, #374151);
  color: var(--text-primary-dark, #f9fafb);
}

.tool-args {
  font-size: 11px;
  font-family: monospace;
  background: var(--bg-secondary, #f9fafb);
  border: 1px solid var(--border-color, #e5e7eb);
  border-radius: 8px;
  padding: 8px 10px;
  margin: 4px 0 8px;
  max-height: 160px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
  color: var(--text-secondary, #4b5563);
}

.dark .tool-args {
  background: var(--bg-secondary-dark, #1f2937);
  border-color: var(--border-color-dark, #374151);
  color: var(--text-secondary-dark, #9ca3af);
}

.approval-reason {
  font-size: 12px;
  color: #b45309;
  margin: 0 0 8px;
}

.approval-card.expired .approval-reason { color: #64748b; }

.instruction-input {
  width: 100%;
  box-sizing: border-box;
  border: 1px solid var(--border-color, #d1d5db);
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 12px;
  font-family: inherit;
  resize: vertical;
  outline: none;
  background: var(--bg-primary, #fff);
  color: var(--text-primary, #111827);
  margin-bottom: 8px;
}

.dark .instruction-input {
  background: var(--bg-primary-dark, #1f2937);
  border-color: var(--border-color-dark, #4b5563);
  color: var(--text-primary-dark, #f9fafb);
}

.instruction-input:focus { border-color: #3b82f6; }

.approval-actions {
  display: flex;
  gap: 8px;
}

.approve-btn, .deny-btn {
  padding: 6px 16px;
  border-radius: 8px;
  border: none;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s;
}

.approve-btn { background: #16a34a; color: #fff; }
.approve-btn:hover:not(:disabled) { background: #15803d; }

.deny-btn { background: #ef4444; color: #fff; }
.deny-btn:hover:not(:disabled) { background: #dc2626; }

.approve-btn:disabled, .deny-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.approval-hint {
  margin-top: 6px;
  font-size: 10px;
  color: var(--text-tertiary, #9ca3af);
  line-height: 1.5;
}
</style>
