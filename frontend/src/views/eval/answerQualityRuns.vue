<template>
  <div class="p-4 md:p-6">
    <div class="mx-auto max-w-7xl">
      <PageBreadcrumb :show-back="true" class="mb-4" />
      <div class="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 class="text-2xl font-semibold tracking-tight text-slate-900 dark:text-slate-100 md:text-[28px]">
            {{ t('router.answerQualityRuns') }}
          </h1>
          <p class="mt-1 text-sm text-slate-500 dark:text-slate-400">
            {{ t('answerQualityEval.subtitle') }}
          </p>
        </div>
        <n-button secondary :loading="loading" data-testid="answer-quality-refresh" @click="loadAll">
          {{ t('answerQualityEval.common.refresh') }}
        </n-button>
      </div>

      <!-- 口径横幅：答案质量 ≠ 检索召回 ≠ 评分表评测 -->
      <n-alert type="info" :bordered="false" class="mb-4" data-testid="answer-quality-scope-banner">
        {{ t('answerQualityEval.scopeBanner') }}
      </n-alert>

      <n-alert v-if="loadError" type="error" class="mb-4" :bordered="false" data-testid="answer-quality-load-failed">
        {{ t('answerQualityEval.common.loadFailed') }}
        <n-button text type="primary" class="ml-2" @click="loadAll">
          {{ t('answerQualityEval.common.retry') }}
        </n-button>
      </n-alert>

      <!-- 过滤：状态 / 日期（UTC 日界）/ 用户（仅管理员可按用户过滤） -->
      <section
        class="mb-4 flex flex-wrap items-end gap-3 rounded-lg border border-slate-200 bg-white p-3 dark:border-slate-700 dark:bg-slate-900"
        data-testid="answer-quality-filters"
      >
        <div class="flex flex-col gap-1">
          <span class="text-xs font-medium text-slate-600 dark:text-slate-400">
            {{ t('answerQualityEval.filters.status') }}
          </span>
          <n-select
            v-model:value="statusFilter"
            :options="statusOptions"
            :aria-label="t('answerQualityEval.filters.status')"
            class="w-44"
            data-testid="answer-quality-status-filter"
            @update:value="applyFilter"
          />
        </div>
        <div class="flex flex-col gap-1">
          <span class="text-xs font-medium text-slate-600 dark:text-slate-400">
            {{ t('answerQualityEval.filters.dateRange') }}
          </span>
          <n-date-picker
            v-model:value="dateRange"
            type="daterange"
            clearable
            class="w-64"
            :aria-label="t('answerQualityEval.filters.dateRange')"
            data-testid="answer-quality-date-filter"
            @update:value="applyFilter"
          />
        </div>
        <div v-if="canFilterByUser" class="flex flex-col gap-1">
          <span class="text-xs font-medium text-slate-600 dark:text-slate-400">
            {{ t('answerQualityEval.filters.user') }}
          </span>
          <n-input-number
            v-model:value="userIdFilter"
            :min="1"
            :precision="0"
            :placeholder="t('answerQualityEval.filters.userPlaceholder')"
            :aria-label="t('answerQualityEval.filters.user')"
            class="w-40"
            data-testid="answer-quality-user-filter"
            @update:value="applyFilter"
          />
        </div>
        <n-button secondary data-testid="answer-quality-reset-filters" @click="resetFilters">
          {{ t('answerQualityEval.filters.reset') }}
        </n-button>
        <span class="ml-auto text-xs tabular-nums text-slate-500 dark:text-slate-400" data-testid="answer-quality-count">
          {{ t('answerQualityEval.filters.filteredCount', { total }) }}
        </span>
      </section>

      <n-spin :show="loading">
        <div
          v-if="trendError"
          class="mb-4 rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300"
          data-testid="answer-quality-trend-failed"
        >
          {{ t('answerQualityEval.list.trendLoadFailed') }}
        </div>

        <!-- 趋势：汇总 + 三维迷你折线 + 常驻数据表（不靠 hover 才能读值） -->
        <section
          v-if="trend && trend.summary.runs > 0"
          class="mb-4 rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900"
          data-testid="answer-quality-trend"
        >
          <div>
            <h2 class="text-sm font-semibold text-slate-900 dark:text-slate-100">
              {{ t('answerQualityEval.list.trendTitle') }}
            </h2>
            <p class="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
              {{ t('answerQualityEval.list.trendHint') }}
            </p>
          </div>

          <div class="mt-3 grid grid-cols-3 gap-3">
            <div class="rounded-lg border border-slate-200 p-3 dark:border-slate-700">
              <p class="text-xs text-slate-600 dark:text-slate-400">{{ t('answerQualityEval.list.trendRuns') }}</p>
              <p class="mt-1 text-xl font-semibold tabular-nums text-slate-900 dark:text-slate-100">
                {{ trend.summary.runs }}
              </p>
            </div>
            <div class="rounded-lg border border-slate-200 p-3 dark:border-slate-700">
              <p class="text-xs text-slate-600 dark:text-slate-400">{{ t('answerQualityEval.list.trendSucceeded') }}</p>
              <p class="mt-1 text-xl font-semibold tabular-nums text-emerald-700 dark:text-emerald-500">
                {{ trend.summary.succeeded_runs }}
              </p>
            </div>
            <div class="rounded-lg border border-slate-200 p-3 dark:border-slate-700">
              <p class="text-xs text-slate-600 dark:text-slate-400">{{ t('answerQualityEval.list.trendFailed') }}</p>
              <p class="mt-1 text-xl font-semibold tabular-nums text-red-600 dark:text-red-400">
                {{ trend.summary.failed_runs }}
              </p>
            </div>
          </div>

          <div class="mt-3 grid gap-3 md:grid-cols-3">
            <div
              v-for="metric in METRICS"
              :key="metric"
              class="rounded-lg border border-slate-200 p-3 dark:border-slate-700"
              :data-testid="`answer-quality-trend-metric-${metric}`"
            >
              <div class="flex items-baseline justify-between gap-2">
                <p class="text-xs text-slate-600 dark:text-slate-400">{{ metricLabel(metric) }}</p>
                <p class="text-sm font-semibold tabular-nums text-slate-900 dark:text-slate-100">
                  {{ formatMetric(trend.summary.averages[metric]) }}
                </p>
              </div>
              <svg
                viewBox="0 0 160 40"
                class="mt-2 h-10 w-full"
                :class="sparklineClass(metric)"
                role="img"
                :aria-label="t('answerQualityEval.list.trendAverage', { metric: metricLabel(metric) })"
                :data-testid="`answer-quality-sparkline-${metric}`"
              >
                <polyline
                  v-if="sparklines[metric].hasData"
                  :points="sparklines[metric].points"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="2"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                />
                <circle
                  v-if="sparklines[metric].hasData"
                  :cx="sparklines[metric].lastX"
                  :cy="sparklines[metric].lastY"
                  r="2.5"
                  fill="currentColor"
                />
              </svg>
            </div>
          </div>

          <div class="mt-4 overflow-x-auto">
            <table class="w-full border-collapse text-left text-xs" data-testid="answer-quality-trend-table">
              <thead>
                <tr class="border-b border-slate-200 dark:border-slate-700">
                  <th class="py-1 pr-3">{{ t('answerQualityEval.list.trendTableTime') }}</th>
                  <th class="py-1 pr-3">{{ t('answerQualityEval.list.columns.status') }}</th>
                  <th class="py-1 pr-3">{{ t('answerQualityEval.list.columns.mode') }}</th>
                  <th class="py-1 pr-3">{{ t('answerQualityEval.list.columns.judgeModel') }}</th>
                  <th class="py-1 pr-3">{{ t('answerQualityEval.list.columns.cases') }}</th>
                  <th v-for="metric in METRICS" :key="metric" class="py-1 pr-3">
                    {{ metricLabel(metric) }}
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="point in trend.points"
                  :key="point.id"
                  class="border-b border-slate-100 dark:border-slate-800"
                  :data-testid="`answer-quality-trend-row-${point.id}`"
                >
                  <td class="py-1 pr-3 whitespace-nowrap">{{ formatDate(point.created_at) }}</td>
                  <td class="py-1 pr-3">
                    <n-tag size="tiny" :bordered="false" :type="point.status === 'completed' ? 'success' : 'error'">
                      {{ t(`answerQualityEval.status.${point.status}`) }}
                    </n-tag>
                    <span v-if="point.error_code" class="ml-1 font-mono text-[10px] text-red-600 dark:text-red-400">
                      {{ point.error_code }}
                    </span>
                  </td>
                  <td class="py-1 pr-3">{{ t(`answerQualityEval.mode.${point.mode}`) }}</td>
                  <td class="py-1 pr-3 font-mono text-[11px]">{{ point.judge_model }}</td>
                  <td class="py-1 pr-3 tabular-nums">{{ point.case_count }}</td>
                  <td v-for="metric in METRICS" :key="metric" class="py-1 pr-3 tabular-nums">
                    {{ formatMetric(point.metrics[metric]) }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <!-- 空态 -->
        <div
          v-if="!loading && !loadError && rows.length === 0"
          class="flex flex-col items-center justify-center rounded-2xl border border-dashed border-slate-200 bg-white px-6 py-20 dark:border-slate-700 dark:bg-slate-900"
          data-testid="answer-quality-empty-state"
        >
          <div class="mb-5 flex h-16 w-16 items-center justify-center rounded-2xl border border-slate-200 bg-slate-50 dark:border-slate-700 dark:bg-slate-800">
            <n-icon size="30" class="text-slate-500 dark:text-slate-400">
              <DocumentTextOutline />
            </n-icon>
          </div>
          <h2 class="mb-2 text-lg font-semibold text-slate-800 dark:text-slate-100">
            {{ t('answerQualityEval.list.empty') }}
          </h2>
          <p class="max-w-md text-center text-sm leading-relaxed text-slate-500 dark:text-slate-400">
            {{ t('answerQualityEval.list.emptyHint') }}
          </p>
        </div>

        <!-- 运行历史列表 -->
        <section
          v-else-if="rows.length > 0"
          class="rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900"
          data-testid="answer-quality-runs-table"
        >
          <h2 class="text-sm font-semibold text-slate-900 dark:text-slate-100">
            {{ t('answerQualityEval.list.title') }}
          </h2>
          <div class="mt-3 overflow-x-auto">
            <table class="w-full border-collapse text-left text-xs">
              <thead>
                <tr class="border-b border-slate-200 dark:border-slate-700">
                  <th class="py-1 pr-3">{{ t('answerQualityEval.list.columns.time') }}</th>
                  <th class="py-1 pr-3">{{ t('answerQualityEval.list.columns.status') }}</th>
                  <th class="py-1 pr-3">{{ t('answerQualityEval.list.columns.mode') }}</th>
                  <th class="py-1 pr-3">{{ t('answerQualityEval.list.columns.judgeModel') }}</th>
                  <th class="py-1 pr-3">{{ t('answerQualityEval.list.columns.promptVersion') }}</th>
                  <th class="py-1 pr-3">{{ t('answerQualityEval.list.columns.cases') }}</th>
                  <th class="py-1 pr-3">{{ t('answerQualityEval.list.columns.user') }}</th>
                  <th v-for="metric in METRICS" :key="metric" class="py-1 pr-3">
                    {{ metricLabel(metric) }}
                  </th>
                  <th class="py-1">{{ t('answerQualityEval.list.columns.actions') }}</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="row in rows"
                  :key="row.id"
                  class="border-b border-slate-100 align-top dark:border-slate-800"
                  :data-testid="`answer-quality-row-${row.id}`"
                >
                  <td class="py-1 pr-3 whitespace-nowrap">{{ formatDate(row.created_at) }}</td>
                  <td class="py-1 pr-3">
                    <n-tag size="tiny" :bordered="false" :type="row.status === 'completed' ? 'success' : 'error'">
                      {{ t(`answerQualityEval.status.${row.status}`) }}
                    </n-tag>
                    <span v-if="row.error_code" class="mt-1 block font-mono text-[10px] text-red-600 dark:text-red-400">
                      {{ row.error_code }}
                    </span>
                  </td>
                  <td class="py-1 pr-3">{{ t(`answerQualityEval.mode.${row.mode}`) }}</td>
                  <td class="py-1 pr-3 font-mono text-[11px]">{{ row.judge_model }}</td>
                  <td class="py-1 pr-3 font-mono text-[11px]">{{ row.prompt_version }}</td>
                  <td class="py-1 pr-3 tabular-nums">{{ row.case_count }}</td>
                  <td class="py-1 pr-3 tabular-nums">{{ row.user_id }}</td>
                  <td v-for="metric in METRICS" :key="metric" class="py-1 pr-3 tabular-nums">
                    {{ formatMetric(row.metrics[metric]) }}
                  </td>
                  <td class="py-1">
                    <n-button
                      text
                      type="primary"
                      size="small"
                      :data-testid="`answer-quality-open-${row.id}`"
                      @click="openDetail(row)"
                    >
                      {{ t('answerQualityEval.common.viewDetail') }}
                    </n-button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <p class="mt-2 text-[11px] text-slate-500 dark:text-slate-400">
            {{ t('answerQualityEval.list.maskedNote') }}
          </p>
          <div class="mt-4 flex justify-end">
            <n-pagination
              v-model:page="page"
              :page-size="pageSize"
              :item-count="total"
              @update:page="loadAll"
            />
          </div>
        </section>
      </n-spin>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  NAlert,
  NButton,
  NDatePicker,
  NIcon,
  NInputNumber,
  NPagination,
  NSelect,
  NSpin,
  NTag
} from 'naive-ui'
import { DocumentTextOutline } from '@vicons/ionicons5'
import PageBreadcrumb from '@/components/common/PageBreadcrumb.vue'
import {
  ANSWER_QUALITY_METRICS,
  answerQualityEvalApi,
  type AnswerQualityMetric,
  type AnswerQualityRunListParams,
  type AnswerQualityRunSummary,
  type AnswerQualityStatusFilter,
  type AnswerQualityTrend
} from '@/api/answerQualityEval'
import { useUserStore } from '@/stores/user'
import { apiUnwrap } from '@/utils/apiUnwrap'
import { parseApiDate } from '@/utils/format'

