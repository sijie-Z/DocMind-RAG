<template>
  <!-- 工件渲染器（体验第四波）：工具结果 / 运行报告的富渲染分派 ——
       diff → DiffView 差异视图；markdown → 复用聊天页 Markdown 组件；
       json → 美化 + hljs 高亮；其余 → 等宽纯文本。工具栏提供按类型下载。 -->
  <div class="artifact-viewer">
    <div class="artifact-toolbar">
      <span class="artifact-kind" data-testid="artifact-kind">{{ t('artifacts.kind.' + kind) }}</span>
      <button
        class="artifact-download"
        data-testid="artifact-download"
        :title="t('artifacts.download')"
        @click="handleDownload"
      >
        ⬇ {{ t('artifacts.download') }}
      </button>
    </div>

    <DiffView v-if="kind === 'diff'" :text="text" />
    <Markdown v-else-if="kind === 'markdown'" :content="text" />
    <pre v-else-if="jsonHtml" class="hljs artifact-code"><code v-html="jsonHtml"></code></pre>
    <pre v-else class="artifact-plain">{{ text }}</pre>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import hljs from 'highlight.js/lib/common'
import Markdown from '@/components/common/Markdown.vue'
import DiffView from '@/components/agent/DiffView.vue'
import {
  artifactFilename,
  detectArtifactKind,
  downloadTextFile,
  type ArtifactKind,
} from '@/utils/artifact'

const props = defineProps<{
  text: string
  /** 显式指定类型（如工具结果在截断预览下仍需按全文识别）；缺省自行识别。 */
  kind?: ArtifactKind
  /** 下载文件名基底（如工具名）；缺省 'artifact'。 */
  name?: string
  /** 下载源文本（完整原文）。缺省与 text 同源；**显示被截断时必须由父级传全文**
       —— 下载永远导出源文本而非截断预览（评审 F1：默认态曾静默导出 303 字符
       + "…" 的截断品，patch 不可应用/JSON 不可解析）。 */
  downloadText?: string
}>()

const { t } = useI18n()

const kind = computed<ArtifactKind>(() => props.kind ?? detectArtifactKind(props.text))

/** JSON 美化 + 高亮；文本不可解析（如截断预览）时为 null → 回退纯文本。 */
const jsonHtml = computed<string | null>(() => {
  if (kind.value !== 'json') return null
  try {
    const pretty = JSON.stringify(JSON.parse(props.text), null, 2)
    return hljs.highlight(pretty, { language: 'json', ignoreIllegals: true }).value
  } catch {
    return null
  }
})

function handleDownload() {
  downloadTextFile(
    artifactFilename(props.name || 'artifact', kind.value),
    props.downloadText ?? props.text,
    kind.value,
  )
}
</script>

<style scoped>
.artifact-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 6px;
}

.artifact-kind {
  font-size: 10px;
  font-weight: 600;
  color: var(--text-tertiary, #9ca3af);
  background: var(--bg-tertiary, #f3f4f6);
  padding: 1px 6px;
  border-radius: 4px;
  text-transform: uppercase;
  letter-spacing: 0.4px;
}

.dark .artifact-kind {
  background: var(--bg-tertiary-dark, #374151);
}

.artifact-download {
  font-size: 10px;
  color: #3b82f6;
  background: none;
  border: none;
  cursor: pointer;
  padding: 1px 4px;
  border-radius: 4px;
  transition: all 0.15s;
}

.artifact-download:hover {
  background: rgba(59, 130, 246, 0.1);
}

.artifact-code,
.artifact-plain {
  margin: 0;
  padding: 8px;
  border-radius: 6px;
  font-family: 'Cascadia Code', 'Fira Code', 'JetBrains Mono', monospace;
  font-size: 11px;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-x: auto;
}

.artifact-code {
  background: #1a1b26;
  color: #c0caf5;
}

.artifact-plain {
  color: var(--text-secondary, #4b5563);
}

.dark .artifact-plain {
  color: var(--text-secondary-dark, #d1d5db);
}

/* diff 视图自带边框配色，避免容器叠加背景 */
.artifact-viewer :deep(.markdown-body) {
  font-size: 11px;
}
</style>
