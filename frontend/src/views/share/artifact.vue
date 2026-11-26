<template>
  <div class="public-artifact-page">
    <header class="public-artifact-header">
      <div class="public-artifact-brand">
        <DocMindMark :size="18" />
      </div>
      <div class="public-artifact-heading">
        <h1 class="public-artifact-title">{{ meta?.title || t('artifacts.public.title') }}</h1>
        <p class="public-artifact-notice">{{ t('artifacts.public.notice') }}</p>
      </div>
      <button
        v-if="meta"
        class="public-artifact-download"
        data-testid="public-artifact-download"
        @click="download"
      >
        {{ t('artifacts.public.download') }}
      </button>
    </header>

    <main class="public-artifact-main">
      <div v-if="loading" class="public-artifact-state" role="status" data-testid="public-artifact-loading">
        {{ t('artifacts.public.loading') }}
      </div>
      <div
        v-else-if="error"
        class="public-artifact-error"
        role="alert"
        data-testid="public-artifact-unavailable"
      >
        {{ t('artifacts.public.unavailable') }}
      </div>
      <template v-else-if="meta">
        <div class="public-artifact-meta">
          <span class="public-artifact-kind" data-testid="public-artifact-kind">{{ meta.kind }}</span>
          <span>v{{ meta.version_no }}</span>
          <span v-if="meta.expires_at" data-testid="public-artifact-expires-at">
            {{ t('artifacts.public.expiresAt', { time: formatTime(meta.expires_at) }) }}
          </span>
        </div>

        <!-- HTML / SVG 只在不透明源沙箱中渲染（allow-scripts、无 allow-same-origin） -->
        <iframe
          v-if="meta.kind === 'html' || meta.kind === 'svg'"
          class="public-artifact-frame"
          data-testid="public-artifact-sandbox-frame"
          sandbox="allow-scripts"
          referrerpolicy="no-referrer"
          :title="meta.title"
          :srcdoc="content"
        />
        <DiffView v-else-if="meta.kind === 'diff'" :text="content" />
        <Markdown v-else-if="meta.kind === 'markdown'" :content="content" />
        <pre v-else-if="meta.kind === 'json'" class="public-artifact-code" data-testid="public-artifact-json">{{ prettyJson }}</pre>
        <pre v-else class="public-artifact-code">{{ content }}</pre>
      </template>
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import Markdown from '@/components/common/Markdown.vue'
import DiffView from '@/components/agent/DiffView.vue'
import DocMindMark from '@/components/brand/DocMindMark.vue'
import { publicArtifactsApi } from '@/api/artifacts'
import type { PublicArtifactMeta } from '@/types/artifact'

const route = useRoute()
const { t } = useI18n()

const meta = ref<PublicArtifactMeta | null>(null)
const content = ref('')
const loading = ref(true)
const error = ref(false)

const prettyJson = computed(() => {
  try {
    return JSON.stringify(JSON.parse(content.value), null, 2)
  } catch {
    return content.value
  }
})

function formatTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

async function load(token: string) {
  loading.value = true
  error.value = false
  meta.value = null
  content.value = ''
  try {
    const res = await publicArtifactsApi.get(token)
    const data = res.data?.data
    if (!res.data?.success || !data) throw new Error('unavailable')
    // 只使用公开白名单字段；任何额外内部字段不读取、不渲染。
    meta.value = data
    const response = await publicArtifactsApi.fetchContent(token)
    if (!response.ok) throw new Error('unavailable')
    content.value = await response.text()
  } catch {
    meta.value = null
    content.value = ''
    error.value = true
  } finally {
    loading.value = false
  }
}

// SPA 内从一个分享链接导航到另一个（组件复用）时也必须重新拉取，避免展示旧内容。
watch(
  () => String(route.params.token || ''),
  (token) => {
    if (token) void load(token)
  },
  { immediate: true },
)

function download() {
  if (!meta.value) return
  const blob = new Blob([content.value], { type: meta.value.mime_type || 'text/plain' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = meta.value.filename || 'artifact'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
</script>

<style scoped>
.public-artifact-page {
  min-height: 100vh;
  background: #f9fafb;
  color: #111827;
}

.dark .public-artifact-page {
  background: #030712;
  color: #f9fafb;
}

.public-artifact-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
  border-bottom: 1px solid #e5e7eb;
  background: #fff;
}

.dark .public-artifact-header {
  border-color: #1f2937;
  background: #111827;
}

.public-artifact-brand {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 6px;
  border-radius: 8px;
  background: #1d4ed8;
  color: #fff;
}

.public-artifact-heading {
  flex: 1;
  min-width: 0;
}

.public-artifact-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.public-artifact-notice {
  margin: 2px 0 0;
  font-size: 12px;
  color: #6b7280;
}

.dark .public-artifact-notice {
  color: #9ca3af;
}

.public-artifact-download {
  padding: 4px 10px;
  border: 1px solid #2563eb;
  border-radius: 6px;
  background: none;
  color: #2563eb;
  font-size: 12px;
  cursor: pointer;
}

.public-artifact-main {
  max-width: 860px;
  margin: 0 auto;
  padding: 20px 16px 40px;
}

.public-artifact-state {
  font-size: 13px;
  color: #6b7280;
}

.public-artifact-error {
  padding: 12px 14px;
  border: 1px solid #fecaca;
  border-radius: 8px;
  background: #fef2f2;
  color: #b91c1c;
  font-size: 13px;
}

.dark .public-artifact-error {
  border-color: #7f1d1d;
  background: rgba(127, 29, 29, 0.25);
  color: #fca5a5;
}

.public-artifact-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  margin-bottom: 12px;
  font-size: 11px;
  color: #6b7280;
}

.public-artifact-kind {
  padding: 1px 6px;
  border-radius: 4px;
  background: #e5e7eb;
  color: #374151;
  font-weight: 600;
  text-transform: uppercase;
}

.dark .public-artifact-kind {
  background: #374151;
  color: #e5e7eb;
}

.public-artifact-frame {
  width: 100%;
  min-height: 420px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fff;
}

.dark .public-artifact-frame {
  border-color: #374151;
}

.public-artifact-code {
  margin: 0;
  padding: 12px;
  border-radius: 8px;
  background: #f3f4f6;
  font-family: 'Cascadia Code', 'Fira Code', 'JetBrains Mono', monospace;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-x: auto;
}

.dark .public-artifact-code {
  background: #1f2937;
  color: #e5e7eb;
}
</style>
