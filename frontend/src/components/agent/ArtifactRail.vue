<template>
  <!-- 工件右栏（批次七）：真实 API 数据驱动的列表 + 预览/源码切换 + 版本历史 + 下载。
       与旧 ArtifactViewer（文本嗅探）的分工：那是「单条工具结果长什么样」的渲染器；
       这里是「本轮/本会话产出了哪些文件」的持久化视图，数据来自 /api/v1/artifacts。 -->
  <div class="artifact-rail" data-testid="artifact-rail">
    <div class="rail-header">
      <span class="rail-title">{{ t('artifacts.panel.title') }}</span>
      <button
        class="rail-close"
        data-testid="artifact-rail-close"
        :title="t('common.close')"
        @click="$emit('close')"
      >
        ×
      </button>
    </div>

    <div v-if="loading" class="rail-state" data-testid="artifact-rail-loading">
      {{ t('common.loading') }}
    </div>
    <div v-else-if="error" class="rail-state rail-error" data-testid="artifact-rail-error">
      {{ t('artifacts.panel.loadFailed') }}
    </div>
    <div v-else-if="items.length === 0" class="rail-state" data-testid="artifact-rail-empty">
      {{ t('artifacts.panel.empty') }}
    </div>

    <template v-else>
      <!-- 列表 -->
      <div class="rail-list" data-testid="artifact-list">
        <button
          v-for="item in items"
          :key="item.id"
          class="rail-item"
          :class="{ active: selected?.id === item.id }"
          :data-testid="`artifact-item-${item.id}`"
          @click="select(item)"
        >
          <span class="item-kind" :data-kind="item.kind">{{ item.kind }}</span>
          <span class="item-title">{{ item.title }}</span>
          <span class="item-meta">
            v{{ item.version_no }} · {{ formatSize(item.file_size) }}
          </span>
        </button>
      </div>

      <!-- 选中工件的预览 -->
      <div v-if="selected" class="rail-detail" data-testid="artifact-detail">
        <div class="detail-header">
          <span class="detail-title" data-testid="artifact-detail-title">{{ selected.title }}</span>
          <div class="detail-actions">
            <button
              class="detail-btn"
              :class="{ active: mode === 'preview' }"
              data-testid="artifact-mode-preview"
              @click="mode = 'preview'"
            >
              {{ t('artifacts.panel.preview') }}
            </button>
            <button
              class="detail-btn"
              :class="{ active: mode === 'source' }"
              data-testid="artifact-mode-source"
              @click="mode = 'source'"
            >
              {{ t('artifacts.panel.source') }}
            </button>
            <button
              class="detail-btn"
              data-testid="artifact-download"
              @click="download"
            >
              {{ t('artifacts.download') }}
            </button>
            <button
              class="detail-btn"
              data-testid="artifact-share-open"
              @click="shareOpen = true"
            >
              {{ t('artifacts.share.button') }}
            </button>
          </div>
        </div>

        <div v-if="contentLoading" class="rail-state">{{ t('common.loading') }}</div>
        <div v-else-if="contentError" class="rail-state rail-error" data-testid="artifact-content-error">
          {{ t('artifacts.panel.contentFailed') }}
        </div>
        <template v-else>
          <!-- 源码模式：等宽原文（转义输出，绝不 v-html） -->
          <pre v-if="mode === 'source'" class="artifact-source" data-testid="artifact-source">{{ content }}</pre>

          <!-- 预览模式：按 kind 分派 -->
          <template v-else>
            <!-- HTML / SVG：**只**在 sandbox iframe 中渲染（allow-scripts，无
                 allow-same-origin = 不透明源，脚本可跑但拿不到宿主 cookie/DOM）。 -->
            <iframe
              v-if="selected.kind === 'html' || selected.kind === 'svg'"
              class="artifact-frame"
              data-testid="artifact-sandbox-frame"
              sandbox="allow-scripts"
              referrerpolicy="no-referrer"
              :title="selected.title"
              :srcdoc="content"
            />
            <DiffView v-else-if="selected.kind === 'diff'" :text="content" />
            <Markdown v-else-if="selected.kind === 'markdown'" :content="content" />
            <pre v-else-if="selected.kind === 'json'" class="artifact-code" data-testid="artifact-json">{{ prettyJson }}</pre>
            <pre v-else class="artifact-source">{{ content }}</pre>
          </template>
        </template>

        <!-- 版本历史（旧 → 新；点击切到该版本） -->
        <div v-if="versions.length > 1" class="version-list" data-testid="artifact-versions">
          <span class="version-label">{{ t('artifacts.panel.versions') }}</span>
          <button
            v-for="v in versions"
            :key="v.id"
            class="version-chip"
            :class="{ active: v.id === selected.id }"
            :data-testid="`artifact-version-${v.version_no}`"
            @click="selectVersion(v)"
          >
            v{{ v.version_no }}
          </button>
        </div>
      </div>
    </template>

    <!-- 分享对话框（批次九）：展示层覆盖在右栏之上；token 只在创建时可见一次。 -->
    <ArtifactSharePanel
      :show="shareOpen"
      :artifact="selected"
      @update:show="shareOpen = $event"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Markdown from '@/components/common/Markdown.vue'
