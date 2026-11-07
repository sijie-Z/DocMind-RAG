<template>
  <div class="document-page flex h-full min-h-0 flex-col bg-slate-50 dark:bg-gray-950">
    <div class="min-w-0 flex-1 overflow-y-auto">
      <div class="mx-auto w-full max-w-5xl px-4 py-5 sm:px-6 md:py-8 lg:px-8">
        <div class="mb-5 flex flex-wrap items-center justify-between gap-3">
          <n-button quaternary data-testid="document-back" @click="goBack">
            <template #icon><n-icon><ArrowBackOutline /></n-icon></template>
            {{ t('knowledge.documentDetail.back') }}
          </n-button>
          <div class="document-actions flex flex-wrap items-center gap-2">
            <n-button
              v-if="focusFound && !evidenceId"
              secondary
              data-testid="document-clear-focus"
              @click="clearFocus"
            >
              {{ t('knowledge.documentDetail.viewFullText') }}
            </n-button>
            <n-button v-if="!evidenceId" secondary data-testid="document-copy-link" @click="copyLink">
              <template #icon><n-icon><CopyOutline /></n-icon></template>
              {{ t('knowledge.documentDetail.copyLink') }}
            </n-button>
            <n-button
              type="primary"
              secondary
              data-testid="document-download"
              :loading="downloadLoading"
              :disabled="downloadLoading || !detail?.id"
              @click="handleDownload"
            >
              <template #icon><n-icon><DownloadOutline /></n-icon></template>
              {{ downloadLoading ? t('knowledge.documentDetail.downloadPreparing') : t('knowledge.documentDetail.download') }}
            </n-button>
          </div>
        </div>

        <div
          v-if="downloadError"
          data-testid="document-download-error"
          role="alert"
          class="mb-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-300"
        >
          {{ downloadError }}
        </div>

        <div v-if="loading" data-testid="document-loading" aria-busy="true" class="space-y-4">
          <div class="rounded-lg border border-slate-200 bg-white p-5 dark:border-slate-700 dark:bg-slate-900">
            <n-skeleton text style="width: 40%" />
            <n-skeleton class="mt-3" text :repeat="2" />
          </div>
          <div class="rounded-lg border border-slate-200 bg-white p-5 dark:border-slate-700 dark:bg-slate-900">
            <n-skeleton text :repeat="8" />
          </div>
        </div>

        <div
          v-else-if="errorKind && !evidenceDetail"
          data-testid="document-error"
          class="rounded-lg border border-slate-200 bg-white py-10 dark:border-slate-700 dark:bg-slate-900"
        >
          <n-result
            :status="errorKind === 'notFound' ? '404' : errorKind === 'forbidden' ? '403' : '500'"
            :title="errorTitle"
            :description="errorDescription"
          >
            <template #footer>
              <div class="flex flex-wrap justify-center gap-2">
                <n-button @click="goBack">{{ t('knowledge.documentDetail.back') }}</n-button>
                <n-button v-if="errorKind === 'failed'" type="primary" @click="loadDocument">
                  {{ t('common.retry') }}
                </n-button>
              </div>
            </template>
          </n-result>
        </div>

        <template v-else>
          <header class="rounded-lg border border-slate-200 bg-white p-5 dark:border-slate-700 dark:bg-slate-900 md:p-6">
            <div class="flex items-start gap-4">
              <div class="flex h-12 w-12 shrink-0 items-center justify-center rounded-lg bg-slate-50 dark:bg-slate-800">
                <n-icon size="26" :color="docIconColor(detail?.file_type)">
                  <DocumentTextOutline v-if="detail?.file_type === 'pdf'" />
                  <DocumentOutline v-else-if="detail?.file_type === 'word'" />
                  <DocumentAttachOutline v-else />
                </n-icon>
              </div>
              <div class="min-w-0 flex-1">
                <h2
                  data-testid="document-title"
                  class="break-words text-[22px] font-semibold leading-8 text-slate-900 dark:text-slate-100"
                >
                  {{ detail?.title || detail?.filename || evidenceDetail?.document.filename || evidenceDetail?.case.title || t('knowledge.documentDetail.titleFallback') }}
                </h2>
                <p
                  v-if="detail?.filename && detail.filename !== detail.title"
                  class="mt-1 truncate text-sm text-slate-500 dark:text-slate-400"
                >
                  {{ detail.filename }}
                </p>
                <div class="mt-3 flex flex-wrap items-center gap-2">
                  <n-tag v-if="detail" size="small" :type="docStatusType(detail.status)" :bordered="false" round>
                    {{ docStatusLabel(detail.status) }}
                  </n-tag>
                  <n-tag v-if="detail?.file_type" size="small" quaternary round>
                    {{ detail.file_type.toUpperCase() }}
                  </n-tag>
                  <n-tag v-if="detail?.file_size" size="small" quaternary round>
                    {{ formatFileSize(detail.file_size) }}
                  </n-tag>
                  <n-tag v-if="detail?.created_at" size="small" quaternary round>
                    {{ t('knowledge.documentDetail.uploadedAt') }} {{ formatUploadedAt(detail.created_at) }}
                  </n-tag>
                  <n-tag v-if="chunkTotal" size="small" quaternary round>
                    {{ chunkCountLabel }}
                  </n-tag>
                  <n-tag v-if="detail?.upload_source || detail?.upload_source_code" size="small" quaternary round>
                    {{ uploadSourceLabel(detail) }}
                  </n-tag>
                </div>
              </div>
            </div>
            <p v-if="detail?.description" class="mt-4 break-words text-sm leading-6 text-slate-600 dark:text-slate-300">
              {{ detail.description }}
            </p>
            <div v-if="tags.length" class="mt-3 flex flex-wrap gap-2">
              <n-tag v-for="tag in tags" :key="tag" size="small" quaternary round># {{ tag }}</n-tag>
            </div>
            <div
              v-if="contentSummary"
              class="mt-3 rounded-lg border border-slate-100 bg-slate-50/70 px-3 py-2 text-xs leading-5 text-slate-600 dark:border-slate-800 dark:bg-slate-800/40 dark:text-slate-300"
            >
              <span class="font-medium text-slate-700 dark:text-slate-200">{{ t('knowledge.documentDetail.summaryLabel') }}</span>
              {{ contentSummary }}
            </div>
          </header>

          <n-alert
            v-if="detail?.parse_error"
            type="error"
            :bordered="false"
            class="mt-4 break-words"
            data-testid="document-parse-error"
          >
            {{ detail.parse_error }}
          </n-alert>

          <n-alert
            v-if="errorKind && evidenceDetail"
            type="warning"
            :bordered="false"
            class="mt-4"
            data-testid="document-snapshot-only"
          >
            {{ t('knowledge.documentDetail.evidenceDocumentUnavailable') }}
          </n-alert>

          <div
            v-if="evidenceId"
            data-testid="document-evidence-banner"
            role="region"
            aria-labelledby="document-evidence-title"
            class="mt-4 rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900 md:p-5"
          >
            <div v-if="evidenceLoading" class="text-sm text-slate-600 dark:text-slate-300">
              {{ t('knowledge.documentDetail.evidenceLoading') }}
            </div>
            <div v-else-if="evidenceErrorKind" class="flex flex-wrap items-center gap-3">
              <n-alert type="warning" :bordered="false" data-testid="document-evidence-error">
                {{ evidenceErrorText }}
              </n-alert>
              <n-button size="small" secondary data-testid="evidence-retry" @click="loadEvidence">
                {{ t('common.retry') }}
              </n-button>
            </div>
            <template v-else-if="evidenceDetail">
              <div class="flex flex-wrap items-start justify-between gap-3">
                <div class="min-w-0">
                  <div class="flex flex-wrap items-center gap-2">
                    <h3 id="document-evidence-title" class="text-sm font-semibold text-slate-900 dark:text-slate-100">
                      {{ t('knowledge.documentDetail.evidenceSnapshotTitle') }}
                    </h3>
                    <n-tag
                      size="small"
                      :type="evidenceDetail.replay.quote_unchanged ? 'success' : 'warning'"
                      :bordered="false"
                      round
                    >
                      {{ evidenceDetail.replay.quote_unchanged
                        ? t('knowledge.documentDetail.evidenceCurrentMatch')
                        : t('knowledge.documentDetail.evidenceStaleBadge') }}
                    </n-tag>
                    <n-tag v-if="evidenceDetail.evidence.doc_version_label" size="small" quaternary round>
                      {{ t('knowledge.documentDetail.evidenceVersion', { label: evidenceDetail.evidence.doc_version_label }) }}
                    </n-tag>
                    <n-tag v-if="evidenceDetail.evidence.page_number" size="small" quaternary round>
                      {{ t('knowledge.documentDetail.evidencePage', { page: evidenceDetail.evidence.page_number }) }}
                    </n-tag>
                  </div>
                  <p class="mt-1 break-words text-xs text-slate-500 dark:text-slate-400">
                    {{ t('knowledge.documentDetail.evidenceCaseLabel') }}{{ t('knowledge.documentDetail.evidenceLabelSeparator') }}{{ evidenceDetail.case.title }}
                    · {{ t('knowledge.documentDetail.evidenceCapturedAt') }}
                    {{ formatUploadedAt(evidenceDetail.evidence.captured_at) }}
                  </p>
                </div>
                <div class="flex flex-wrap gap-2">
                  <n-button
                    size="small"
                    secondary
                    data-testid="evidence-back-to-case"
                    @click="router.push({ name: 'ReviewDetail', params: { id: evidenceDetail.case.id } })"
                  >
                    {{ t('knowledge.documentDetail.evidenceBackToCase') }}
                  </n-button>
                  <n-button size="small" secondary data-testid="evidence-copy-link" @click="copyLink">
                    {{ t('knowledge.documentDetail.evidenceCopyLink') }}
                  </n-button>
                  <n-button v-if="!errorKind" size="small" tertiary @click="clearFocus">
                    {{ t('knowledge.documentDetail.viewFullText') }}
                  </n-button>
                </div>
              </div>
              <div
                data-testid="document-evidence-quote"
                class="mt-3 max-h-64 overflow-y-auto whitespace-pre-wrap break-words rounded-md border border-slate-100 bg-slate-50/60 p-3 text-sm leading-6 text-slate-700 dark:border-slate-800 dark:bg-slate-800/40 dark:text-slate-200"
              >
                {{ evidenceDetail.evidence.quote }}
              </div>
              <ul v-if="evidenceWarnings.length" class="mt-2 space-y-0.5 text-xs text-amber-700 dark:text-amber-400">
                <li v-for="warning in evidenceWarnings" :key="warning">· {{ warning }}</li>
              </ul>
              <details
                v-if="evidenceDetail.live_chunk.found && evidenceDetail.live_chunk.text && !evidenceDetail.live_chunk.matches_snapshot"
                class="mt-3"
              >
                <summary class="cursor-pointer text-xs font-medium text-slate-600 dark:text-slate-300">
                  {{ t('knowledge.documentDetail.evidenceCompareCurrent') }}
                </summary>
                <div class="mt-2 whitespace-pre-wrap break-words rounded-md border border-amber-200 bg-amber-50/50 p-3 text-xs leading-5 text-slate-700 dark:border-amber-800/50 dark:bg-amber-900/10 dark:text-slate-200">
                  {{ evidenceDetail.live_chunk.text }}
                </div>
              </details>
            </template>
          </div>

          <div
            v-if="chunkRequested"
            data-testid="document-focus-banner"
            role="status"
            aria-live="polite"
            class="mt-4 flex flex-wrap items-start gap-3 rounded-lg border border-slate-200 bg-white px-4 py-3 dark:border-slate-700 dark:bg-slate-900"
          >
            <template v-if="focusFound">
              <n-icon size="18" class="mt-1 shrink-0 text-blue-600 dark:text-blue-400"><LocateOutline /></n-icon>
              <div class="min-w-0 flex-1">
                <p class="text-sm font-medium text-slate-800 dark:text-slate-100">
                  {{ t('knowledge.documentDetail.focusFound') }}
                </p>
                <p v-if="focusMeta" class="mt-1 break-words text-xs text-slate-500 dark:text-slate-400">
                  {{ focusMeta }}
                </p>
              </div>
            </template>
            <div v-else class="flex flex-wrap items-center gap-2">
              <p
                data-testid="document-focus-miss"
                class="text-sm"
                :class="chunkLoading || chunkPending
                  ? 'text-slate-600 dark:text-slate-300'
                  : 'text-amber-700 dark:text-amber-400'"
              >
                {{ focusMissText }}
              </p>
              <n-button
                v-if="chunkErrorKind === 'failed' || chunkRetrying"
                size="small"
                secondary
                data-testid="document-chunk-retry"
                :loading="chunkLoading"
                @click="retryChunk"
              >
                {{ t('common.retry') }}
              </n-button>
            </div>
          </div>

          <n-alert
            v-if="content && isUntrustedProvenance"
            type="warning"
            :bordered="false"
            class="mt-4"
            data-testid="document-provenance"
          >
            {{ t('knowledge.previewOriginalUnavailable') }}
          </n-alert>
          <n-alert
            v-if="truncated"
            type="info"
            :bordered="false"
            class="mt-4"
            data-testid="document-truncated"
          >
            {{ t('knowledge.previewTruncated') }}
          </n-alert>

          <section v-if="!errorKind" class="mt-4 overflow-hidden rounded-lg border border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-900">
            <div class="flex items-center justify-between gap-3 border-b border-slate-100 px-5 py-3 dark:border-slate-800">
              <h3 class="text-sm font-semibold text-slate-800 dark:text-slate-100">
                {{ t('knowledge.documentDetail.contentTitle') }}
              </h3>
              <span v-if="content" class="shrink-0 text-xs tabular-nums text-slate-500 dark:text-slate-400">
                {{ t('knowledge.documentDetail.charCount', { count: content.length }) }}
              </span>
            </div>
            <div
              v-if="content"
              ref="contentEl"
              class="document-content whitespace-pre-wrap break-words p-5 text-sm leading-7 text-slate-700 dark:text-slate-200 md:p-8"
            >
              <template v-for="(segment, index) in segments" :key="index">
                <mark
                  v-if="segment.focus"
                  data-testid="document-focus"
                  class="rounded-sm bg-amber-200/70 px-0.5 text-inherit dark:bg-amber-400/30"
                >{{ segment.text }}</mark>
                <template v-else>{{ segment.text }}</template>
              </template>
            </div>
            <div v-else class="p-8">
              <n-empty :description="emptyContentHint" />
            </div>
          </section>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { NAlert, NButton, NEmpty, NIcon, NResult, NSkeleton, NTag } from 'naive-ui'
