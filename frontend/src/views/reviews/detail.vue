<template>
  <div class="min-h-screen bg-gradient-to-br from-slate-50 via-white to-blue-50 dark:from-gray-950 dark:via-gray-900 dark:to-slate-950 p-6">
    <div class="max-w-7xl mx-auto">
      <div class="mb-6 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div class="flex items-center gap-3">
          <n-button quaternary circle @click="router.push('/reviews')">
            <template #icon><n-icon><ArrowBackOutline /></n-icon></template>
          </n-button>
          <div>
            <h1 class="text-2xl font-bold text-gray-800 dark:text-white">
              {{ detail?.title || t('menu.reviews') }}
            </h1>
            <div class="mt-1 flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400">
              <n-tag v-if="detail" :type="statusTagType[detail.status]" size="small">
                {{ statusLabel[detail.status] }}
              </n-tag>
              <span v-if="detail">{{ detail.checklist_version }}</span>
            </div>
          </div>
        </div>
        <div class="flex flex-wrap gap-2">
          <n-button :loading="loading" quaternary @click="load">
            <template #icon><n-icon><RefreshOutline /></n-icon></template>
            {{ t('reviews.retry') }}
          </n-button>
          <n-button
            type="primary"
            :loading="analyzing"
            :disabled="!canAnalyze"
            @click="runAnalyze"
          >
            {{ analyzing ? t('reviews.analyzing') : t('reviews.analyze') }}
          </n-button>
          <n-button
            type="primary"
            secondary
            :loading="exporting"
            :disabled="!canExport"
            @click="runExport"
          >
            {{ exporting ? t('reviews.exporting') : t('reviews.export') }}
          </n-button>
        </div>
      </div>

      <n-alert v-if="loadError" type="error" class="mb-4" :bordered="false">
        {{ t('reviews.detailLoadFailed') }}
      </n-alert>

      <n-spin :show="loading">
        <template v-if="detail">
          <n-alert v-if="detail.status === 'blocked'" type="error" class="mb-3" :bordered="false">
            {{ t('reviews.blockedHint') }}
            <div class="mt-1 text-xs">{{ detail.blocking_reasons.join(' · ') }}</div>
          </n-alert>
          <n-alert v-if="detail.live_source_stale" type="warning" class="mb-3" :bordered="false">
            {{ t('reviews.liveSourceStale') }}
          </n-alert>
          <n-alert v-if="detail.status !== 'blocked' && !analyzed" type="info" class="mb-3" :bordered="false">
            {{ t('reviews.notAnalyzedHint') }}
          </n-alert>

          <!-- 文档身份与版本 -->
          <div class="mb-6 grid gap-3 md:grid-cols-3">
            <div
              v-for="side in SIDES"
              :key="side"
              class="rounded-xl border border-gray-200 bg-white/80 p-4 dark:border-gray-800 dark:bg-gray-900/60"
            >
              <div class="flex items-center justify-between">
                <div class="text-sm font-semibold text-gray-700 dark:text-gray-200">
                  {{ t(`reviews.doc${sideCap(side)}`) }}
                </div>
                <n-tag
                  v-if="docOf(side)?.readiness"
                  :type="docOf(side)?.readiness?.status === 'reviewable' ? 'success' : 'error'"
                  size="small"
                >
                  {{
                    docOf(side)?.readiness?.status === 'reviewable'
                      ? t('reviews.readinessReviewable')
                      : t('reviews.readinessBlocked')
                  }}
                </n-tag>
              </div>
              <div class="mt-2 text-sm text-gray-600 dark:text-gray-300">
                {{ docOf(side)?.filename || '—' }}
              </div>
              <div class="mt-1 text-xs text-gray-500 dark:text-gray-400">
                {{ t('reviews.versionLabel') }}:
                {{ docOf(side)?.version_label || '—' }}
                <span v-if="docOf(side)?.version_seq">#{{ docOf(side)?.version_seq }}</span>
              </div>
              <div class="mt-1 break-all text-xs text-gray-400">
                {{ t('reviews.contentSha') }}: {{ (docOf(side)?.content_sha256 || '—').slice(0, 16) }}
              </div>
              <div class="mt-1 flex flex-wrap gap-1">
                <n-tag v-if="docOf(side)?.missing" type="error" size="small">
                  {{ t('reviews.documentMissing') }}
                </n-tag>
                <n-tag v-if="docOf(side)?.identity_changed" type="warning" size="small">
                  {{ t('reviews.identityChanged') }}
                </n-tag>
              </div>
            </div>
          </div>

          <!-- 核验项 -->
          <div class="space-y-4">
            <div
              v-for="item in detail.items"
              :key="item.id"
              class="rounded-xl border border-gray-200 bg-white/80 p-4 dark:border-gray-800 dark:bg-gray-900/60"
              :data-testid="`review-item-${item.item_key}`"
            >
              <div class="flex flex-wrap items-center gap-2">
                <span class="text-base font-semibold text-gray-800 dark:text-gray-100">
                  {{ t(`reviews.item${itemCap(item.item_key)}`) }}
                </span>
                <n-tag v-if="item.change_type" :type="changeTagType[item.change_type]" size="small">
                  {{ changeLabel[item.change_type] }}
                </n-tag>
                <n-tag v-else size="small">{{ t('reviews.changeNotAnalyzed') }}</n-tag>
                <n-tag :type="itemStatusTagType[item.status]" size="small">
                  {{ itemStatusLabel[item.status] }}
                </n-tag>
              </div>

              <!-- 文本事实 -->
              <div class="mt-2 space-y-1 text-xs text-gray-600 dark:text-gray-300">
                <div class="font-medium text-gray-500">{{ t('reviews.factsTitle') }}</div>
                <div v-for="line in factLines(item)" :key="line.side">
                  <span class="text-gray-400">{{ t(`reviews.doc${sideCap(line.side)}`) }}:</span>
                  {{ line.text }}
                </div>
              </div>

              <!-- 证据快照 -->
              <div class="mt-3">
                <div class="mb-1 text-xs font-medium text-gray-500">{{ t('reviews.evidenceTitle') }}</div>
                <div class="grid gap-3 md:grid-cols-3">
                  <div v-for="side in SIDES" :key="side" class="rounded-lg bg-gray-50 p-2 dark:bg-gray-800/50">
                    <div class="mb-1 text-xs font-medium text-gray-500">
                      {{ t(`reviews.doc${sideCap(side)}`) }}
                    </div>
                    <template v-if="(item.evidence[side] || []).length">
                      <div
                        v-for="row in item.evidence[side]"
                        :key="row.id"
                        class="mb-2 rounded-md border border-gray-200 bg-white p-2 dark:border-gray-700 dark:bg-gray-900"
                      >
                        <div class="whitespace-pre-wrap text-xs text-gray-700 dark:text-gray-200">
                          {{ row.quote }}
                        </div>
                        <div class="mt-1 flex flex-wrap items-center gap-1 text-[11px] text-gray-400">
                          <span v-if="row.clause_no?.length">
                            {{ t('reviews.evidenceClause', { clause: row.clause_no.join(',') }) }}
                          </span>
                          <span v-if="row.page_number">
                            {{ t('reviews.evidencePage', { page: row.page_number }) }}
                          </span>
                          <span v-if="row.char_start !== null && row.char_end !== null">
                            {{
                              t('reviews.evidenceOffset', {
                                start: row.char_start,
                                end: row.char_end
                              })
                            }}
                          </span>
                          <n-tag v-if="row.stale" type="warning" size="small">
                            {{ t('reviews.staleBadge') }}
                          </n-tag>
                          <n-tag v-if="row.selected" type="success" size="small">
                            {{ t('reviews.evidenceSelected') }}
                          </n-tag>
                        </div>
                        <n-button
                          text
                          size="tiny"
                          type="primary"
                          class="mt-1"
                          :disabled="detail.status === 'blocked'"
                          @click="toggleEvidence(item, row)"
                        >
                          {{
                            row.selected
                              ? t('reviews.evidenceDeselect')
                              : t('reviews.evidenceSelect')
                          }}
                        </n-button>
                      </div>
                    </template>
                    <div v-else class="text-xs text-gray-400">{{ t('reviews.evidenceEmpty') }}</div>
                  </div>
                </div>
              </div>

              <!-- 人工决策 -->
              <div class="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center">
                <span class="text-xs font-medium text-gray-500">{{ t('reviews.decisionTitle') }}</span>
                <n-radio-group
                  v-model:value="decisionOf(item).status"
                  size="small"
                  :disabled="detail.status === 'blocked' || !item.change_type"
                >
                  <n-radio-button value="verified">{{ t('reviews.statusVerified') }}</n-radio-button>
                  <n-radio-button value="needs_human">
                    {{ t('reviews.statusNeedsHuman') }}
                  </n-radio-button>
                  <n-radio-button value="not_applicable">
                    {{ t('reviews.statusNotApplicable') }}
                  </n-radio-button>
                </n-radio-group>
                <n-input
                  v-model:value="decisionOf(item).note"
                  size="small"
                  class="flex-1"
                  :placeholder="t('reviews.decisionNote')"
                  :disabled="detail.status === 'blocked'"
                />
                <n-button
                  size="small"
                  type="primary"
                  :disabled="detail.status === 'blocked'"
                  :loading="decidingItem === item.id"
                  @click="saveDecision(item)"
                >
                  {{ t('reviews.decisionSave') }}
                </n-button>
              </div>
            </div>
          </div>

          <!-- 导出历史 -->
          <div class="mt-8">
            <div class="mb-2 text-base font-semibold text-gray-700 dark:text-gray-200">
              {{ t('reviews.exportHistory') }}
            </div>
            <n-empty v-if="exports.length === 0" :description="t('reviews.exportEmpty')" />
            <div
              v-for="row in exports"
              :key="row.id"
              class="mb-2 flex flex-col gap-2 rounded-lg border border-gray-200 bg-white/70 p-3 text-sm dark:border-gray-800 dark:bg-gray-900/50 sm:flex-row sm:items-center sm:justify-between"
            >
              <div class="text-gray-600 dark:text-gray-300">
                <div>
                  {{
                    t('reviews.exportCounts', {
                      items: row.item_count,
                      needsHuman: row.needs_human_count,
                      unidentified: row.unidentified_count
                    })
                  }}
                </div>
                <div class="text-xs text-gray-400">
                  {{ t('reviews.exportSha') }}: {{ row.payload_sha256.slice(0, 16) }} ·
                  {{ row.created_at ? new Date(row.created_at).toLocaleString() : '' }}
                </div>
              </div>
              <div class="flex gap-2">
                <n-button size="small" @click="downloadExport(row.id, 'json')">
                  {{ t('reviews.downloadJson') }}
                </n-button>
                <n-button size="small" @click="downloadExport(row.id, 'markdown')">
                  {{ t('reviews.downloadMarkdown') }}
                </n-button>
              </div>
            </div>
          </div>
        </template>
      </n-spin>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useMessage } from 'naive-ui'
