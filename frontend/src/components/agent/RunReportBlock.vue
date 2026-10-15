<template>
  <!-- 运行报告折叠块（issue #242 收尾的展示形态；issue #289 抽为共享组件，
       Agent 页与聊天页 Agent 模式共用，保证双入口信息同构）。
       体验第四波：正文从裸 <pre> 升级为复用聊天页 Markdown 组件（报告本身是
       ## 标题 + 列表形），并提供一键导出 Markdown。 -->
  <details v-if="report" class="run-report">
    <summary>
      {{ t('artifacts.runReport') }}<template v-if="totalTokens"> · Tokens: {{ totalTokens }}</template>
    </summary>
    <div class="run-report-body">
      <Markdown :content="report" />
    </div>
    <button
      class="run-report-export"
      data-testid="run-report-export"
      @click="handleExport"
    >
      ⬇ {{ t('artifacts.exportMarkdown') }}
    </button>
  </details>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import Markdown from '@/components/common/Markdown.vue'
import { artifactFilename, downloadTextFile } from '@/utils/artifact'

const props = defineProps<{
  report?: string
  totalTokens?: number
}>()

const { t } = useI18n()

function handleExport() {
  if (!props.report) return
  downloadTextFile(artifactFilename('run_report', 'markdown'), props.report)
}
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
.run-report-body {
  background: rgba(100, 116, 139, 0.08);
  padding: 10px;
  border-radius: 8px;
  max-height: 320px;
  overflow: auto;
}
.run-report-export {
  margin-top: 6px;
  font-size: 10px;
  color: #3b82f6;
  background: none;
  border: none;
  cursor: pointer;
  padding: 2px 4px;
  border-radius: 4px;
  transition: all 0.15s;
}
.run-report-export:hover {
  background: rgba(59, 130, 246, 0.1);
}
</style>