import {
  ArrowBackOutline,
  CopyOutline,
  DocumentAttachOutline,
  DocumentOutline,
  DocumentTextOutline,
  DownloadOutline,
  LocateOutline
} from '@vicons/ionicons5'
import {
  getDocumentChunk,
  getDocumentContent,
  getDocumentDetail,
  getDocumentDownloadUrl
} from '@/api/knowledge'
import type { DocumentChunkPreview, DocumentContent, DocumentDetail } from '@/api/knowledge'
import { reviewsApi } from '@/api/reviews'
import type { ReviewEvidenceDetail } from '@/api/reviews'
import { apiUnwrap } from '@/utils/apiUnwrap'
import { getErrorMessage } from '@/utils/apiError'
import { formatDateTime, formatFileSize } from '@/utils/format'
import { splitFocusText } from '@/utils/focusText'
import { setPageTitleKey, setPageTitleText } from '@/locales'
import { useDedupedMessage } from '@/utils/message'
import { parseTrustedDownloadUrl } from '@/views/chat/utils/downloadUrl'
import { docIconColor, docStatusLabel, docStatusType } from './docDisplay'

type ErrorKind = 'notFound' | 'forbidden' | 'failed'

const route = useRoute()
const router = useRouter()
const { t, locale } = useI18n()
const message = useDedupedMessage()

