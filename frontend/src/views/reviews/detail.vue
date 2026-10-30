<template>
  <div class="min-h-screen bg-gradient-to-br from-slate-50 via-white to-blue-50 dark:from-gray-950 dark:via-gray-900 dark:to-slate-950 p-6">
    <div class="max-w-7xl mx-auto">
      <div class="mb-6 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div class="flex items-center gap-3">
          <n-button
            quaternary
            circle
            :aria-label="t('reviews.backToList')"
            :title="t('reviews.backToList')"
            @click="router.push('/reviews')"
          >
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
          <n-button :loading="loading" quaternary @click="load(false)">
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
          <n-popconfirm
            v-if="canArchive"
            :positive-button-props="{ loading: archiving, disabled: archiving }"
            @positive-click="archiveCase"
          >
            <template #trigger>
              <n-button secondary type="warning" :loading="archiving" data-testid="archive-case">
                {{ t('reviews.archiveCase') }}
              </n-button>
            </template>
            {{ t('reviews.archiveConfirm') }}
          </n-popconfirm>
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
          <n-alert v-if="detail.archived_at" type="info" class="mb-3" :bordered="false" data-testid="archived-banner">
            {{ t('reviews.archivedBanner') }}
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
                  {{ docLabel(side) }}
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
                  {{ itemLabel(item.item_key) }}
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
                  <span class="text-gray-400">{{ docLabel(line.side) }}:</span>
                  {{ line.text }}
                </div>
              </div>

              <!-- 证据快照 -->
              <div class="mt-3">
                <div class="mb-1 text-xs font-medium text-gray-500">{{ t('reviews.evidenceTitle') }}</div>
                <div class="mb-1 text-[11px] text-gray-400">{{ t('reviews.selectHint') }}</div>
                <div class="grid gap-3 md:grid-cols-3">
                  <div v-for="side in SIDES" :key="side" class="rounded-lg bg-gray-50 p-2 dark:bg-gray-800/50">
                    <div class="mb-1 text-xs font-medium text-gray-500">
                      {{ docLabel(side) }}
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
                          :disabled="!canSelectEvidence"
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
              <div
                v-if="item.decided_by"
                class="mt-3 text-[11px] text-gray-400"
                :data-testid="`decision-meta-${item.item_key}`"
              >
                {{ t('reviews.decidedBy') }} #{{ item.decided_by }}
                <span v-if="item.decided_at">
                  · {{ t('reviews.decidedAt') }} {{ new Date(item.decided_at).toLocaleString() }}
                </span>
              </div>
              <div class="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center">
                <span class="text-xs font-medium text-gray-500">{{ t('reviews.decisionTitle') }}</span>
                <n-radio-group
                  v-model:value="decisionOf(item).status"
                  size="small"
                  :disabled="detail.status === 'blocked' || !!detail.archived_at || !item.change_type"
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
                  :maxlength="2000"
                  :disabled="detail.status === 'blocked' || !!detail.archived_at"
                />
                <n-button
                  size="small"
                  type="primary"
                  :disabled="detail.status === 'blocked' || !!detail.archived_at || !item.change_type || !canSaveDecision(item)"
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
import { getErrorMessage } from '@/utils/apiError'

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
const archiving = ref(false)
const decidingItem = ref('')

const decisions = reactive<
  Record<string, { status: ReviewDecisionStatus | null; note: string }>
>({})

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
const canArchive = computed(
  () =>
    !!detail.value &&
    !detail.value.archived_at &&
    ['blocked', 'completed'].includes(detail.value.status)
)
const canAnalyze = computed(
  () =>
    !!detail.value &&
    !detail.value.archived_at &&
    ['ready', 'in_review'].includes(detail.value.status)
)
const canExport = computed(() => {
  const current = detail.value
  if (!current || current.archived_at || current.status === 'blocked' || !analyzed.value) return false
  return current.items.every((item) =>
    ['verified', 'needs_human', 'not_applicable'].includes(item.status)
  )
})

