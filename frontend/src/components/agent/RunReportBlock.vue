<template>
  <!-- 运行报告折叠块（issue #242 收尾的展示形态；issue #289 抽为共享组件，
       Agent 页与聊天页 Agent 模式共用，保证双入口信息同构） -->
  <details v-if="report" class="run-report">
    <summary>
      运行报告<template v-if="totalTokens"> · Tokens: {{ totalTokens }}</template>
    </summary>
    <pre>{{ report }}</pre>
  </details>
</template>

<script setup lang="ts">
defineProps<{
  report?: string
  totalTokens?: number
}>()
</script>

<style scoped>
.run-report {
  margin-top: 10px;
  font-size: 12px;
}
.run-report summary {
  cursor: pointer;
  color: #64748b;
  user-select: none;
}
.run-report pre {
  white-space: pre-wrap;
  word-break: break-word;
  background: rgba(100, 116, 139, 0.08);
  padding: 10px;
  border-radius: 8px;
  max-height: 320px;
  overflow: auto;
}
</style>