const formatUploadedAt = (value?: string | null): string => formatDateTime(value, locale.value)

// 优先使用后端稳定 code；旧响应没有 code 时退回原始展示文案。
const uploadSourceLabel = (doc: DocumentDetail): string => {
  if (doc.upload_source_code === 'chat') return t('knowledge.documentDetail.uploadSourceChat')
  if (doc.upload_source_code === 'knowledge') return t('knowledge.documentDetail.uploadSourceKnowledge')
  return doc.upload_source || ''
}

const documentId = computed(() => (typeof route.params.documentId === 'string' ? route.params.documentId : ''))
const queryString = (value: unknown): string =>
  typeof value === 'string' ? value : Array.isArray(value) && typeof value[0] === 'string' ? value[0] : ''
const chunkId = computed(() => queryString(route.query.chunk))
const evidenceId = computed(() => queryString(route.query.evidence))

const loading = ref(false)
const detail = ref<DocumentDetail | null>(null)
// 已成功加载的文档 id：分块请求只允许落在当前文档上，避免路由切换时
// 用旧文档状态去取新文档的分块（watcher 执行顺序不可依赖）。
const loadedDocumentId = ref('')
const content = ref('')
const provenance = ref<DocumentContent['text_provenance'] | undefined>(undefined)
const truncated = ref(false)
const contentSummary = ref('')
const suggestedTags = ref<string[]>([])
const chunkTotal = ref<number | null>(null)
const chunk = ref<DocumentChunkPreview | null>(null)
const chunkLoading = ref(false)
// 分块失败类型：401/403 无权；其余 4xx（除 408/429）不存在；网络/5xx 可重试。
const chunkErrorKind = ref<'missing' | 'forbidden' | 'failed' | null>(null)
// 手动重试期间保持按钮挂载（loading 态），避免点击后焦点丢失。
const chunkRetrying = ref(false)
// 已完成的 chunk 请求键：路由切到同一文档的新 chunk 时，旧高亮/旧「已定位」
// 不得在新请求在途期间继续展示或播报。
const chunkLoadedKey = ref('')
const errorKind = ref<ErrorKind | null>(null)
const errorMessage = ref('')