const { t, locale } = useI18n()
const router = useRouter()
const userStore = useUserStore()

const METRICS = ANSWER_QUALITY_METRICS
const pageSize = 20

const rows = ref<AnswerQualityRunSummary[]>([])
const total = ref(0)
const page = ref(1)
const loading = ref(false)
const loadError = ref(false)
const trend = ref<AnswerQualityTrend | null>(null)
const trendError = ref(false)

const statusFilter = ref<AnswerQualityStatusFilter>('all')
const dateRange = ref<[number, number] | null>(null)
const userIdFilter = ref<number | null>(null)

/** 仅管理员可按运行归属用户过滤（普通成员看本组织全量即可）。 */
const canFilterByUser = computed(() => userStore.isAdmin)

const statusOptions = computed(() => [
  { label: t('answerQualityEval.status.all'), value: 'all' },
  { label: t('answerQualityEval.status.completed'), value: 'completed' },
  { label: t('answerQualityEval.status.failed'), value: 'failed' }
])

interface SparklineData {
  points: string
  lastX: number
  lastY: number
  hasData: boolean
}

const sparklines = computed<Record<AnswerQualityMetric, SparklineData>>(() => {
  const result = {} as Record<AnswerQualityMetric, SparklineData>
  const width = 160
  const height = 40
  const pad = 4
  for (const metric of METRICS) {
    const values: number[] = []
    for (const point of trend.value?.points ?? []) {
      const value = point.metrics[metric]
      if (typeof value === 'number' && Number.isFinite(value)) values.push(value)
    }
    if (values.length === 0) {
      result[metric] = { points: '', lastX: 0, lastY: 0, hasData: false }
      continue
    }
    const xAt = (index: number): number =>
      values.length === 1 ? width / 2 : pad + (index * (width - pad * 2)) / (values.length - 1)
    const yAt = (value: number): number => height - pad - value * (height - pad * 2)
    const coords = values.map((value, index) => `${xAt(index).toFixed(1)},${yAt(value).toFixed(1)}`)
    result[metric] = {
      points: coords.join(' '),
      lastX: xAt(values.length - 1),
      lastY: yAt(values[values.length - 1]),
      hasData: true
    }
  }
  return result
})

