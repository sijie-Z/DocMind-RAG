<template>
  <!-- 统一 diff 差异视图（工件渲染升级：工具结果像 diff 时给染色视图而非裸文本）。
       行级着色 + 旧/新行号两列 gutter；hunk 头行用青色系与文本区分。 -->
  <div class="diff-view" data-testid="diff-view">
    <div
      v-for="(line, i) in lines"
      :key="i"
      class="diff-line"
      :class="'diff-' + line.type"
    >
      <span class="diff-gutter">{{ line.oldNo ?? '' }}</span>
      <span class="diff-gutter">{{ line.newNo ?? '' }}</span>
      <span class="diff-sign">{{ signOf(line.type) }}</span>
      <span class="diff-text">{{ line.text }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { parseDiffLines, type DiffLineType } from '@/utils/artifact'

const props = defineProps<{
  text: string
}>()

const lines = computed(() => parseDiffLines(props.text))

function signOf(type: DiffLineType): string {
  if (type === 'add') return '+'
  if (type === 'del') return '-'
  if (type === 'ctx') return ' '
  return ''
}
</script>

<style scoped>
.diff-view {
  font-family: 'Cascadia Code', 'Fira Code', 'JetBrains Mono', monospace;
  font-size: 11px;
  line-height: 1.5;
  background: var(--bg-primary, #fff);
  border: 1px solid var(--border-color, #e5e7eb);
  border-radius: 8px;
  overflow-x: auto;
  padding: 4px 0;
}

.dark .diff-view {
  background: var(--bg-primary-dark, #0f172a);
  border-color: var(--border-color-dark, #374151);
}

.diff-line {
  display: flex;
  white-space: pre;
  min-width: max-content;
}

.diff-gutter {
  width: 34px;
  min-width: 34px;
  text-align: right;
  padding-right: 6px;
  color: var(--text-tertiary, #9ca3af);
  user-select: none;
  flex-shrink: 0;
}

.diff-sign {
  width: 12px;
  min-width: 12px;
  text-align: center;
  user-select: none;
  flex-shrink: 0;
}

.diff-text {
  padding-right: 12px;
}

/* 语义色与页面全域一致：加=绿 / 删=红 / hunk 头=青紫 / 文件头=灰 */
.diff-add {
  background: rgba(22, 163, 74, 0.1);
}
.diff-add .diff-text, .diff-add .diff-sign {
  color: #15803d;
}
.dark .diff-add { background: rgba(34, 197, 94, 0.14); }
.dark .diff-add .diff-text, .dark .diff-add .diff-sign { color: #86efac; }

.diff-del {
  background: rgba(220, 38, 38, 0.1);
}
.diff-del .diff-text, .diff-del .diff-sign {
  color: #b91c1c;
}
.dark .diff-del { background: rgba(248, 113, 113, 0.14); }
.dark .diff-del .diff-text, .dark .diff-del .diff-sign { color: #fca5a5; }

.diff-hunk {
  background: rgba(99, 102, 241, 0.08);
}
.diff-hunk .diff-text {
  color: #6366f1;
}
.dark .diff-hunk { background: rgba(99, 102, 241, 0.16); }
.dark .diff-hunk .diff-text { color: #a5b4fc; }

.diff-meta .diff-text {
  color: var(--text-tertiary, #9ca3af);
}

.diff-ctx .diff-text {
  color: var(--text-secondary, #4b5563);
}
.dark .diff-ctx .diff-text {
  color: var(--text-secondary-dark, #d1d5db);
}
</style>