import { ArrowBackOutline, RefreshOutline } from '@vicons/ionicons5'
import {
  reviewsApi,
  type ReviewCaseDetail,
  type ReviewChangeType,
  type ReviewDecisionStatus,
  type ReviewEvidenceRow,
  type ReviewExportSummary,
  type ReviewItem,
  type ReviewItemStatus,
  type ReviewSide
} from '@/api/reviews'
import { apiUnwrap } from '@/utils/apiUnwrap'

const SIDES: ReviewSide[] = ['policy', 'base', 'revised']

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const message = useMessage()

const caseId = computed(() => String(route.params.id || ''))
const detail = ref<ReviewCaseDetail | null>(null)
const exports = ref<ReviewExportSummary[]>([])
const loading = ref(false)
const loadError = ref(false)
const analyzing = ref(false)
const exporting = ref(false)
const decidingItem = ref('')

const decisions = reactive<Record<string, { status: ReviewDecisionStatus; note: string }>>({})

const statusLabel = computed<Record<ReviewCaseDetail['status'], string>>(() => ({
  blocked: t('reviews.statusBlocked'),
  ready: t('reviews.statusReady'),
  in_review: t('reviews.statusInReview'),
  completed: t('reviews.statusCompleted')
}))
const statusTagType: Record<ReviewCaseDetail['status'], 'error' | 'info' | 'warning' | 'success'> = {
  blocked: 'error',
  ready: 'info',
  in_review: 'warning',
  completed: 'success'
}
const changeLabel = computed<Record<ReviewChangeType, string>>(() => ({
  unchanged: t('reviews.changeUnchanged'),
  modified: t('reviews.changeModified'),
  added: t('reviews.changeAdded'),
  removed: t('reviews.changeRemoved'),
  renumbered: t('reviews.changeRenumbered'),
  unidentified: t('reviews.changeUnidentified')
}))
const changeTagType: Record<ReviewChangeType, 'default' | 'success' | 'warning' | 'error' | 'info'> =
  {
    unchanged: 'success',
    modified: 'warning',
    added: 'info',
    removed: 'error',
    renumbered: 'info',
    unidentified: 'warning'
  }
