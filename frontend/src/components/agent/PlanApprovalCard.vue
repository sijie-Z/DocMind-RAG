<template>
  <div class="plan-approval-card">
    <!-- B：计划审批卡（plan_approval_mode=always 的暂停现场）。
         与 ApprovalCard 同风格（dock 在输入区上方），青紫色系与工具审批
         （橙）区分：这一张管"整版计划"，那一张管"单次工具行动"。 -->
    <div class="card-header">
      <span class="plan-badge">{{ t('hitl.planTitle') }}</span>
      <span class="plan-goal">{{ t('hitl.planGoal') }}：{{ goal }}</span>
    </div>

    <div class="steps-label">
      {{ t('hitl.planSteps') }}（{{ localSteps.length }}）
    </div>

    <div class="steps">
      <div v-for="(s, i) in localSteps" :key="s.id" class="step-row">
        <span class="step-index">{{ i + 1 }}</span>
        <div class="step-body">
          <template v-if="editing">
            <textarea
              v-model="s.description"
              class="step-input"
              rows="1"
              maxlength="2000"
              :disabled="busy"
            ></textarea>
            <div class="edit-row">
              <input
                v-model="s.tool_hint"
                class="step-tool"
                maxlength="100"
                placeholder="tool_hint"
                :disabled="busy"
              />
              <button
                v-if="localSteps.length > 1"
                class="step-remove"
                :disabled="busy"
                @click="removeStep(i)"
              >
                {{ t('hitl.planRemove') }}
              </button>
            </div>
          </template>
          <template v-else>
            <div class="step-desc">{{ s.description }}</div>
            <div class="step-meta">
              <span v-if="s.tool_hint" class="tool-chip">{{ s.tool_hint }}</span>
              <span v-if="s.dependencies.length" class="dep-chip">
                {{ t('hitl.planDeps') }}: {{ s.dependencies.join(', ') }}
              </span>
            </div>
          </template>
        </div>
      </div>
      <button v-if="editing" class="add-step-btn" :disabled="busy" @click="addStep">
        + {{ t('hitl.planAddStep') }}
      </button>
    </div>

    <!-- 查看态：批准 / 编辑 / 拒绝（附理由） -->
    <template v-if="!editing">
      <textarea
        v-model="note"
        class="note-input"
        rows="2"
        maxlength="500"
        :placeholder="t('hitl.planRejectNotePlaceholder')"
        :disabled="busy"
      ></textarea>
      <div class="card-actions">
        <button class="edit-btn" :disabled="busy" @click="startEdit">
          {{ t('hitl.planEditSteps') }}
        </button>
        <button class="reject-btn" :disabled="busy" @click="emitReject">
          {{ t('hitl.planReject') }}
        </button>
        <button class="approve-btn" :disabled="busy" @click="emitApprove">
          {{ busy ? t('hitl.planSubmitting') : t('hitl.planApprove') }}
        </button>
      </div>
      <div class="reject-hint">{{ t('hitl.planRejectHint') }}</div>
    </template>

    <!-- 编辑态：保存并批准 / 取消编辑 -->
    <template v-else>
      <div class="card-actions">
        <button class="cancel-edit-btn" :disabled="busy" @click="cancelEdit">
          {{ t('hitl.planCancelEdit') }}
        </button>
        <button
          class="approve-btn"
          :disabled="busy || !editValid"
          @click="emitSaveApprove"
        >
          {{ busy ? t('hitl.planSubmitting') : t('hitl.planSaveApprove') }}
        </button>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import type { PlanStepEdit } from '@/types/agent'

const props = defineProps<{
  goal: string
  steps: PlanStepEdit[]
  busy?: boolean
}>()

const emit = defineEmits<{
  decide: [payload: { decision: 'approve' | 'edit' | 'reject'; edited_steps?: PlanStepEdit[]; note?: string }]
}>()

const { t } = useI18n()

function cloneSteps(steps: PlanStepEdit[]): PlanStepEdit[] {
  return (steps || []).map((s) => ({
    id: s.id,
    description: s.description,
    dependencies: [...(s.dependencies || [])],
    tool_hint: s.tool_hint ?? null,
  }))
}

const editing = ref(false)
const note = ref('')
const localSteps = ref<PlanStepEdit[]>(cloneSteps(props.steps))

const editValid = computed(
  () => localSteps.value.length > 0 && localSteps.value.every((s) => s.description.trim().length > 0),
)

function startEdit() {
  localSteps.value = cloneSteps(props.steps)
  editing.value = true
}

function cancelEdit() {
  editing.value = false
  localSteps.value = cloneSteps(props.steps)
}

function removeStep(index: number) {
  localSteps.value.splice(index, 1)
}

function addStep() {
  localSteps.value.push({
    id: `step-${Math.random().toString(36).slice(2, 8)}`,
    description: '',
    dependencies: [],
    tool_hint: null,
  })
}

function emitApprove() {
  emit('decide', { decision: 'approve' })
}

function emitReject() {
  const trimmed = note.value.trim()
  emit('decide', { decision: 'reject', note: trimmed || undefined })
}

function emitSaveApprove() {
  if (!editValid.value) return
  const edited: PlanStepEdit[] = localSteps.value.map((s) => ({
    id: s.id,
    description: s.description.trim(),
    dependencies: s.dependencies,
    tool_hint: s.tool_hint?.trim() ? s.tool_hint.trim() : null,
  }))
  emit('decide', { decision: 'edit', edited_steps: edited })
}
</script>

