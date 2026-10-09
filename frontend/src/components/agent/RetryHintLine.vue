<template>
  <!-- retry_hint 轻量提示行（issue #250 第 6 条收尾的展示形态；issue #289
       抽为共享组件，Agent 页与聊天页 Agent 模式共用） -->
  <div class="retry-hint">♻ {{ text }}</div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { formatRetryHint } from '@/utils/agentEventLabels'

const props = defineProps<{
  content?: string
}>()

const text = computed(() => formatRetryHint(props.content))
</script>

<style scoped>
/* 风格对齐 run-report 的轻量辅助信息 */
.retry-hint {
  margin: 4px 0;
  padding: 6px 10px;
  font-size: 12px;
  color: #64748b;
  background: rgba(100, 116, 139, 0.08);
  border-radius: 8px;
}
</style>
