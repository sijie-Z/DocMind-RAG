<template>
  <div class="p-4 md:p-6">
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
            <h1 class="text-2xl font-semibold text-gray-800 dark:text-white md:text-[28px]">
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
            <div class="mt-1 text-xs" :title="detail.blocking_reasons.join(' · ')">
              {{ reasonsLabel(t, detail.blocking_reasons) }}
            </div>
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

          <!-- 结论横幅：结论 + 真实进度 + 计数筛选（数据全部来自 items 状态） -->
          <section
            class="sticky top-0 z-20 -mx-4 mb-4 border-b border-slate-200 bg-gray-50 px-4 py-3 dark:border-slate-800 dark:bg-gray-950 md:-mx-6 md:px-6"
            data-testid="review-conclusion"
          >
            <div class="flex flex-wrap items-center gap-2">
              <n-tag :type="conclusion.tag" size="medium">{{ conclusion.title }}</n-tag>
              <span class="line-clamp-2 text-sm text-slate-600 dark:text-slate-300">{{ conclusion.hint }}</span>
              <span v-if="detail.archived_at" class="text-xs text-slate-500 dark:text-slate-400">
                {{ t('reviews.summaryArchivedHint') }}
              </span>
            </div>
            <div class="mt-2 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-slate-600 dark:text-slate-300">
              <span class="tabular-nums">
                {{ t('reviews.summaryProgress', { done: itemStats.decided, total: itemStats.total }) }}
              </span>
              <div class="w-32">
                <n-progress
                  type="line"
                  :percentage="decisionProgress"
                  :show-indicator="false"
                  :height="6"
                />
              </div>
              <span class="tabular-nums">
                {{ t('reviews.summaryChanged', { count: itemStats.changed }) }}
              </span>
              <div class="flex flex-wrap gap-1">
                <n-button
                  v-for="chip in itemFilterChips"
                  :key="chip.value"
                  size="tiny"
                  class="review-chip"
                  :type="itemFilter === chip.value ? 'primary' : 'default'"
                  :secondary="itemFilter !== chip.value"
                  :aria-pressed="itemFilter === chip.value"
                  @click="itemFilter = chip.value"
                >
                  {{ chip.label }} {{ chip.count }}
                </n-button>
              </div>
            </div>
          </section>

          <!-- 文档身份与版本：默认折叠为一条版本链，展开看哈希/readiness -->
          <section class="mb-5 overflow-hidden rounded-xl border border-gray-200 bg-white dark:border-gray-800 dark:bg-gray-900">
            <button
              type="button"
              class="flex w-full flex-wrap items-center gap-2 bg-white px-4 py-3 text-left text-gray-700 hover:bg-gray-50 dark:bg-gray-900 dark:text-gray-200 dark:hover:bg-gray-800"
              :aria-expanded="versionsOpen"
              aria-controls="review-versions-panel"
              data-testid="review-versions-toggle"
              @click="versionsOpen = !versionsOpen"
            >
              <span class="text-sm font-semibold text-gray-700 dark:text-gray-200">
                {{ t('reviews.versionsTitle') }}
              </span>
              <span class="flex flex-wrap items-center gap-1 text-xs text-gray-500 dark:text-gray-400">
                <span
                  v-for="side in SIDES"
                  :key="side"
                  class="rounded-md bg-gray-50 px-2 py-0.5 tabular-nums dark:bg-gray-800"
                >
                  {{ docLabel(side) }} · {{ docOf(side)?.version_label || '—' }}
                  <template v-if="docOf(side)?.version_seq">#{{ docOf(side)?.version_seq }}</template>
                </span>
              </span>
              <n-icon class="ml-auto text-gray-500 dark:text-gray-300">
                <component :is="versionsOpen ? ChevronUpOutline : ChevronDownOutline" />
              </n-icon>
            </button>
            <div
              v-if="versionsOpen"
              id="review-versions-panel"
              class="grid gap-3 border-t border-gray-200 p-4 dark:border-gray-800 md:grid-cols-3"
            >
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
                <div class="mt-1 break-all text-xs text-gray-500 dark:text-gray-400">
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
          </section>

          <!-- 核验项 -->
          <div class="mb-2 flex items-center justify-between">
            <h2 class="text-base font-semibold text-gray-800 dark:text-gray-100">
              {{ t('reviews.itemsTitle') }}
            </h2>
            <span class="text-xs tabular-nums text-gray-500 dark:text-gray-400">
              {{ filteredItems.length }} / {{ itemStats.total }}
            </span>
          </div>
          <div
            v-if="filteredItems.length === 0"
            class="flex flex-col items-center justify-center rounded-xl border border-dashed border-gray-200 bg-white py-12 px-6 text-center dark:border-gray-700 dark:bg-gray-900"
            data-testid="review-items-empty"
          >
            <p class="text-sm text-gray-600 dark:text-gray-300">{{ t('reviews.itemsEmptyFiltered') }}</p>
            <n-button class="mt-4" size="small" type="primary" @click="itemFilter = 'all'">
              {{ t('reviews.filterAllItems') }}
            </n-button>
          </div>
          <div class="space-y-4">
            <div
              v-for="item in filteredItems"
              :id="`review-item-${item.item_key}`"
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
                <div class="font-medium text-gray-500 dark:text-gray-400">{{ t('reviews.factsTitle') }}</div>
                <div v-for="line in factLines(item)" :key="line.side">
                  <span class="text-gray-500 dark:text-gray-400">{{ docLabel(line.side) }}:</span>
                  {{ line.text }}
                </div>
              </div>

              <!-- 证据快照 -->
              <div class="mt-3">
                <div class="mb-1 text-xs font-medium text-gray-500 dark:text-gray-400">{{ t('reviews.evidenceTitle') }}</div>
                <div class="mb-1 text-xs text-gray-500 dark:text-gray-400">{{ t('reviews.selectHint') }}</div>
                <div class="grid gap-3 md:grid-cols-3">
                  <div v-for="side in SIDES" :key="side" class="rounded-lg bg-gray-50 p-2 dark:bg-gray-800/50">
                    <div class="mb-1 text-xs font-medium text-gray-500 dark:text-gray-400">
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
                        <div class="mt-1 flex flex-wrap items-center gap-1 text-xs text-gray-500 dark:text-gray-400">
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
                    <div v-else class="text-xs text-gray-500 dark:text-gray-400">{{ t('reviews.evidenceEmpty') }}</div>
                  </div>
                </div>
              </div>

              <!-- 人工决策 -->
              <div
                v-if="item.decided_by"
                class="mt-3 text-xs text-gray-500 dark:text-gray-400"
                :data-testid="`decision-meta-${item.item_key}`"
              >
                {{ t('reviews.decidedBy') }} #{{ item.decided_by }}
                <span v-if="item.decided_at">
                  · {{ t('reviews.decidedAt') }} {{ formatDateTime(item.decided_at) }}
                </span>
              </div>
              <div class="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center">
                <span class="text-xs font-medium text-gray-500 dark:text-gray-400">{{ t('reviews.decisionTitle') }}</span>
                <n-radio-group
                  v-model:value="decisionOf(item).status"
                  size="small"
                  class="review-decision-group"
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
                <div class="text-xs text-gray-500 dark:text-gray-400">
                  {{ t('reviews.exportSha') }}: {{ row.payload_sha256.slice(0, 16) }} ·
                  {{ formatDateTime(row.created_at) }}
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
import { ArrowBackOutline, ChevronDownOutline, ChevronUpOutline, RefreshOutline } from '@vicons/ionicons5'
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
import { reasonLabel, reasonsLabel } from './reviewReasons'