// 证据永久链接：快照为主体；当前 chunk 只作为可选定位增强。
const evidenceDetail = ref<ReviewEvidenceDetail | null>(null)
const evidenceLoading = ref(false)
const evidenceErrorKind = ref<'notFound' | 'forbidden' | 'failed' | null>(null)
let evidenceSeq = 0

const downloadLoading = ref(false)
const downloadError = ref('')

// 路由切换（浏览器前进/后退、站内再次跳转）必须整页重取，不能只读 onMounted。
let documentSeq = 0
let chunkSeq = 0
// documentId 与 chunk 同时变化时，两个 watcher 会各触发一次取块；同一
// (文档, 分块) 的在途请求只保留一个，避免重复请求与旧响应覆盖。
let inFlightChunkKey = ''

const evidenceChunkId = computed(() => {
  const evidence = evidenceDetail.value
  if (!evidence) return ''
  return evidence.replay.can_highlight_current_chunk
    ? evidence.evidence.chunk_id_at_capture
    : ''
})
// 证据优先：stale 时绝不把当前文本当证据高亮；没有 evidence 时沿用 ?chunk=。
const effectiveChunkId = computed(() =>
  evidenceDetail.value ? evidenceChunkId.value : chunkId.value
)

const chunkRequested = computed(() => effectiveChunkId.value.length > 0)
const currentChunkKey = computed(() => `${documentId.value}|${effectiveChunkId.value}`)
const chunkPending = computed(
  () => chunkRequested.value && chunkLoadedKey.value !== currentChunkKey.value
)
const segments = computed(() =>
  splitFocusText(content.value, chunkPending.value ? '' : (chunk.value?.text || ''))
)
const focusFound = computed(
  () =>
    chunkRequested.value &&
    !chunkPending.value &&
    segments.value.some((segment) => segment.focus)
)
const focusMeta = computed(() => {
  const parts: string[] = []
  if (chunk.value?.section_title) parts.push(chunk.value.section_title)
  if (chunk.value?.page_number != null) parts.push(t('knowledge.documentDetail.pageMeta', { page: chunk.value.page_number }))
  if (chunk.value?.chunk_index != null && chunk.value.chunk_index >= 0) {
    parts.push(t('knowledge.documentDetail.chunkMeta', { index: chunk.value.chunk_index + 1 }))
  }
  return parts.join(' · ')
})

