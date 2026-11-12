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
        <div class="review-top-actions flex flex-wrap gap-2">
          <n-button :loading="loading" quaternary @click="load(true)">
            <template #icon><n-icon><RefreshOutline /></n-icon></template>
            {{ t('reviews.retry') }}
          </n-button>
          <n-button
            v-if="!analyzedAll || !canAnalyze"
            data-testid="review-analyze"
            type="primary"
            :loading="analyzing"
            :disabled="!canAnalyze"
            @click="runAnalyze"
          >
            {{ analyzing ? t('reviews.analyzing') : t('reviews.analyze') }}
          </n-button>
          <n-button
            v-if="!analyzedAll"
            data-testid="review-export"
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
            class="review-conclusion sticky top-0 z-20 -mx-4 mb-4 border-b border-slate-200 bg-gray-50 px-4 py-3 dark:border-slate-800 dark:bg-gray-950 md:-mx-6 md:px-6"
            data-testid="review-conclusion"
          >
            <div class="flex flex-wrap items-center gap-2">
              <n-tag :type="conclusion.tag" size="medium">{{ conclusion.title }}</n-tag>
              <span class="line-clamp-1 text-sm text-slate-600 dark:text-slate-300 md:line-clamp-2">{{ conclusion.hint }}</span>
              <span v-if="detail.archived_at" class="text-xs text-slate-500 dark:text-slate-400">
                {{ t('reviews.summaryArchivedHint') }}
              </span>
            </div>
            <div class="mt-2 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-slate-600 dark:text-slate-300">
              <span class="tabular-nums">
                {{ t('reviews.summaryProgress', { done: itemStats.decided, total: itemStats.total }) }}
              </span>
              <div class="review-progress w-32">
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
              <div class="review-filter-chips flex flex-wrap gap-1">
                <n-button
                  v-for="chip in itemFilterChips"
                  :key="chip.value"
                  size="small"
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
            <div class="mt-2 flex flex-wrap items-center gap-2" data-testid="review-primary-actions">
              <n-button
                v-if="nextPendingCount > 0 && detail.status !== 'blocked' && !detail.archived_at"
                size="small"
                type="primary"
                data-testid="review-next-pending"
                @click="goNextPending"
              >
                {{ t('reviews.nextPending') }} · {{ nextPendingCount }}
              </n-button>
              <n-button
                v-if="analyzedAll"
                size="small"
                type="primary"
                secondary
                data-testid="review-export"
                :loading="exporting"
                :disabled="!canExport"
                @click="runExport"
              >
                {{ exporting ? t('reviews.exporting') : t('reviews.export') }}
              </n-button>
              <n-button v-if="exports.length" size="small" data-testid="review-latest-download" @click="downloadExport(exports[0].id, 'json')">
                {{ t('reviews.downloadLatest') }}
              </n-button>
              <n-tag v-if="detail.stale_evidence_count" type="warning" size="small">
                {{ t('reviews.staleCount', { count: detail.stale_evidence_count }) }}
              </n-tag>
              <span
                v-if="hasUnsavedDrafts"
                role="status"
                aria-live="polite"
                class="text-xs text-amber-700 dark:text-amber-400"
              >
                {{ t('reviews.unsavedDraftsHint') }}
              </span>
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
            v-if="itemStats.total === 0"
            class="rounded-xl border border-dashed border-gray-200 bg-white py-10 px-6 text-center text-sm text-gray-600 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300"
            data-testid="review-items-none"
          >
            {{ t('reviews.itemsNone') }}
          </div>
          <div
            v-else-if="filteredItems.length === 0"
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
              class="scroll-mt-44 rounded-xl border border-gray-200 bg-white/80 p-4 dark:border-gray-800 dark:bg-gray-900/60 md:scroll-mt-32"
              :data-testid="`review-item-${item.item_key}`"
            >
              <div class="flex flex-wrap items-center gap-2">
                <span
                  class="text-base font-semibold text-gray-800 dark:text-gray-100"
                  tabindex="-1"
                  data-item-heading
                >{{ itemLabel(item.item_key) }}</span>
                <n-tag v-if="item.change_type" :type="changeTagType[item.change_type]" size="small">
                  {{ changeLabel[item.change_type] }}
                </n-tag>
                <n-tag v-else size="small">{{ t('reviews.changeNotAnalyzed') }}</n-tag>
                <n-tag :type="itemStatusTagType[item.status]" size="small">
                  {{ itemStatusLabel[item.status] }}
                </n-tag>
                <n-tag v-if="item.selected_evidence_count" type="success" size="small">
                  {{ t('reviews.evidenceSelectedCount', { count: item.selected_evidence_count }) }}
                </n-tag>
              </div>
              <div
                v-if="factLocator(item, 'base') || factLocator(item, 'revised')"
                class="mt-1 flex flex-wrap gap-x-3 text-xs text-slate-500 dark:text-slate-400"
                data-testid="item-locator"
              >
                <span v-if="factLocator(item, 'base')">
                  {{ t('reviews.baseVersion') }} {{ factLocator(item, 'base') }}
                </span>
                <span v-if="factLocator(item, 'revised')">
                  {{ t('reviews.revisedVersion') }} {{ factLocator(item, 'revised') }}
                </span>
              </div>

              <!-- 文本事实 -->
              <div class="mt-2 space-y-1 text-xs text-gray-600 dark:text-gray-300">
                <div class="font-medium text-gray-500 dark:text-gray-400">{{ t('reviews.factsTitle') }}</div>
                <div v-for="line in factLines(item)" :key="line.side">
                  <span class="text-gray-500 dark:text-gray-400">{{ docLabel(line.side) }}:</span>
                  {{ line.text }}
                </div>
              </div>

              <!-- 差异条：基准 → 修订（无法判定时不猜） -->
              <div
                v-if="diffSummary(item)"
                data-testid="item-diff"
                class="mt-2 rounded-md border border-amber-200 bg-amber-50 px-2 py-1.5 text-xs leading-relaxed text-amber-900 dark:border-amber-800/60 dark:bg-amber-900/10 dark:text-amber-200 xl:line-clamp-2"
                :title="diffSummary(item)"
              >
                <span class="font-semibold">
                  {{ item.change_type ? changeLabel[item.change_type] : '' }}:
                </span>
                {{ diffSummary(item) }}
              </div>
              <div
                v-else-if="item.change_type === 'modified' || item.change_type === 'renumbered'"
                data-testid="item-diff-same"
                class="mt-2 text-xs text-slate-500 dark:text-slate-400"
              >
                {{ t('reviews.diffTextSameHint') }}
              </div>

              <!-- 证据快照 -->
              <div class="mt-3">
                <div class="mb-1 text-xs font-medium text-gray-500 dark:text-gray-400">{{ t('reviews.evidenceTitle') }}</div>
                <div class="mb-1 text-xs text-gray-500 dark:text-gray-400">{{ t('reviews.selectHint') }}</div>
                <div class="grid gap-3 lg:grid-cols-2 xl:grid-cols-3">
                  <div v-for="side in EVIDENCE_SIDES" :key="side" class="rounded-lg bg-gray-50 p-2 dark:bg-gray-800/50">
                    <div class="mb-1 text-xs font-medium text-gray-500 dark:text-gray-400">
                      {{ docLabel(side) }}
                    </div>
                    <template v-if="(item.evidence[side] || []).length">
                      <div
                        v-for="row in evidenceRows(item, side)"
                        :key="row.id"
                        :data-testid="`review-evidence-row-${side}`"
                        class="mb-2 rounded-md border border-gray-200 bg-white p-2 dark:border-gray-700 dark:bg-gray-900"
                      >
                        <div class="whitespace-pre-wrap text-xs text-gray-700 dark:text-gray-200">
                          {{ row.quote }}
                        </div>
                        <div class="mt-1 flex flex-wrap items-center gap-1 text-xs text-gray-500 dark:text-gray-400">
                          <n-tag
                            v-if="isPrimaryEvidence(item, row)"
                            type="info"
                            size="small"
                            data-testid="evidence-primary"
                            :title="t('reviews.evidencePrimaryHint')"
                          >
                            {{ t('reviews.evidencePrimary') }}
                          </n-tag>
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
                        <div class="mt-2 flex flex-wrap items-center gap-2">
                          <a
                            v-if="evidenceHref(row)"
                            :href="evidenceHref(row)"
                            target="_blank"
                            rel="noopener noreferrer"
                            data-testid="evidence-open-original"
                            class="evidence-open-original inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-blue-600 no-underline transition-colors hover:bg-blue-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 dark:text-blue-400 dark:hover:bg-blue-500/10"
                            :aria-label="evidenceAriaLabel(row)"
                            :title="evidenceLinkHint(row)"
                          >
                            <n-icon size="14" aria-hidden="true"><OpenOutline /></n-icon>
                            {{ evidenceLinkLabel(row) }}
                          </a>
                          <span
                            v-else
                            class="text-xs text-slate-600 dark:text-slate-400"
                            data-testid="evidence-open-unavailable"
                            :title="evidenceUnavailableHint(row)"
                          >
                            {{ docOf(row.side)?.missing
                              ? t('reviews.evidenceOpenUnavailable')
                              : t('reviews.evidenceOpenNoChunk') }}
                          </span>
                          <n-button
                            size="tiny"
                            tertiary
                            class="evidence-select evidence-copy-link"
                            data-testid="evidence-copy-link"
                            :aria-label="`${t('reviews.evidenceCopyLink')} · ${docLabel(row.side)}`"
                            :title="t('reviews.evidenceCopyLink')"
                            @click="copyEvidenceLink(row)"
                          >
                            <template #icon><n-icon><CopyOutline /></n-icon></template>
                          </n-button>
                          <n-button
                            size="tiny"
                            tertiary
                            class="evidence-select"
                            :type="row.selected ? 'success' : 'primary'"
                            :aria-pressed="row.selected"
                            :aria-label="`${row.selected ? t('reviews.evidenceDeselect') : t('reviews.evidenceSelect')} · ${docLabel(row.side)}`"
                            :loading="evidenceBusy === row.id"
                            :disabled="!canSelectEvidence || !!evidenceBusy"
                            @click="toggleEvidence(item, row)"
                          >
                            {{
                              row.selected
                                ? t('reviews.evidenceDeselect')
                                : t('reviews.evidenceSelect')
                            }}
                          </n-button>
                        </div>
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
                  role="radiogroup"
                  :aria-label="t('reviews.decisionTitle')"
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
                  class="review-decision-note flex-1"
                  :placeholder="t('reviews.decisionNote')"
                  :input-props="{ 'aria-label': t('reviews.decisionNote') }"
                  :maxlength="2000"
                  :disabled="detail.status === 'blocked' || !!detail.archived_at"
                />
                <n-button
                  size="small"
                  type="primary"
                  class="review-decision-save"
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
            <n-empty v-if="exports.length === 0" class="review-export-empty" :description="t('reviews.exportEmpty')" />
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
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useMessage } from 'naive-ui'
import { ArrowBackOutline, ChevronDownOutline, ChevronUpOutline, CopyOutline, OpenOutline, RefreshOutline } from '@vicons/ionicons5'
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
import { parseApiDate } from '@/utils/format'
import { getErrorMessage } from '@/utils/apiError'
import { reasonLabel, reasonsLabel } from './reviewReasons'
import { buildEvidencePermalink, buildEvidenceLinkTarget } from './evidenceLink'

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
const evidenceBusy = ref('')
const versionsOpen = ref(false)
const ITEM_FILTERS = ['all', 'changed', 'pending', 'decided'] as const
type ItemFilter = (typeof ITEM_FILTERS)[number]
function filterFromRoute(): ItemFilter {
  const raw = (route.query || {}).filter
  return typeof raw === 'string' && (ITEM_FILTERS as readonly string[]).includes(raw)
    ? (raw as ItemFilter)
    : 'all'
}
const itemFilter = ref<ItemFilter>(filterFromRoute())
let loadSeq = 0
let foregroundSeq = 0

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
    const hints: string[] = [t('reviews.summaryCompletedHint')]
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
      tag: 'success' as const,
      title: t('reviews.summaryCompleted'),
      hint: hints.join(' · ')
    }
  }
  if (!analyzedAll.value) {
    return current.status === 'ready'
      ? { tag: 'info' as const, title: t('reviews.summaryReady'), hint: t('reviews.notAnalyzedHint') }
      : { tag: 'info' as const, title: t('reviews.summaryInReview'), hint: t('reviews.notAnalyzedHint') }
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
  if (current.status === 'ready') {
    return {
      tag: 'info' as const,
      title: t('reviews.summaryReadyToDecide'),
      hint: t('reviews.summaryInReviewHint')
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
  const date = parseApiDate(value)
  return date ? dateFormatter.value.format(date) : '—'
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

const analyzed = computed(() =>
  !!detail.value && detail.value.items.length > 0 && detail.value.items.every((item) => item.change_type !== null)
)
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
  if (hasUnsavedDrafts.value) return false
  return current.items.every((item) =>
    ['verified', 'needs_human', 'not_applicable'].includes(item.status)
  )
})

const hasUnsavedDrafts = computed(() => {
  const items = detail.value?.items ?? []
  return items.some((item) => {
    const draft = decisions[item.id]
    if (!draft || !draft.status) return false
    return (
      draft.status !== item.status ||
      (draft.note || '').trim() !== (item.decision_note || '').trim()
    )
  })
})

const DECIDED_STATUSES = ['verified', 'needs_human', 'not_applicable']
const nextPendingCount = computed(
  () =>
    (detail.value?.items ?? []).filter(
      (item) => item.change_type !== null && !DECIDED_STATUSES.includes(item.status)
    ).length
)

/** 滚到下一条待决策项，并把焦点移到标题（粘性横幅之外仍是键盘可达的）。 */
function goNextPending() {
  const next = (detail.value?.items ?? []).find(
    (item) => item.change_type !== null && !DECIDED_STATUSES.includes(item.status)
  )
  if (!next) return
  itemFilter.value = 'pending'
  void nextTick(() => {
    const element = document.getElementById(`review-item-${next.item_key}`)
    element?.scrollIntoView?.({ block: 'start', behavior: 'smooth' })
    element?.querySelector<HTMLElement>('[data-item-heading]')?.focus({ preventScroll: true })
  })
}

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
// 证据对比顺序：基准 ↔ 修订优先（核心对比），制度/模板整段引文放最后，
// 避免两栏布局下 policy+base 占一行、真正要对比的修订版被推到下一行。
const EVIDENCE_SIDES: ReviewSide[] = ['base', 'revised', 'policy']
function docLabel(side: ReviewSide) {
  return t(DOC_LABELS[side])
}
function itemLabel(key: string) {
  return ITEM_LABELS[key] ? t(ITEM_LABELS[key]) : key
}
function docOf(side: ReviewSide) {
  return detail.value?.documents?.[side]
}

// 证据 → 当前文档深链（新标签页打开，避免丢失未保存的决策草稿）。
function evidenceHref(row: ReviewEvidenceRow): string {
  const target = buildEvidenceLinkTarget(row, docOf(row.side))
  return target ? router.resolve(target).href : ''
}
function evidenceLinkLabel(row: ReviewEvidenceRow): string {
  return row.stale ? t('reviews.evidenceOpenCurrent') : t('reviews.evidenceOpenOriginal')
}
function evidenceLinkHint(row: ReviewEvidenceRow): string {
  return row.stale ? t('reviews.evidenceOpenStaleHint') : t('reviews.evidenceOpenOriginalHint')
}
function evidenceAriaLabel(row: ReviewEvidenceRow): string {
  const parts = [evidenceLinkLabel(row), docLabel(row.side)]
  if (row.clause_no?.length) {
    parts.push(t('reviews.evidenceClause', { clause: row.clause_no.join(',') }))
  } else if (row.page_number) {
    parts.push(t('reviews.evidencePage', { page: row.page_number }))
  }
  parts.push(t('reviews.evidenceOpenNewTab'))
  return parts.join(' · ')
}
function evidenceUnavailableHint(row: ReviewEvidenceRow): string {
  return docOf(row.side)?.missing
    ? t('reviews.evidenceOpenUnavailableHint')
    : t('reviews.evidenceOpenNoChunkHint')
}

async function copyEvidenceLink(row: ReviewEvidenceRow) {
  const target = buildEvidencePermalink(row)
  if (!target) {
    message.warning(t('reviews.evidenceLinkUnavailable'))
    return
  }
  try {
    const href = router.resolve(target).href
    await navigator.clipboard.writeText(new URL(href, window.location.origin).href)
    message.success(t('reviews.evidenceLinkCopied'))
  } catch {
    message.error(t('reviews.evidenceLinkCopyFailed'))
  }
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
function factText(item: ReviewItem, side: ReviewSide): string {
  const facts = (item.facts || {}) as Record<string, unknown>
  const fact = facts[side] as Record<string, unknown> | undefined
  if (!fact || typeof fact !== 'object') return '—'
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
    return parts.join(' · ') || t('reviews.factsNone')
  }
  if (status === 'ambiguous') {
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
    return (
      (reasonText || t('reviews.notFound')) +
      (candidates ? ` · ${t('reviews.candidatesLabel')}: ${candidates}` : '')
    )
  }
  return t('reviews.notFound')
}

function factLines(item: ReviewItem): Array<{ side: ReviewSide; text: string }> {
  const facts = (item.facts || {}) as Record<string, unknown>
  return SIDES.filter((side) => {
    const fact = facts[side]
    return !!fact && typeof fact === 'object'
  }).map((side) => ({ side, text: factText(item, side) }))
}

/** modified/added/removed/renumbered 的基准→修订差异条；文本事实相同则不硬造箭头。 */
function diffSummary(item: ReviewItem): string {
  const type = item.change_type
  if (!type || type === 'unchanged' || type === 'unidentified') return ''
  const base = factText(item, 'base')
  const revised = factText(item, 'revised')
  if (type === 'added') return base === '—' && revised === '—' ? '' : `— → ${revised}`
  if (type === 'removed') return base === '—' && revised === '—' ? '' : `${base} → —`
  // 文本事实相同（差异来自正文哈希/条号）时不展示 `A → A` 的误导性对比。
  if (base === revised) return ''
  return `${base} → ${revised}`
}

/** 文本事实里的条款/页码定位（不依赖证据行是否为空）。 */
function factLocator(item: ReviewItem, side: ReviewSide): string {
  const facts = (item.facts || {}) as Record<string, unknown>
  const fact = facts[side] as Record<string, unknown> | undefined
  if (!fact || typeof fact !== 'object') return ''
  const parts: string[] = []
  if (Array.isArray(fact.clause_no) && fact.clause_no.length) {
    parts.push(t('reviews.evidenceClause', { clause: fact.clause_no.join(',') }))
  }
  if (Array.isArray(fact.section_no) && fact.section_no.length) {
    parts.push(t('reviews.evidenceSection', { section: fact.section_no.join(',') }))
  }
  if (fact.page_number) {
    parts.push(t('reviews.evidencePage', { page: fact.page_number }))
  }
  return parts.join(' · ')
}

/** 分析层抽取该侧事实时实际定位的分块；旧数据/未识别项可能没有。 */
function primaryChunkId(item: ReviewItem, side: ReviewSide): string {
  const facts = (item.facts || {}) as Record<string, unknown>
  const fact = facts[side] as Record<string, unknown> | undefined
  return fact && typeof fact === 'object' && fact.chunk_id ? String(fact.chunk_id) : ''
}

/** 分析定位的证据候选置顶；其余保持服务端分块顺序（稳定排序）。 */
function evidenceRows(item: ReviewItem, side: ReviewSide): ReviewEvidenceRow[] {
  const rows = item.evidence[side] || []
  const primary = primaryChunkId(item, side)
  if (!primary) return rows
  return [...rows].sort(
    (a, b) =>
      Number(b.chunk_id_at_capture === primary) - Number(a.chunk_id_at_capture === primary)
  )
}

function isPrimaryEvidence(item: ReviewItem, row: ReviewEvidenceRow): boolean {
  const primary = primaryChunkId(item, row.side)
  return !!primary && row.chunk_id_at_capture === primary
}

async function load(
  keepDrafts = false,
  options: { background?: boolean; includeExports?: boolean } = {}
) {
  if (!caseId.value) return
  const seq = ++loadSeq
  const background = options.background === true
  const includeExports = options.includeExports !== false
  // 前后台各用独立令牌：后台请求不得让前台 spinner 永久卡住。
  const foregroundToken = background ? null : ++foregroundSeq
  if (!background) {
    loading.value = true
    loadError.value = false
  }
  try {
    const data = apiUnwrap<ReviewCaseDetail>(await reviewsApi.get(caseId.value))
    if (seq !== loadSeq) return
    // 脏合并：只有用户真的改过的草稿才保留；未被编辑的项采纳服务端最新值
    // （其他评审的决策/备注会收敛进来，不会被 keepDrafts 整表冻结）。
    const previousById = new Map((detail.value?.items ?? []).map((item) => [item.id, item]))
    detail.value = data
    loadError.value = false
    for (const item of data.items) {
      const previous = previousById.get(item.id)
      const draft = decisions[item.id]
      const previousDecided = previous &&
        (previous.status === 'verified' ||
          previous.status === 'needs_human' ||
          previous.status === 'not_applicable')
        ? previous.status
        : null
      const draftDirty =
        !!previous &&
        !!draft &&
        (draft.status !== previousDecided ||
          (draft.note || '').trim() !== (previous.decision_note || '').trim())
      if (keepDrafts && draftDirty) continue
      const decided =
        item.status === 'verified' ||
        item.status === 'needs_human' ||
        item.status === 'not_applicable'
      decisions[item.id] = {
        status: decided ? (item.status as ReviewDecisionStatus) : null,
        note: item.decision_note || ''
      }
    }
  } catch {
    // 后台刷新失败不把整页打成错误态；保留当前内容与已有错误提示。
    if (seq === loadSeq && !background) loadError.value = true
  } finally {
    if (foregroundToken !== null && foregroundToken === foregroundSeq) loading.value = false
  }
  if (!includeExports) return
  try {
    const listed = apiUnwrap<{ items: ReviewExportSummary[] }>(
      await reviewsApi.exports(caseId.value)
    )
    if (seq === loadSeq) exports.value = listed?.items ?? []
  } catch {
    // 导出历史失败不影响案件主体；保持旧列表。
  }
}

async function toggleEvidence(item: ReviewItem, row: ReviewEvidenceRow) {
  if (evidenceBusy.value) return
  evidenceBusy.value = row.id
  try {
    await reviewsApi.selectEvidence(caseId.value, item.id, {
      side: row.side,
      chunk_id: row.chunk_id_at_capture,
      role: 'main',
      selected: !row.selected
    })
    // 后台刷新：不闪全页 spinner，也不重复拉导出历史。
    await load(true, { background: true, includeExports: false })
  } catch {
    message.error(t('reviews.evidenceFailed'))
  } finally {
    evidenceBusy.value = ''
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
    await load(true, { background: true, includeExports: false })
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
    await load(true, { background: true, includeExports: true })
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

watch(itemFilter, (value) => {
  const query = { ...((route.query || {}) as Record<string, string>) }
  if (value === 'all') delete query.filter
  else query.filter = value
  void router.replace({ query })
})

watch(
  () => (route.query || {}).filter,
  (value) => {
    const next =
      typeof value === 'string' && (ITEM_FILTERS as readonly string[]).includes(value)
        ? (value as ItemFilter)
        : 'all'
    if (next !== itemFilter.value) itemFilter.value = next
  }
)

function handleBeforeUnload(event: BeforeUnloadEvent) {
  if (!hasUnsavedDrafts.value) return
  event.preventDefault()
  event.returnValue = ''
}

onMounted(() => {
  window.addEventListener('beforeunload', handleBeforeUnload)
  void load()
})

onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', handleBeforeUnload)
})
</script>

<style scoped>
.evidence-select.n-button {
  min-height: 32px;
}
.evidence-copy-link.n-button {
  min-width: 32px;
}
.evidence-open-original {
  min-height: 32px;
}
[data-item-heading]:focus {
  outline: 2px solid #2563eb;
  outline-offset: 4px;
  border-radius: 4px;
}
@media (max-width: 1024px) {
  .review-top-actions .n-button,
  [data-testid="review-primary-actions"] .n-button {
    min-height: 44px;
  }
  .review-decision-note :deep(.n-input__input-el) {
    font-size: 16px;
  }
  .review-decision-note.n-input {
    --n-height: 44px !important;
  }
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
  .evidence-select.n-button {
    min-height: 44px;
  }
  .evidence-copy-link.n-button {
    min-width: 44px;
  }
  .evidence-open-original {
    min-height: 44px;
    padding-left: 12px;
    padding-right: 12px;
  }
  .review-decision-save.n-button {
    min-height: 44px;
  }
  .review-export-empty :deep(.n-empty__description) {
    color: #6b7280;
  }
}
@media (max-width: 767.98px) {
  .review-conclusion {
    padding-top: 8px;
    padding-bottom: 8px;
  }
  .review-progress {
    display: none;
  }
  .review-filter-chips {
    flex-wrap: nowrap;
    overflow-x: auto;
    padding-bottom: 2px;
  }
}
.dark .review-export-empty :deep(.n-empty__description) {
  color: #9ca3af;
}
.review-export-empty :deep(.n-empty__description) {
  color: #6b7280;
}
</style>
