<template>
  <div class="agent-input-area">
    <div class="input-row">
      <textarea
        v-model="inputText"
        @keydown.enter.exact.prevent="handleSend"
        @keydown.shift.enter="inputText += '\n'"
        :placeholder="placeholder"
        :disabled="disabled"
        class="agent-textarea"
        rows="1"
        ref="textareaEl"
      ></textarea>
      <button
        v-if="!loading"
        class="send-btn"
        :disabled="!inputText.trim() || disabled"
        @click="handleSend"
      >
        发送
      </button>
      <button
        v-else
        class="stop-btn"
        @click="$emit('stop')"
      >
        ⏹ 停止
      </button>
    </div>
    <div class="input-hint">
      Enter 发送 · Shift+Enter 换行
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, nextTick } from 'vue'

const props = defineProps<{
  placeholder?: string
  disabled?: boolean
  loading?: boolean
}>()

const emit = defineEmits<{
  send: [text: string]
  stop: []
}>()

const inputText = ref('')
const textareaEl = ref<HTMLTextAreaElement>()

function handleSend() {
  const text = inputText.value.trim()
  if (!text || props.disabled) return
  emit('send', text)
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

.send-btn, .stop-btn {
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