// 静态键映射：动态模板键无法通过 tUsageKeysDefined 门禁（前缀不是对象节点）。
const DOC_LABELS: Record<ReviewSide, string> = {
  policy: 'reviews.docPolicy',
  base: 'reviews.docBase',
  revised: 'reviews.docRevised'
}
const ITEM_LABELS: Record<string, string> = {
  payment_term: 'reviews.itemPaymentTerm',
  start_condition: 'reviews.itemStartCondition',
  acceptance_invoice: 'reviews.itemAcceptanceInvoice',
  exception: 'reviews.itemException'
}
function docLabel(side: ReviewSide) {
  return t(DOC_LABELS[side])
}
function itemLabel(key: string) {
  return ITEM_LABELS[key] ? t(ITEM_LABELS[key]) : key
}
function docOf(side: ReviewSide) {
  return detail.value?.documents?.[side]
}
function decisionOf(item: ReviewItem) {
  if (!decisions[item.id]) {
    decisions[item.id] = {
      // 未决策/证据不足/解析失败不预选：人工必须显式选择三态之一。
      status:
        item.status === 'verified' ||
        item.status === 'needs_human' ||
        item.status === 'not_applicable'
          ? item.status
          : null,
      note: item.decision_note || ''
    }
  }
  return decisions[item.id]
}
function canSaveDecision(item: ReviewItem) {
  const draft = decisionOf(item)
  if (!draft.status) return false
  // 与服务端一致时不重复提交：completed 案件的无变更保存会把状态打回 in_review。
  // 后端会 strip 备注空白：比较时同样 trim，避免仅靠尾随空格绕过变更检测。
  return (
    draft.status !== item.status ||
    (draft.note || '').trim() !== (item.decision_note || '').trim()
  )
}
const canSelectEvidence = computed(
  () => !!detail.value && ['ready', 'in_review'].includes(detail.value.status)
)
function unitLabel(unit?: string | null) {
  if (unit === 'workday') return t('reviews.unitWorkday')
  if (unit === 'month') return t('reviews.unitMonth')
  if (unit === 'day') return t('reviews.unitDay')
  // 契约外/缺失单位不得渲染成确定的「自然日」。
  return unit ? String(unit) : t('reviews.factsNone')
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
      const reasonMap: Record<string, string> = {
        unit_unclear: t('reviews.ambiguousUnit'),
        multiple_durations: t('reviews.ambiguousMultiple')
      }
      // 后端可能给出组合码（unit_unclear+multiple_durations）。
      const reasonText =
        String(fact.reason || '')
          .split('+')
          .filter(Boolean)
          .map((code) => reasonMap[code] || code)
          .join(' · ') || String(fact.reason || '')
      const candidates = Array.isArray(fact.candidates)
        ? (
            fact.candidates as Array<{
              value?: number
              unit?: string | null
              unit_raw?: string | null
              raw?: string | null
            }>
          )
            .map((candidate) =>
              candidate.raw
                ? String(candidate.raw)
                : `${candidate.value} ${
                    candidate.unit ? unitLabel(candidate.unit) : candidate.unit_raw || ''
                  }`
            )
            .join(' / ')
        : ''
      lines.push({
        side,
        text:
          (reasonText || t('reviews.notFound')) +
          (candidates ? ` · ${t('reviews.candidatesLabel')}: ${candidates}` : '')
      })
    } else {
      lines.push({ side, text: t('reviews.notFound') })
    }
  }
  return lines
}

async function load(keepDrafts = false) {
  if (!caseId.value) return
  loading.value = true
  loadError.value = false
  try {
    const data = apiUnwrap<ReviewCaseDetail>(await reviewsApi.get(caseId.value))
    detail.value = data
    for (const item of data.items) {
      const decided =
        item.status === 'verified' ||
        item.status === 'needs_human' ||
        item.status === 'not_applicable'
      if (keepDrafts && decisions[item.id]) continue
      decisions[item.id] = {
        status: decided ? (item.status as ReviewDecisionStatus) : null,
        note: item.decision_note || ''
      }
    }
  } catch {
    loadError.value = true
  } finally {
    loading.value = false
  }
  try {
    const listed = apiUnwrap<{ items: ReviewExportSummary[] }>(
      await reviewsApi.exports(caseId.value)
    )
    exports.value = listed?.items ?? []
  } catch {
    // 导出历史失败不影响案件主体；保持旧列表。
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
    await load(true)
  } catch {
    message.error(t('reviews.evidenceFailed'))
  }
}

async function saveDecision(item: ReviewItem) {
  const decision = decisionOf(item)
  if (!decision.status) return
  decidingItem.value = item.id
  try {
    await reviewsApi.decide(caseId.value, item.id, {
      status: decision.status,
      note: decision.note || null
    })
    message.success(t('reviews.decisionSaved'))
    await load(true)
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
    await load(true)
  } catch {
    message.error(t('reviews.analysisFailed'))
  } finally {
    analyzing.value = false
  }
}

async function archiveCase() {
  if (!caseId.value || archiving.value) return
  archiving.value = true
  try {
    await reviewsApi.archive(caseId.value)
    message.success(t('reviews.archiveDone'))
    await load(false)
  } catch (error) {
    message.error(getErrorMessage(error, t('reviews.archiveFailed')))
  } finally {
    archiving.value = false
  }
}

async function runExport() {
  exporting.value = true
  try {
    await reviewsApi.exportCase(caseId.value)
    message.success(t('reviews.exportDone'))
    await load(true)
  } catch (error) {
    const response = (error as { response?: { status?: number; data?: unknown } })?.response
    if (response?.status === 409) {
      const detailBlock = (
        response.data as {
          detail?: { items?: Array<{ item_key?: string; reasons?: string[] }> }
        }
      )?.detail
      const items = (detailBlock?.items || [])
        .map((item) => `${item.item_key || '?'}(${(item.reasons || []).join('/')})`)
        .join(', ')
      message.error(
        items ? t('reviews.exportBlockedDetail', { items }) : t('reviews.exportBlocked')
      )
    } else {
      message.error(t('reviews.exportFailed'))
    }
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
    document.body.appendChild(anchor)
    anchor.click()
    document.body.removeChild(anchor)
    setTimeout(() => URL.revokeObjectURL(url), 0)
  } catch {
    message.error(t('reviews.downloadFailed'))
  }
}

onMounted(load)
</script>
