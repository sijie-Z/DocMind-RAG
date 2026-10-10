<template>
  <div class="agent-input-area">
    <div class="input-row">
      <textarea
        v-model="inputText"
        @keydown.enter.exact.prevent="handleEnter"
        @keydown.shift.enter="inputText += '\n'"
        :placeholder="placeholder"
        :disabled="disabled"
        class="agent-textarea"
        rows="1"
        ref="textareaEl"
      ></textarea>
      <template v-if="steering">
        <!-- A：运行中转向（steering）——不打断当前步骤，步边界生效。
             转向模式出现时（running / waiting_approval）以"发送指导"为主动作；
             仍在运行中（loading）时保留"停止"。 -->
        <button
          class="steer-btn"
          :disabled="!inputText.trim() || disabled"
          data-testid="steer-btn"
          @click="handleSteer"
        >
          {{ t('hitl.steerSend') }}
        </button>
        <button
          v-if="loading"
          class="stop-btn"
          :disabled="stopping"
          @click="$emit('stop')"
        >
          {{ stopping ? '⏳ 停止中…' : '⏹ 停止' }}
        </button>
      </template>
      <button
        v-else-if="!loading"
        class="send-btn"
        :disabled="!inputText.trim() || disabled"
        @click="handleSend"
      >
        发送
      </button>
      <button
        v-else
        class="stop-btn"
        :disabled="stopping"
        @click="$emit('stop')"
      >
        {{ stopping ? '⏳ 停止中…' : '⏹ 停止' }}
      </button>
    </div>
    <div class="input-hint">
      {{ steering ? t('hitl.steerHint') : 'Enter 发送 · Shift+Enter 换行' }}
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps<{
  placeholder?: string
  disabled?: boolean
  loading?: boolean
  /** HITL：服务端取消已受理、等待 run 在检查点收敛（按钮显示"停止中…"）。 */
  stopping?: boolean
  /** A：运行中转向——loading 时显示"发送指导"按钮，Enter 亦发送指导。 */
  steering?: boolean
}>()

const emit = defineEmits<{
  send: [text: string]
  stop: []
  steer: [text: string]
}>()

const { t } = useI18n()
const inputText = ref('')
const textareaEl = ref<HTMLTextAreaElement>()

function handleSend() {
  const text = inputText.value.trim()
  if (!text || props.disabled) return
  emit('send', text)
  inputText.value = ''
}

/** Enter：转向模式发指导（running / waiting_approval 皆然）；其余维持"发送"。 */
function handleEnter() {
  if (props.steering) {
    handleSteer()
    return
  }
  handleSend()
}

function handleSteer() {
  const text = inputText.value.trim()
  if (!text || props.disabled) return
  emit('steer', text)
  inputText.value = ''
}

// Auto-resize textarea
watch(inputText, async () => {
  await nextTick()
  if (textareaEl.value) {
    textareaEl.value.style.height = 'auto'
    textareaEl.value.style.height = Math.min(textareaEl.value.scrollHeight, 120) + 'px'
  }
})

defineExpose({ clear: () => { inputText.value = '' } })
</script>

<style scoped>
.agent-input-area {
  border-top: 1px solid var(--border-color, #e5e7eb);
  padding: 12px 16px;
}

.dark .agent-input-area {
  border-color: var(--border-color-dark, #374151);
}

.input-row {
  display: flex;
  gap: 8px;
  align-items: flex-end;
}

.agent-textarea {
  flex: 1;
  padding: 10px 14px;
  border-radius: 12px;
  border: 1px solid var(--border-color, #d1d5db);
  background: var(--bg-primary, #fff);
  color: var(--text-primary, #111827);
  font-size: 13px;
  font-family: inherit;
  resize: none;
  outline: none;
  max-height: 120px;
  line-height: 1.5;
}

.dark .agent-textarea {
  background: var(--bg-primary-dark, #1f2937);
  border-color: var(--border-color-dark, #4b5563);
  color: var(--text-primary-dark, #f9fafb);
}

.agent-textarea:focus {
  border-color: #3b82f6;
  box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.15);
}

.agent-textarea:disabled {
  opacity: 0.6;
}

.send-btn, .stop-btn, .steer-btn {
  padding: 10px 20px;
  border-radius: 12px;
  border: none;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
}

.send-btn {
  background: #3b82f6;
  color: white;
}

.send-btn:hover:not(:disabled) { background: #2563eb; }
.send-btn:disabled { opacity: 0.5; cursor: not-allowed; }

/* A：运行中转向——蓝色系（与"停止"的红形成动作区分），深色下同色仍达标 */
.steer-btn {
  background: #6366f1;
  color: white;
}

.steer-btn:hover:not(:disabled) { background: #4f46e5; }
.steer-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.dark .steer-btn { background: #818cf8; }
.dark .steer-btn:hover:not(:disabled) { background: #6366f1; }

.stop-btn {
  background: #ef4444;
  color: white;
}

.stop-btn:hover { background: #dc2626; }

.input-hint {
  margin-top: 4px;
  font-size: 10px;
  color: var(--text-tertiary, #9ca3af);
  text-align: center;
}
</style>