<style scoped>
.plan-approval-card {
  border: 1px solid rgba(99, 102, 241, 0.35);
  border-left: 4px solid #6366f1;
  border-radius: 10px;
  background: rgba(99, 102, 241, 0.06);
  padding: 12px 14px;
  font-size: 12px;
}

.dark .plan-approval-card {
  background: rgba(99, 102, 241, 0.12);
  border-color: rgba(129, 140, 248, 0.4);
}

.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.plan-badge {
  flex-shrink: 0;
  padding: 0 8px;
  border-radius: 4px;
  background: #6366f1;
  color: #fff;
  font-size: 11px;
  font-weight: 600;
  line-height: 20px;
}

.plan-goal {
  color: var(--text-primary, #111827);
  font-weight: 600;
  word-break: break-word;
}

.dark .plan-goal { color: var(--text-primary-dark, #f9fafb); }

.steps-label {
  color: var(--text-secondary, #6b7280);
  margin-bottom: 6px;
}

.dark .steps-label { color: var(--text-secondary-dark, #9ca3af); }

.steps {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 240px;
  overflow-y: auto;
  margin-bottom: 10px;
}

.step-row {
  display: flex;
  gap: 8px;
  align-items: flex-start;
}

.step-index {
  flex-shrink: 0;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: rgba(99, 102, 241, 0.18);
  color: #4f46e5;
  font-size: 10px;
  font-weight: 600;
  line-height: 18px;
  text-align: center;
}

.dark .step-index { color: #c7d2fe; }

.step-body { flex: 1; min-width: 0; }

.step-desc {
  color: var(--text-primary, #111827);
  line-height: 1.5;
  word-break: break-word;
}

.dark .step-desc { color: var(--text-primary-dark, #f9fafb); }

.step-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 2px;
}

.tool-chip, .dep-chip {
  padding: 0 6px;
  border-radius: 4px;
  font-size: 10px;
  line-height: 16px;
}

.tool-chip {
  background: rgba(59, 130, 246, 0.14);
  color: #2563eb;
}

.dep-chip {
  background: rgba(100, 116, 139, 0.14);
  color: #64748b;
}

.step-input {
  width: 100%;
  padding: 6px 8px;
  border-radius: 6px;
  border: 1px solid var(--border-color, #d1d5db);
  background: var(--bg-primary, #fff);
  color: var(--text-primary, #111827);
  font-size: 12px;
  font-family: inherit;
  resize: vertical;
  min-height: 30px;
  outline: none;
}

.dark .step-input {
  background: var(--bg-primary-dark, #1f2937);
  border-color: var(--border-color-dark, #4b5563);
  color: var(--text-primary-dark, #f9fafb);
}

.step-input:focus, .step-tool:focus, .note-input:focus { border-color: #6366f1; }

.edit-row {
  display: flex;
  gap: 6px;
  margin-top: 4px;
  align-items: center;
}

.step-tool {
  flex: 1;
  padding: 4px 8px;
  border-radius: 6px;
  border: 1px solid var(--border-color, #d1d5db);
  background: var(--bg-primary, #fff);
  color: var(--text-primary, #111827);
  font-size: 11px;
  outline: none;
}

.dark .step-tool {
  background: var(--bg-primary-dark, #1f2937);
  border-color: var(--border-color-dark, #4b5563);
  color: var(--text-primary-dark, #f9fafb);
}

.step-remove, .add-step-btn {
  padding: 3px 8px;
  border-radius: 6px;
  border: 1px dashed rgba(100, 116, 139, 0.5);
  background: transparent;
  color: var(--text-secondary, #6b7280);
  font-size: 11px;
  cursor: pointer;
}

.step-remove:hover, .add-step-btn:hover { border-color: #6366f1; color: #6366f1; }

.add-step-btn { align-self: flex-start; }

.note-input {
  width: 100%;
  box-sizing: border-box;
  padding: 6px 8px;
  border-radius: 6px;
  border: 1px solid var(--border-color, #d1d5db);
  background: var(--bg-primary, #fff);
  color: var(--text-primary, #111827);
  font-size: 12px;
  font-family: inherit;
  resize: vertical;
  outline: none;
  margin-bottom: 8px;
}

.dark .note-input {
  background: var(--bg-primary-dark, #1f2937);
  border-color: var(--border-color-dark, #4b5563);
  color: var(--text-primary-dark, #f9fafb);
}

.card-actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}

.approve-btn, .reject-btn, .edit-btn, .cancel-edit-btn {
  padding: 6px 14px;
  border-radius: 8px;
  border: none;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s;
}

.approve-btn { background: #16a34a; color: #fff; }
.approve-btn:hover:not(:disabled) { background: #15803d; }

.edit-btn { background: #6366f1; color: #fff; }
.edit-btn:hover:not(:disabled) { background: #4f46e5; }

.reject-btn { background: #ef4444; color: #fff; }
.reject-btn:hover:not(:disabled) { background: #dc2626; }

.cancel-edit-btn {
  background: transparent;
  color: var(--text-secondary, #6b7280);
  border: 1px solid var(--border-color, #d1d5db);
}

.cancel-edit-btn:hover:not(:disabled) { border-color: #6366f1; color: #6366f1; }

.approve-btn:disabled, .reject-btn:disabled, .edit-btn:disabled,
.cancel-edit-btn:disabled, .add-step-btn:disabled, .step-remove:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.reject-hint {
  margin-top: 6px;
  font-size: 10px;
  color: var(--text-tertiary, #9ca3af);
}
</style>