const itemStatusLabel = computed<Record<ReviewItemStatus, string>>(() => ({
  pending: t('reviews.statusPending'),
  verified: t('reviews.statusVerified'),
  needs_human: t('reviews.statusNeedsHuman'),
  not_applicable: t('reviews.statusNotApplicable'),
  insufficient: t('reviews.statusInsufficient'),
  parse_failed: t('reviews.statusParseFailed')
}))
const itemStatusTagType: Record<ReviewItemStatus, 'default' | 'success' | 'warning' | 'error' | 'info'> =
  {
    pending: 'default',
    verified: 'success',
    needs_human: 'warning',
    not_applicable: 'info',
    insufficient: 'error',
    parse_failed: 'error'
  }

const analyzed = computed(() => !!detail.value?.items.every((item) => item.change_type !== null))
const canAnalyze = computed(
  () => !!detail.value && ['ready', 'in_review'].includes(detail.value.status)
)
const canExport = computed(() => {
  const current = detail.value
  if (!current || current.status === 'blocked' || !analyzed.value) return false
  return current.items.every((item) =>
    ['verified', 'needs_human', 'not_applicable'].includes(item.status)
  )
})

function sideCap(side: ReviewSide) {
  return side.charAt(0).toUpperCase() + side.slice(1)
}
function itemCap(key: string) {
  return key
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join('')
}
function docOf(side: ReviewSide) {
  return detail.value?.documents?.[side]
}
function decisionOf(item: ReviewItem) {
  if (!decisions[item.id]) {
    decisions[item.id] = {
      status:
        item.status === 'verified' ||
        item.status === 'needs_human' ||
        item.status === 'not_applicable'
          ? item.status
          : 'verified',
      note: item.decision_note || ''
    }
  }
  return decisions[item.id]
}
function unitLabel(unit?: string | null) {
  if (unit === 'workday') return t('reviews.unitWorkday')
  if (unit === 'month') return t('reviews.unitMonth')
  return t('reviews.unitDay')
}
function factLines(item: ReviewItem): Array<{ side: ReviewSide; text: string }> {
  const facts = (item.facts || {}) as Record<string, unknown>
  const lines: Array<{ side: ReviewSide; text: string }> = []
  for (const side of SIDES) {
    const fact = facts[side] as Record<string, unknown> | undefined
    if (!fact || typeof fact !== 'object') continue
    const status = String(fact.status || '')
    if (status === 'identified') {
      const parts: string[] = []
      if (fact.value !== undefined && fact.value !== null) {
        parts.push(t('reviews.factsValue', { value: fact.value, unit: unitLabel(fact.unit as string) }))
      }
      if (Array.isArray(fact.events) && fact.events.length) {
        parts.push(`${t('reviews.eventsLabel')}: ${fact.events.join(', ')}`)
      }
      if (Array.isArray(fact.terms) && fact.terms.length) {
        parts.push(`${t('reviews.termsLabel')}: ${fact.terms.join(', ')}`)
      }
      if (Array.isArray(fact.cross_refs) && fact.cross_refs.length) {
        parts.push(`${t('reviews.crossRefsLabel')}: ${fact.cross_refs.join(', ')}`)
      }
      lines.push({ side, text: parts.join(' · ') || t('reviews.factsNone') })
    } else if (status === 'ambiguous') {
      lines.push({ side, text: t('reviews.ambiguous', { reason: fact.reason || '' }) })
    } else {
      lines.push({ side, text: t('reviews.notFound') })
    }
  }
  return lines
}