const SIDES: ReviewSide[] = ['policy', 'base', 'revised']

const { t, locale } = useI18n()
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
const versionsOpen = ref(false)
const itemFilter = ref<'all' | 'changed' | 'pending' | 'decided'>('all')

const itemStats = computed(() => {
  const items = detail.value?.items ?? []
  const total = items.length
  const changed = items.filter((item) =>
    item.change_type !== null &&
    ['modified', 'added', 'removed', 'renumbered'].includes(item.change_type)
  ).length
  const unidentified = items.filter((item) => item.change_type === 'unidentified').length
  const decided = items.filter((item) =>
    ['verified', 'needs_human', 'not_applicable'].includes(item.status)
  ).length
  const needsHuman = items.filter((item) => item.status === 'needs_human').length
  const insufficient = items.filter((item) => item.status === 'insufficient').length
  const parseFailed = items.filter((item) => item.status === 'parse_failed').length
  const pending = items.filter((item) => item.status === 'pending').length
  return { total, changed, unidentified, decided, needsHuman, insufficient, parseFailed, pending, blockedItems: insufficient + parseFailed }
})

const analyzedAll = computed(
  () =>
    itemStats.value.total > 0 &&
    (detail.value?.items ?? []).every((item) => item.change_type !== null)
)
const decisionProgress = computed(() =>
  itemStats.value.total === 0
    ? 0
    : Math.round((itemStats.value.decided / itemStats.value.total) * 100)
)
const filteredItems = computed(() => {
  const items = detail.value?.items ?? []
  if (itemFilter.value === 'changed') {
    return items.filter(
      (item) =>
        item.change_type !== null &&
        ['modified', 'added', 'removed', 'renumbered'].includes(item.change_type)
    )
  }
  if (itemFilter.value === 'pending') {
    return items.filter((item) => !['verified', 'needs_human', 'not_applicable'].includes(item.status))
  }
  if (itemFilter.value === 'decided') {
    return items.filter((item) => ['verified', 'needs_human', 'not_applicable'].includes(item.status))
  }
  return items
})
const itemFilterChips = computed(() => [
  { value: 'all' as const, label: t('reviews.filterAllItems'), count: itemStats.value.total },
  { value: 'changed' as const, label: t('reviews.filterChangedItems'), count: itemStats.value.changed },
  {
    value: 'pending' as const,
    label: t('reviews.filterPendingItems'),
    count: itemStats.value.total - itemStats.value.decided
  },
  { value: 'decided' as const, label: t('reviews.filterDecidedItems'), count: itemStats.value.decided }
])
const conclusion = computed(() => {
  const current = detail.value
  if (!current) return { tag: 'default' as const, title: '', hint: '' }
  if (current.status === 'blocked') {
    return {
      tag: 'error' as const,
      title: t('reviews.summaryBlocked'),
      hint: reasonsLabel(t, current.blocking_reasons)
    }
  }
  if (current.archived_at) {
    return { tag: 'default' as const, title: t('reviews.summaryArchived'), hint: '' }
  }
  // completed 的权威来源是 status（= 已生成导出快照），不能用「全部已决策」冒充。
  if (current.status === 'completed') {
    return {
      tag: 'success' as const,
      title: t('reviews.summaryCompleted'),
      hint: t('reviews.summaryCompletedHint')
    }
  }
  if (!analyzedAll.value) {
    return current.status === 'ready'
      ? { tag: 'info' as const, title: t('reviews.summaryReady'), hint: t('reviews.notAnalyzedHint') }
      : { tag: 'info' as const, title: t('reviews.summaryInReview'), hint: t('reviews.notAnalyzedHint') }
  }
  if (current.status === 'ready') {
    return {
      tag: 'info' as const,
      title: t('reviews.summaryReadyToDecide'),
      hint: t('reviews.summaryInReviewHint')
    }
  }
  if (itemStats.value.needsHuman > 0 || itemStats.value.blockedItems > 0 || itemStats.value.unidentified > 0) {
    const hints: string[] = []
    if (itemStats.value.needsHuman > 0) {
      hints.push(t('reviews.summaryNeedsHumanHint', { count: itemStats.value.needsHuman }))
    }
    if (itemStats.value.blockedItems > 0) {
      hints.push(t('reviews.summaryInsufficientHint', { count: itemStats.value.blockedItems }))
    }
    if (itemStats.value.unidentified > 0) {
      hints.push(t('reviews.summaryUnidentifiedHint', { count: itemStats.value.unidentified }))
    }
    return {
      tag: 'warning' as const,
      title: t('reviews.summaryNeedsHuman'),
      hint: hints.join(' · ')
    }
  }
  if (itemStats.value.total > 0 && itemStats.value.decided === itemStats.value.total) {
    return {
      tag: 'info' as const,
      title: t('reviews.summaryAwaitingExport'),
      hint: t('reviews.summaryAwaitingExportHint')
    }
  }
  return {
    tag: 'info' as const,
    title: t('reviews.summaryInReview'),
    hint: t('reviews.summaryInReviewHint')
  }
})
const dateFormatter = computed(
  () => new Intl.DateTimeFormat(locale.value, { dateStyle: 'medium', timeStyle: 'short' })
)
function formatDateTime(value?: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : dateFormatter.value.format(date)
}

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
// 归档态只可能是 blocked/completed（状态门已挡），archived 判据是纵深防御。
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
      // 组合码（unit_unclear+multiple_durations）与未知码统一走原因码注册表。
      const reasonText = fact.reason ? reasonLabel(t, String(fact.reason)) : ''
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
    // 页面陈旧（他人已归档）：收敛到最新状态，避免继续展示归档按钮。
    const code = (error as { response?: { data?: { error_code?: string } } })?.response?.data
      ?.error_code
    if (code === 'REVIEW_CASE_ALREADY_ARCHIVED') {
      await load(false)
    }
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
        .map(
          (item) =>
            `${itemLabel(String(item.item_key || '?'))}(${(item.reasons || [])
              .map((reason) => reasonLabel(t, String(reason)))
              .join(' / ')})`
        )
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

<style scoped>
@media (max-width: 1023.98px) {
  .review-chip.n-button {
    height: 44px !important;
    padding-left: 12px;
    padding-right: 12px;
  }
  .review-decision-group {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }
  .review-decision-group :deep(.n-radio-button) {
    height: 44px !important;
    line-height: 44px;
  }
  .review-export-empty :deep(.n-empty__description) {
    color: #6b7280;
  }
}
.review-export-empty :deep(.n-empty__description) {
  color: #6b7280;
}
</style>