const focusMissText = computed(() => {
  if (chunkLoading.value || chunkPending.value) return t('knowledge.documentDetail.focusLoading')
  if (chunkErrorKind.value === 'failed') return t('knowledge.documentDetail.chunkLoadFailed')
  if (chunkErrorKind.value === 'forbidden') return t('knowledge.documentDetail.chunkForbidden')
  if (!content.value) return t('knowledge.documentDetail.focusMissEmpty')
  if (truncated.value) return t('knowledge.documentDetail.focusMissTruncated')
  return t('knowledge.documentDetail.focusMiss')
})

const tags = computed(() => {
  const keywords = detail.value?.keywords || []
  return keywords.length > 0 ? keywords : suggestedTags.value
})

const EVIDENCE_WARNING_KEYS: Record<string, string> = {
  chunk_missing: 'knowledge.documentDetail.evidenceWarningChunkMissing',
  chunk_text_empty: 'knowledge.documentDetail.evidenceWarningChunkEmpty',
  chunk_text_changed: 'knowledge.documentDetail.evidenceWarningChunkChanged',
  document_missing: 'knowledge.documentDetail.evidenceWarningDocumentMissing',
  document_hash_changed: 'knowledge.documentDetail.evidenceWarningHashChanged',
  document_version_changed: 'knowledge.documentDetail.evidenceWarningVersionChanged',
  document_identity_changed: 'knowledge.documentDetail.evidenceWarningIdentityChanged'
}
const evidenceWarnings = computed(() =>
  (evidenceDetail.value?.replay.warnings || []).map((code) =>
    EVIDENCE_WARNING_KEYS[code] ? t(EVIDENCE_WARNING_KEYS[code]) : code
  )
)
const evidenceErrorText = computed(() => {
  if (evidenceErrorKind.value === 'notFound') return t('knowledge.documentDetail.evidenceNotFound')
  if (evidenceErrorKind.value === 'forbidden') return t('knowledge.documentDetail.evidenceForbidden')
  return t('knowledge.documentDetail.evidenceLoadFailed')
})