async function load() {
  if (!caseId.value) return
  loading.value = true
  loadError.value = false
  try {
    const data = apiUnwrap<ReviewCaseDetail>(await reviewsApi.get(caseId.value))
    detail.value = data
    for (const item of data.items) {
      decisions[item.id] = {
        status:
          item.status === 'verified' ||
          item.status === 'needs_human' ||
          item.status === 'not_applicable'
            ? item.status
            : 'verified',
        note: item.decision_note || ''
      }
    }
    const listed = apiUnwrap<{ items: ReviewExportSummary[] }>(
      await reviewsApi.exports(caseId.value)
    )
    exports.value = listed?.items ?? []
  } catch {
    loadError.value = true
  } finally {
    loading.value = false
  }
}

async function toggleEvidence(item: ReviewItem, row: ReviewEvidenceRow) {
  try {
    await reviewsApi.selectEvidence(caseId.value, item.id, {
      side: row.side,
      chunk_id: row.chunk_id_at_capture,
      role: 'main',
      selected: !row.selected
    })
    await load()
  } catch {
    message.error(t('reviews.loadFailed'))
  }
}

async function saveDecision(item: ReviewItem) {
  const decision = decisionOf(item)
  decidingItem.value = item.id
  try {
    await reviewsApi.decide(caseId.value, item.id, {
      status: decision.status,
      note: decision.note || null
    })
    message.success(t('reviews.decisionSaved'))
    await load()
  } catch {
    message.error(t('reviews.decisionFailed'))
  } finally {
    decidingItem.value = ''
  }
}

async function runAnalyze() {
  analyzing.value = true
  try {
    await reviewsApi.analyze(caseId.value)
    message.success(t('reviews.analysisDone'))
    await load()
  } catch {
    message.error(t('reviews.analysisFailed'))
  } finally {
    analyzing.value = false
  }
}

async function runExport() {
  exporting.value = true
  try {
    await reviewsApi.exportCase(caseId.value)
    message.success(t('reviews.exportDone'))
    await load()
  } catch {
    message.error(t('reviews.exportBlocked'))
  } finally {
    exporting.value = false
  }
}

async function downloadExport(exportId: string, format: 'json' | 'markdown') {
  try {
    const response = await reviewsApi.download(caseId.value, exportId, format)
    const blob = response.data as Blob
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `review-${caseId.value.slice(0, 8)}-${exportId.slice(0, 8)}.${
      format === 'json' ? 'json' : 'md'
    }`
    anchor.click()
    URL.revokeObjectURL(url)
  } catch {
    message.error(t('reviews.downloadFailed'))
  }
}

onMounted(load)
</script>
