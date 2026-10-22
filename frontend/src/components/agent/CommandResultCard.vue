<template>
  <div class="command-card" data-testid="command-result-card">
    <div class="command-card-header">
      <span class="command-card-title" data-testid="command-title">
        /{{ commandName || 'command' }}
      </span>
      <span
        class="command-status"
        :class="'command-status-' + status"
        data-testid="command-status"
      >
        {{ statusLabel }}
      </span>
    </div>

    <!-- /export：内容可能很大（最多 2000 条消息），只呈现摘要与下载入口 -->
    <div v-if="isExport && terminal" class="command-export" data-testid="command-export-panel">
      <span class="command-export-text">
        {{ t('agentCommand.exportReady', { count: exportCount }) }}
      </span>
      <button
        type="button"
        class="command-export-btn"
        data-testid="command-export"
        :title="t('agentCommand.download', { filename: exportFilename })"
        @click="downloadExport"
      >
        ⬇ {{ t('agentCommand.download', { filename: exportFilename }) }}
      </button>
    </div>

    <div
      v-else-if="terminal?.type === 'command_error'"
      class="command-error-text"
      data-testid="command-result-content"
    >
      {{ terminal.content || t('agentCommand.noOutput') }}
    </div>

    <div v-else-if="terminal" class="command-result-content" data-testid="command-result-content">
      <Markdown :content="terminal.content || t('agentCommand.noOutput')" />
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * User Commands（#475）结果卡片：command_start + command_result/error 的配对渲染。
 *
 * 数据来源是后端既有 SSE 事件信封（content / command_name / command_data）；
 * /export 的内容只走下载，不在页面渲染全文（最多 2000 条消息，直接渲染会拖垮
 * 消息流）。下载文件名/格式由后端 command_data 决定，不在前端臆造。
 */
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { AgentEvent } from '@/types/agent'
import Markdown from '@/components/common/Markdown.vue'
import { downloadTextFile } from '@/utils/artifact'

const props = defineProps<{
  start?: AgentEvent
  terminal?: AgentEvent
}>()

const { t } = useI18n()

const commandName = computed(
  () => props.terminal?.command_name || props.start?.command_name || '',
)
const status = computed<'running' | 'completed' | 'failed'>(() => {
  if (!props.terminal) return 'running'
  return props.terminal.type === 'command_result' ? 'completed' : 'failed'
})
const statusLabel = computed(() => {
  if (status.value === 'running') return t('agentCommand.running')
  if (status.value === 'completed') return t('agentCommand.completed')
  return t('agentCommand.failed')
})

const exportFilename = computed(() => {
  const name = props.terminal?.command_data?.filename
  return typeof name === 'string' && name ? name : 'export.md'
})
const isExport = computed(
  () => props.terminal?.command_name === 'export' && !!props.terminal?.command_data?.filename,
)
const exportCount = computed(() => {
  const count = props.terminal?.command_data?.message_count
  return typeof count === 'number' ? count : 0
})

function downloadExport() {
  if (!props.terminal) return
  const format = props.terminal.command_data?.format
  downloadTextFile(
    exportFilename.value,
    props.terminal.content,
    format === 'json' ? 'json' : 'markdown',
  )
}
</script>

<style scoped>
.command-card {
  margin: 6px 0;
  border: 1px solid rgba(59, 130, 246, 0.3);
  border-left: 3px solid #3b82f6;
  border-radius: 8px;
  background: rgba(59, 130, 246, 0.05);
  padding: 8px 12px;
  font-size: 12px;
}

.dark .command-card {
  background: rgba(59, 130, 246, 0.12);
  border-color: rgba(59, 130, 246, 0.4);
}

.command-card-header {
  display: flex;
  align-items: center;
  gap: 8px;
}

.command-card-title {
  font-family: 'Cascadia Code', 'Fira Code', 'JetBrains Mono', monospace;
  font-weight: 600;
  color: #1d4ed8;
}

.dark .command-card-title {
  color: #93c5fd;
}

.command-status {
  padding: 0 6px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 600;
  line-height: 18px;
  color: #fff;
}

.command-status-running { background: #64748b; }
.command-status-completed { background: #16a34a; }
.command-status-failed { background: #dc2626; }

.command-result-content {
  margin-top: 6px;
  color: var(--text-primary, #111827);
  word-break: break-word;
}

.dark .command-result-content {
  color: var(--text-primary-dark, #f9fafb);
}

.command-error-text {
  margin-top: 6px;
  color: #b91c1c;
  word-break: break-word;
}

.dark .command-error-text {
  color: #fca5a5;
}

.command-export {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin-top: 6px;
}

.command-export-text {
  color: var(--text-secondary, #4b5563);
}

.dark .command-export-text {
  color: var(--text-secondary-dark, #9ca3af);
}

.command-export-btn {
  border: 1px solid rgba(37, 99, 235, 0.4);
  color: #2563eb;
  background: rgba(37, 99, 235, 0.06);
  border-radius: 6px;
  font-size: 12px;
  padding: 3px 10px;
  cursor: pointer;
}

.command-export-btn:hover {
  background: rgba(37, 99, 235, 0.14);
}
</style>