function metricLabel(metric: AnswerQualityMetric): string {
  return t(`answerQualityEval.metrics.${metric}`)
}

function sparklineClass(metric: AnswerQualityMetric): string {
  if (metric === 'faithfulness') return 'text-blue-600 dark:text-blue-400'
  if (metric === 'relevancy') return 'text-emerald-700 dark:text-emerald-500'
  return 'text-amber-600 dark:text-amber-400'
}

function formatMetric(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${(value * 100).toFixed(1)}%`
}

function formatDate(value: string | null): string {
  const date = parseApiDate(value)
  if (!date) return '—'
  return date.toLocaleString(locale.value)
}

/** 日期选择器 → 后端口径的 UTC 日（YYYY-MM-DD，与审计页一致）。 */
function toDateParam(timestamp: number): string {
  return new Date(timestamp).toISOString().slice(0, 10)
}

function filterParams(): Omit<AnswerQualityRunListParams, 'page' | 'page_size'> {
  const params: Omit<AnswerQualityRunListParams, 'page' | 'page_size'> = {}
  if (statusFilter.value !== 'all') params.status = statusFilter.value
  if (canFilterByUser.value && userIdFilter.value !== null) params.user_id = userIdFilter.value
  if (dateRange.value) {
    params.date_from = toDateParam(dateRange.value[0])
    params.date_to = toDateParam(dateRange.value[1])
  }
  return params
}

async function loadList(): Promise<void> {
  try {
    const res = await answerQualityEvalApi.list({
      ...filterParams(),
      page: page.value,
      page_size: pageSize
    })
    const data = apiUnwrap<{ items: AnswerQualityRunSummary[]; total: number }>(res)
    rows.value = data?.items ?? []
    total.value = data?.total ?? rows.value.length
  } catch {
    loadError.value = true
    rows.value = []
    total.value = 0
  }
}

async function loadTrend(): Promise<void> {
  trendError.value = false
  try {
    const res = await answerQualityEvalApi.trend({ ...filterParams(), limit: 200 })
    trend.value = apiUnwrap<AnswerQualityTrend>(res) ?? null
  } catch {
    trendError.value = true
    trend.value = null
  }
}

async function loadAll(): Promise<void> {
  loading.value = true
  loadError.value = false
  await Promise.all([loadList(), loadTrend()])
  loading.value = false
}

function applyFilter(): void {
  page.value = 1
  void loadAll()
}

function resetFilters(): void {
  statusFilter.value = 'all'
  dateRange.value = null
  userIdFilter.value = null
  applyFilter()
}

function openDetail(row: AnswerQualityRunSummary): void {
  void router.push(`/eval/answer-quality/runs/${row.id}`)
}

onMounted(loadAll)

defineExpose({
  rows,
  total,
  page,
  trend,
  trendError,
  loading,
  loadError,
  statusFilter,
  dateRange,
  userIdFilter,
  canFilterByUser,
  loadAll,
  applyFilter,
  resetFilters,
  openDetail,
  sparklines
})
</script>