// 英文/法文按单复数选择词形；zh/ja 只有单一形态。
const chunkCountLabel = computed(() => {
  const count = chunkTotal.value
  if (!count) return ''
  return t('knowledge.documentDetail.chunkCount', { count }, count)
})

const isUntrustedProvenance = computed(() => {
  const value = provenance.value
  return value !== 'parser_original' && value !== 'none'
})

const errorTitle = computed(() => {
  if (errorKind.value === 'notFound') return t('knowledge.documentDetail.notFoundTitle')
  if (errorKind.value === 'forbidden') return t('knowledge.documentDetail.forbiddenTitle')
  return t('knowledge.documentDetail.loadFailedTitle')
})
const errorDescription = computed(() => {
  if (errorKind.value === 'notFound') return t('knowledge.documentDetail.notFoundDesc')
  if (errorKind.value === 'forbidden') return t('knowledge.documentDetail.forbiddenDesc')
  return errorMessage.value || t('knowledge.documentDetail.loadFailedDesc')
})

const emptyContentHint = computed(() => {
  if (detail.value?.status === 'failed') return t('knowledge.documentDetail.contentFailed')
  if (detail.value && ['pending', 'uploaded', 'parsing', 'parsed'].includes(detail.value.status)) {
    return t('knowledge.documentDetail.contentPending')
  }
  return t('chat.noContent')
})

const contentEl = ref<HTMLElement | null>(null)