import DiffView from '@/components/agent/DiffView.vue'
import ArtifactSharePanel from '@/components/agent/ArtifactSharePanel.vue'
import { artifactsApi } from '@/api/artifacts'
import { apiUnwrap } from '@/utils/apiUnwrap'
import type { AgentArtifact } from '@/types/artifact'

const props = defineProps<{
  /** 作用域：按会话或任务过滤列表（二者可都不传 = 当前用户全部）。 */
  conversationId?: string | null
  taskId?: string | null
  /** 新工件事件到达时父级递增它触发刷新（不直接耦合 SSE 事件对象）。 */
  refreshKey?: number
}>()

defineEmits<{ close: [] }>()

const { t } = useI18n()

const items = ref<AgentArtifact[]>([])
const loading = ref(false)
const error = ref(false)
const selected = ref<AgentArtifact | null>(null)
const versions = ref<AgentArtifact[]>([])
const content = ref('')
const contentLoading = ref(false)
const contentError = ref(false)
const mode = ref<'preview' | 'source'>('preview')
const shareOpen = ref(false)

const prettyJson = computed(() => {
  try {
    return JSON.stringify(JSON.parse(content.value), null, 2)
  } catch {
    return content.value
  }
})

function formatSize(bytes: number): string {
  if (!bytes || bytes < 1024) return `${bytes || 0} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

async function loadList() {
  loading.value = true
  error.value = false
  try {
    const res = await artifactsApi.list({
      conversation_id: props.conversationId || undefined,
      task_id: props.taskId || undefined,
      limit: 100,
    })
    const data = apiUnwrap<{ items: AgentArtifact[] }>(res)
    items.value = data?.items || []
    if (selected.value) {
      const still = items.value.find((item) => item.id === selected.value?.id)
      if (still) selected.value = still
    }
  } catch {
    error.value = true
    items.value = []
  } finally {
    loading.value = false
  }
}

async function loadContent(artifact: AgentArtifact) {
  content.value = ''
  contentError.value = false
  contentLoading.value = true
  try {
    const response = await artifactsApi.fetchContent(artifact.id)
    if (!response.ok) {
      contentError.value = true
      return
    }
    content.value = await response.text()
  } catch {
    contentError.value = true
  } finally {
    contentLoading.value = false
  }
}

async function loadVersions(artifact: AgentArtifact) {
  versions.value = [artifact]
  try {
    const res = await artifactsApi.get(artifact.id)
    const detail = apiUnwrap<AgentArtifact>(res)
    if (detail?.versions?.length) versions.value = detail.versions
  } catch {
    // 版本史是增强：拉不到就只显示当前版本（不打断预览）。
  }
}

async function select(artifact: AgentArtifact) {
  selected.value = artifact
  mode.value = 'preview'
  versions.value = [artifact]
  await Promise.all([loadContent(artifact), loadVersions(artifact)])
}

async function selectVersion(v: AgentArtifact) {
  const entry = items.value.find((item) => item.id === v.id) || v
  selected.value = entry
  await loadContent(entry)
}

async function download() {
  if (!selected.value) return
  try {
    const response = await artifactsApi.fetchContent(selected.value.id)
    if (!response.ok) return
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = selected.value.filename || `${selected.value.title}.txt`
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch {
    // 下载失败保持静默：拉取按钮可重试，不弹全局错误。
  }
}

onMounted(loadList)
watch(() => [props.conversationId, props.taskId, props.refreshKey], loadList)
</script>

<style scoped>
.artifact-rail {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-width: 300px;
  gap: 8px;
  padding: 10px;
  background: var(--bg-primary, #fff);
  border-left: 1px solid var(--border-color, #e5e7eb);
  overflow: hidden;
}

.dark .artifact-rail {
  background: var(--bg-primary-dark, #0f172a);
  border-color: var(--border-color-dark, #374151);
}

.rail-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.rail-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary, #111827);
}

.dark .rail-title {
  color: var(--text-primary-dark, #f9fafb);
}

.rail-close {
  background: none;
  border: none;
  font-size: 18px;
  line-height: 1;
  cursor: pointer;
  color: var(--text-tertiary, #9ca3af);
}

.rail-state {
  padding: 12px;
  font-size: 12px;
  color: var(--text-tertiary, #9ca3af);
}

.rail-error {
  color: #dc2626;
}

.rail-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 40%;
  overflow-y: auto;
}

.rail-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 8px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: none;
  cursor: pointer;
  text-align: left;
  color: var(--text-primary, #111827);
}

.rail-item:hover {
  background: var(--bg-tertiary, #f3f4f6);
}

.rail-item.active {
  border-color: #3b82f6;
  background: rgba(59, 130, 246, 0.08);
}

.item-kind {
  font-size: 9px;
  font-weight: 600;
  text-transform: uppercase;
  color: #3b82f6;
  background: rgba(59, 130, 246, 0.12);
  border-radius: 4px;
  padding: 1px 4px;
}

.item-title {
  flex: 1;
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.item-meta {
  font-size: 10px;
  color: var(--text-tertiary, #9ca3af);
  white-space: nowrap;
}

.rail-detail {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: 0;
  overflow-y: auto;
}

.detail-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
}

.detail-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary, #111827);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 55%;
}

.detail-actions {
  display: flex;
  gap: 4px;
}

.detail-btn {
  font-size: 10px;
  padding: 2px 6px;
  border-radius: 4px;
  border: 1px solid var(--border-color, #e5e7eb);
  background: none;
  cursor: pointer;
  color: var(--text-secondary, #4b5563);
}

.detail-btn.active {
  border-color: #3b82f6;
  color: #3b82f6;
}

.artifact-source,
.artifact-code {
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
  background: var(--bg-tertiary, #f3f4f6);
}

.dark .artifact-code {
  background: var(--bg-tertiary-dark, #1f2937);
  color: #e5e7eb;
}

.artifact-frame {
  width: 100%;
  min-height: 320px;
  flex: 1;
  border: 1px solid var(--border-color, #e5e7eb);
  border-radius: 6px;
  background: #fff;
}

.version-list {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
  padding-top: 6px;
  border-top: 1px solid var(--border-color, #e5e7eb);
}

.version-label {
  font-size: 10px;
  color: var(--text-tertiary, #9ca3af);
}

.version-chip {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 10px;
  border: 1px solid var(--border-color, #e5e7eb);
  background: none;
  cursor: pointer;
  color: var(--text-secondary, #4b5563);
}

.version-chip.active {
  border-color: #3b82f6;
  color: #3b82f6;
}
</style>