const loadChunk = async (manual = false) => {
  const rawId = effectiveChunkId.value
  const docId = documentId.value
  const key = `${docId}|${rawId}`
  if (!rawId) {
    chunkSeq += 1
    chunk.value = null
    chunkLoading.value = false
    chunkLoadedKey.value = key
    chunkErrorKind.value = null
    chunkRetrying.value = false
    return
  }
  // 文档尚未就绪时由 loadDocument 末尾统一取块，避免双请求。
  if (!docId || loadedDocumentId.value !== docId) return
  // 去重必须在推进 seq 之前：否则被去重的调用会作废它本想复用的在途请求。
  if (inFlightChunkKey === key) return
  const seq = ++chunkSeq
  chunkLoadedKey.value = ''
  chunkErrorKind.value = null
  inFlightChunkKey = key
  chunkLoading.value = true
  try {
    const res = await getDocumentChunk(docId, rawId, {
      silent: true,
      ...(manual ? { maxRetries: 1 } : {})
    })
    if (seq !== chunkSeq) return
    const data = apiUnwrap<DocumentChunkPreview>(res)
    chunk.value = data?.text ? data : null
    chunkLoadedKey.value = key
  } catch (error) {
    if (seq !== chunkSeq) return
    chunk.value = null
    chunkLoadedKey.value = key
    const status = (error as { response?: { status?: number } })?.response?.status
    if (status === 401 || status === 403) {
      chunkErrorKind.value = 'forbidden'
    } else {
      const nonRetryable =
        typeof status === 'number' && status >= 400 && status < 500 && status !== 408 && status !== 429
      chunkErrorKind.value = nonRetryable ? 'missing' : 'failed'
    }
  } finally {
    if (inFlightChunkKey === key) inFlightChunkKey = ''
    if (seq === chunkSeq) chunkLoading.value = false
    chunkRetrying.value = false
  }
}

const retryChunk = () => {
  chunkRetrying.value = true
  void loadChunk(true)
}

const loadEvidence = async () => {
  const id = evidenceId.value
  const seq = ++evidenceSeq
  evidenceDetail.value = null
  evidenceErrorKind.value = null
  if (!id) {
    evidenceLoading.value = false
    return
  }
  evidenceLoading.value = true
  try {
    const res = await reviewsApi.getEvidence(id)
    if (seq !== evidenceSeq) return
    const data = apiUnwrap<ReviewEvidenceDetail>(res)
    if (!data?.evidence) throw new Error('invalid evidence payload')
    evidenceDetail.value = data
    // 链接中的 documentId 与证据捕获文档不一致：纠正到权威文档并保留证据 id。
    if (data.evidence.document_id !== documentId.value) {
      await router.replace({
        name: 'KnowledgeDocument',
        params: { documentId: data.evidence.document_id },
        query: { evidence: id }
      })
    }
  } catch (error) {
    if (seq !== evidenceSeq) return
    const status = (error as { response?: { status?: number } })?.response?.status
    evidenceErrorKind.value =
      status === 404 ? 'notFound' : status === 403 ? 'forbidden' : 'failed'
  } finally {
    if (seq === evidenceSeq) evidenceLoading.value = false
  }
}

const loadDocument = async () => {
  const docId = documentId.value
  const seq = ++documentSeq
  chunkSeq += 1
  chunk.value = null
  chunkLoading.value = false
  chunkLoadedKey.value = ''
  chunkErrorKind.value = null
  chunkRetrying.value = false
  // 旧文档的在途 chunk 请求必须失效；清空键让新文档可以重新发起请求
  // （A→B→A 快速往返时不能因旧键仍占用而被永久跳过）。
  inFlightChunkKey = ''
  loadedDocumentId.value = ''
  errorKind.value = null
  errorMessage.value = ''
  // 异步详情返回前先落路由级标题；失败时不会残留上一篇文档名。
  setPageTitleKey('router.knowledgeDocument')

  if (!docId) {
    loading.value = false
    detail.value = null
    chunkTotal.value = null
    errorKind.value = 'notFound'
    return
  }

  loading.value = true
  try {
    const [detailRes, contentRes] = await Promise.all([
      getDocumentDetail(docId, { silent: true }),
      getDocumentContent(docId, { silent: true })
    ])
    if (seq !== documentSeq) return
    detail.value = apiUnwrap<DocumentDetail>(detailRes) || null
    loadedDocumentId.value = docId
    setPageTitleText(
      detail.value?.title || detail.value?.filename || t('knowledge.documentDetail.titleFallback')
    )
    const contentData = apiUnwrap<DocumentContent>(contentRes) || {}
    content.value = contentData.content || ''
    provenance.value = contentData.text_provenance
    truncated.value = contentData.truncated === true
    contentSummary.value = contentData.summary || ''
    suggestedTags.value = contentData.suggested_tags || []
    chunkTotal.value = contentData.total_chunks ?? detail.value?.chunk_count ?? null
    loading.value = false
    void loadChunk()
  } catch (error) {
    if (seq !== documentSeq) return
    const status = (error as { response?: { status?: number } })?.response?.status
    errorKind.value = status === 404 ? 'notFound' : status === 403 ? 'forbidden' : 'failed'
    errorMessage.value = status === 404 || status === 403 ? '' : getErrorMessage(error, '')
  } finally {
    if (seq === documentSeq) loading.value = false
  }
}

watch(documentId, () => { void loadDocument() }, { immediate: true })
watch(evidenceId, () => { void loadEvidence() }, { immediate: true })
watch(effectiveChunkId, () => {
  // 文档切换尚未完成时由 loadDocument 统一取块；这里只处理同文档内换 chunk。
  if (loading.value || loadedDocumentId.value !== documentId.value) return
  void loadChunk()
})
// 标题回退词条跟随语言；文件名本身不翻译。
watch(locale, () => {
  if (!detail.value) return
  setPageTitleText(
    detail.value.title || detail.value.filename || t('knowledge.documentDetail.titleFallback')
  )
})

// 直接卸载（非路由切换）时让在途详情/分块响应失效，避免回写标题或状态。
onBeforeUnmount(() => {
  documentSeq += 1
  chunkSeq += 1
  evidenceSeq += 1
})

watch(
  [() => content.value, () => chunk.value, () => loading.value, () => focusFound.value],
  async () => {
    if (!focusFound.value) return
    // 证据模式以快照为主体：首屏不滚动，避免把证据横幅推出视口。
    if (evidenceId.value) return
    await nextTick()
    const reduceMotion =
      typeof window !== 'undefined' &&
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
    contentEl.value
      ?.querySelector<HTMLElement>('[data-testid="document-focus"]')
      ?.scrollIntoView({ block: 'center', behavior: reduceMotion ? 'auto' : 'smooth' })
  },
  { flush: 'post' }
)

const goBack = () => router.push({ name: 'Knowledge' })

const clearFocus = () => {
  const query = Object.fromEntries(
    Object.entries(route.query).filter(([key]) => key !== 'chunk' && key !== 'evidence')
  )
  void router.replace({ name: 'KnowledgeDocument', params: { documentId: documentId.value }, query })
}

const copyLink = async () => {
  try {
    // 有证据时复制永久证据链接；否则复制当前分块深链。
    const query = evidenceId.value
      ? { evidence: evidenceId.value }
      : chunkId.value
        ? { chunk: chunkId.value }
        : {}
    const href = router.resolve({
      name: 'KnowledgeDocument',
      params: { documentId: documentId.value },
      query
    }).href
    await navigator.clipboard.writeText(new URL(href, window.location.origin).href)
    message.success(t('knowledge.documentDetail.linkCopied'))
  } catch {
    message.error(t('knowledge.documentDetail.linkCopyFailed'))
  }
}

const handleDownload = async () => {
  const fileId = detail.value?.id
  if (!fileId || downloadLoading.value) return
  downloadLoading.value = true
  downloadError.value = ''
  try {
    const res = await getDocumentDownloadUrl(fileId, { silent: true })
    const payload = apiUnwrap<{ downloadUrl?: string; expiresIn?: number }>(res)
    if (!payload?.downloadUrl || !Number.isFinite(payload.expiresIn) || (payload.expiresIn as number) <= 0) {
      throw new Error('invalid download URL response')
    }
    const trustedUrl = parseTrustedDownloadUrl(payload.downloadUrl)
    if (!trustedUrl) throw new Error('untrusted download URL')
    // 只打开通过校验的原始预签名 URL，不追加 JWT，也不记录完整签名 URL。
    window.open(trustedUrl, '_blank', 'noopener,noreferrer')
  } catch {
    // 内部校验失败（相对路径/不可信 URL）不得把英文技术串直接上屏。
    downloadError.value = t('knowledge.documentDetail.downloadFailed')
  } finally {
    downloadLoading.value = false
  }
}
</script>

<style scoped>
.document-content {
  text-wrap: pretty;
}
@media (max-width: 1024px) {
  .document-page :deep(.n-button) {
    min-height: 44px;
  }
}
</style>
