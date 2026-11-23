<template>
  <div class="eval-runs-page h-full min-h-0 overflow-y-auto bg-slate-50 p-4 lg:p-6">
    <div class="border-b border-slate-200 bg-white px-5 py-2 dark:border-slate-700 dark:bg-slate-900">
      <PageBreadcrumb :show-back="true" />
    </div>
    <div class="mx-auto max-w-7xl space-y-4">
      <header class="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
        <div>
          <h1 class="text-2xl font-semibold text-slate-900 dark:text-slate-100">{{ t('router.knowledgeEvalRuns') }}</h1>
          <p class="mt-1 text-sm text-slate-600 dark:text-slate-400">{{ t('evalRuns.subtitle') }}</p>
        </div>
        <n-button secondary :aria-label="t('evalRuns.refresh')" @click="loadAll">
          <template #icon><span aria-hidden="true">↻</span></template>
          {{ t('evalRuns.refresh') }}
        </n-button>
      </header>

      <!-- 过滤器单行：用例集哈希决定下方趋势 + 列表的口径，指标仅决定趋势图读哪一列。 -->
      <div class="flex flex-wrap items-center gap-3 rounded-lg border border-slate-200 bg-white p-3 dark:border-slate-700 dark:bg-slate-900">
        <label class="text-xs font-medium text-slate-600 dark:text-slate-400" for="eval-hash">
          {{ t('evalRuns.filterSet') }}
        </label>
        <n-select
          id="eval-hash"
          :value="selectedHash"
          :options="hashOptions"
          class="w-64"
          size="small"
          @update:value="onHashChange"
        />
        <label class="text-xs font-medium text-slate-600 dark:text-slate-400" for="eval-metric">
          {{ t('evalRuns.trend.metric') }}
        </label>
        <n-select
          id="eval-metric"
          :value="metric"
          :options="metricOptions"
          class="w-48"
          size="small"
          @update:value="onMetricChange"
        />
        <span class="text-xs tabular-nums text-slate-500 dark:text-slate-400">
          {{ t('evalRuns.filteredCount', { count: runs.length, total: listTotal }) }}
        </span>
      </div>

      <div v-if="error" class="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300" role="alert">
        <p class="font-medium">{{ t('evalRuns.loadFailed') }}</p>
        <p class="mt-1">{{ error }}</p>
        <n-button size="small" secondary class="mt-3" @click="loadAll">{{ t('evalRuns.retry') }}</n-button>
      </div>

      <template v-else>
        <!-- KPI 行：中性面 + 单一强调色，状态三重表达（文字 + 图标文本 + 颜色）。 -->
        <section v-if="trend" class="grid grid-cols-2 gap-3 md:grid-cols-4" :aria-label="t('evalRuns.trend.title')">
          <div class="rounded-lg border border-slate-200 bg-white p-3 dark:border-slate-700 dark:bg-slate-900">
            <p class="text-xs text-slate-600 dark:text-slate-400">{{ t('evalRuns.trend.runs') }}</p>
            <p class="mt-1 text-xl font-semibold tabular-nums text-slate-900 dark:text-slate-100">{{ trend.summary.runs }}</p>
          </div>
          <div class="rounded-lg border border-slate-200 bg-white p-3 dark:border-slate-700 dark:bg-slate-900">
            <p class="text-xs text-slate-600 dark:text-slate-400">{{ t('evalRuns.trend.succeededRuns') }}</p>
            <p class="mt-1 text-xl font-semibold tabular-nums text-emerald-700 dark:text-emerald-500">✓ {{ trend.summary.succeeded_runs }}</p>
          </div>
          <div class="rounded-lg border border-slate-200 bg-white p-3 dark:border-slate-700 dark:bg-slate-900">
            <p class="text-xs text-slate-600 dark:text-slate-400">{{ t('evalRuns.trend.failedRuns') }}</p>
            <p class="mt-1 text-xl font-semibold tabular-nums text-red-600 dark:text-red-400">! {{ trend.summary.failed_runs }}</p>
          </div>
          <div class="rounded-lg border border-slate-200 bg-white p-3 dark:border-slate-700 dark:bg-slate-900">
            <p class="text-xs text-slate-600 dark:text-slate-400">{{ t('evalRuns.trend.average', { metric: metricLabel }) }}</p>
            <p class="mt-1 text-xl font-semibold tabular-nums text-slate-900 dark:text-slate-100">{{ formatValue(trend.summary.averages[metric]) }}</p>
          </div>
        </section>

        <!-- 趋势：单系列折线 + 十字线/tooltip；下方常驻表格，值不被 hover 门控。 -->
        <section v-if="trend" class="rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900">
          <div class="flex items-center justify-between">
            <div>
              <h2 class="text-sm font-semibold text-slate-900 dark:text-slate-100">{{ t('evalRuns.trend.title') }}</h2>
              <p class="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{{ t('evalRuns.trend.subtitle') }}</p>
            </div>
            <span class="text-xs text-slate-500 dark:text-slate-400">{{ metricLabel }}</span>
          </div>
          <div v-if="chartPoints.length === 0" class="py-10 text-center text-sm text-slate-500 dark:text-slate-400">
            {{ t('evalRuns.trend.noData') }}
          </div>
          <div v-else ref="chartWrap" class="relative mt-3" data-test="eval-trend-chart">
            <svg
              :viewBox="`0 0 ${chartWidth} ${chartHeight}`"
              class="w-full"
              role="img"
              :aria-label="t('evalRuns.trend.chartLabel', { metric: metricLabel })"
            >
              <line
                v-for="tick in [0, 0.5, 1]"
                :key="tick"
                :x1="padLeft"
                :x2="chartWidth - padRight"
                :y1="yFor(tick)"
                :y2="yFor(tick)"
                class="eval-grid"
              />
              <text v-for="tick in [0, 0.5, 1]" :key="`label-${tick}`" :x="padLeft - 8" :y="yFor(tick) + 4" text-anchor="end" class="eval-axis">
                {{ formatValue(tick) }}
              </text>
              <path v-if="areaPath" :d="areaPath" class="eval-area" />
              <path :d="linePath" class="eval-line" />
              <line
                v-if="hovered !== null"
                :x1="xFor(hovered)"
                :x2="xFor(hovered)"
                :y1="padTop"
                :y2="chartHeight - padBottom"
                class="eval-crosshair"
              />
              <circle
                v-for="(point, index) in chartPoints"
                :key="point.id"
                :cx="xFor(index)"
                :cy="yFor(point.value)"
                r="4"
                class="eval-dot"
              />
              <rect
                v-for="(point, index) in chartPoints"
                :key="`hit-${point.id}`"
                :x="hitX(index)"
                :y="padTop"
                :width="hitWidth(index)"
                :height="chartHeight - padTop - padBottom"
                fill="transparent"
                tabindex="0"
                :aria-label="`${point.created_at}: ${formatValue(point.value)}`"
                @mouseenter="hovered = index"
                @mouseleave="hovered = null"
                @focus="hovered = index"
                @blur="hovered = null"
              />
              <text v-if="chartPoints.length" :x="padLeft" :y="chartHeight - 8" class="eval-axis">{{ shortTime(chartPoints[0].created_at) }}</text>
              <text v-if="chartPoints.length" :x="chartWidth - padRight" :y="chartHeight - 8" text-anchor="end" class="eval-axis">{{ shortTime(chartPoints.at(-1)!.created_at) }}</text>
              <text v-if="chartPoints.length" :x="xFor(chartPoints.length - 1)" :y="yFor(chartPoints.at(-1)!.value) - 12" text-anchor="end" class="eval-end-label">
                {{ formatValue(chartPoints.at(-1)!.value) }}
              </text>
            </svg>
            <div
              v-if="hovered !== null"
              class="pointer-events-none absolute z-10 rounded border border-slate-200 bg-white px-2 py-1 text-xs shadow-sm dark:border-slate-700 dark:bg-slate-800"
              :style="{ left: tooltipLeft, top: tooltipTop }"
              role="tooltip"
            >
              <span class="font-semibold tabular-nums text-slate-900 dark:text-slate-100">{{ formatValue(chartPoints[hovered].value) }}</span>
              <span class="ml-2 text-slate-500 dark:text-slate-400">{{ chartPoints[hovered].created_at }}</span>
            </div>
          </div>
          <details class="mt-3 text-xs" data-test="eval-trend-table">
            <summary class="cursor-pointer text-blue-600 dark:text-blue-500">{{ t('evalRuns.trend.tableView') }}</summary>
            <div class="mt-2 overflow-x-auto">
              <table class="w-full border-collapse text-left tabular-nums">
                <thead>
                  <tr class="border-b border-slate-200 dark:border-slate-700">
                    <th class="py-1 pr-3">{{ t('evalRuns.columns.time') }}</th>
                    <th class="py-1 pr-3">{{ metricLabel }}</th>
                    <th class="py-1">{{ t('evalRuns.columns.status') }}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="point in trend.points" :key="point.id" class="border-b border-slate-100 dark:border-slate-800">
                    <td class="py-1 pr-3">{{ point.created_at }}</td>
                    <td class="py-1 pr-3">{{ formatValue(point.metrics[metric]) }}</td>
                    <td class="py-1">{{ point.counts.errors > 0 ? t('evalRuns.statusFailed') : t('evalRuns.statusOk') }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </details>
        </section>

        <!-- 运行列表 -->
        <section class="rounded-lg border border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-900">
          <div v-if="visibleRuns.length === 0" class="p-10 text-center">
            <p class="text-sm font-medium text-slate-700 dark:text-slate-200">{{ t('evalRuns.emptyTitle') }}</p>
            <p class="mt-1 text-xs text-slate-500 dark:text-slate-400">{{ t('evalRuns.emptyDesc') }}</p>
          </div>
          <n-data-table
            v-else
            :columns="columns"
            :data="visibleRuns"
            :row-key="(row: EvalRunSummary) => row.id"
            :bordered="false"
            size="small"
            data-test="eval-run-list"
          />
        </section>

        <!-- 选中运行详情 -->
        <section v-if="detail" class="rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900" data-test="eval-run-detail">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h2 class="text-sm font-semibold text-slate-900 dark:text-slate-100">{{ t('evalRuns.detail.title') }}</h2>
              <p class="mt-0.5 font-mono text-xs text-slate-500 dark:text-slate-400">{{ detail.id }}</p>
            </div>
            <div class="flex flex-wrap gap-2">
              <n-button size="small" secondary @click="exportRun('json')">{{ t('evalRuns.exportJson') }}</n-button>
              <n-button size="small" secondary @click="exportRun('csv')">{{ t('evalRuns.exportCsv') }}</n-button>
              <n-button data-test="eval-replay" size="small" type="primary" :loading="replaying" @click="replay">{{ t('evalRuns.replay') }}</n-button>
            </div>
          </div>
          <dl class="mt-3 grid grid-cols-2 gap-3 text-xs md:grid-cols-4">
            <div><dt class="text-slate-500 dark:text-slate-400">{{ t('evalRuns.columns.topK') }}</dt><dd class="mt-0.5 font-semibold tabular-nums text-slate-900 dark:text-slate-100">{{ detail.top_k }}</dd></div>
            <div><dt class="text-slate-500 dark:text-slate-400">{{ t('evalRuns.columns.cases') }}</dt><dd class="mt-0.5 font-semibold tabular-nums text-slate-900 dark:text-slate-100">{{ detail.counts.cases }}</dd></div>
            <div><dt class="text-slate-500 dark:text-slate-400">{{ t('evalRuns.columns.evaluated') }}</dt><dd class="mt-0.5 font-semibold tabular-nums text-slate-900 dark:text-slate-100">{{ detail.counts.evaluated }}</dd></div>
            <div><dt class="text-slate-500 dark:text-slate-400">{{ t('evalRuns.columns.errors') }}</dt><dd class="mt-0.5 font-semibold tabular-nums text-slate-900 dark:text-slate-100">{{ detail.counts.errors }}</dd></div>
          </dl>
          <p class="mt-2 break-all font-mono text-xs text-slate-500 dark:text-slate-400">
            {{ t('evalRuns.columns.hash') }}: {{ detail.case_set_hash }}
          </p>
          <div class="mt-3 overflow-x-auto">
            <table class="w-full border-collapse text-left text-xs">
              <thead>
                <tr class="border-b border-slate-200 dark:border-slate-700">
                  <th class="py-1 pr-3">{{ t('evalRuns.columns.status') }}</th>
                  <th class="py-1 pr-3">{{ t('evalRuns.columns.name') }}</th>
                  <th class="py-1 pr-3">{{ t('evalRuns.columns.query') }}</th>
                  <th class="py-1">{{ t('evalRuns.columns.recall') }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(result, index) in detail.results" :key="index" class="border-b border-slate-100 dark:border-slate-800">
                  <td class="py-1 pr-3">{{ result.status === 'ok' ? t('evalRuns.statusOk') : t('evalRuns.statusFailed') }}<span v-if="result.error_code" class="ml-1 text-red-600 dark:text-red-400">! {{ result.error_code }}</span></td>
                  <td class="py-1 pr-3">{{ result.name }}</td>
                  <td class="py-1 pr-3 break-all">{{ result.query }}</td>
                  <td class="py-1 tabular-nums">{{ formatValue(result.recall_at_k) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, h, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import type { DataTableColumns } from 'naive-ui'
import { apiUnwrap } from '@/utils/apiUnwrap'
import { useDedupedMessage } from '@/utils/message'
import PageBreadcrumb from '@/components/common/PageBreadcrumb.vue'
import {
  exportEvalRun,
  getEvalRun,
  getEvalTrend,
  listEvalRuns,
  runEval,
  type EvalRunDetail,
  type EvalRunSummary,
  type EvalTrend,
  type EvalRunMetrics
} from '@/api/retrievalEval'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const message = useDedupedMessage()

const runs = ref<EvalRunSummary[]>([])
const listTotal = ref(0)
const trend = ref<EvalTrend | null>(null)
const detail = ref<EvalRunDetail | null>(null)
const error = ref('')
const replaying = ref(false)
const hovered = ref<number | null>(null)

const METRICS = ['recall_at_k', 'mrr', 'document_hit_at_k'] as const
type MetricKey = (typeof METRICS)[number]

const selectedHash = computed(() => String(route.query.case_set_hash || ''))
const metric = computed<MetricKey>(() => {
  const value = String(route.query.metric || 'recall_at_k')
  return (METRICS as readonly string[]).includes(value) ? (value as MetricKey) : 'recall_at_k'
})
const metricLabel = computed(() => t(`evalRuns.trend.metrics.${metric.value}`))
// 服务端按 case_set_hash 过滤；前端不再二次截断，否则超过 page_size 的运行会被静默隐藏。
const visibleRuns = computed(() => runs.value)

const hashOptions = computed(() => {
  const hashes = [...new Set(runs.value.map((run) => run.case_set_hash))]
  return [
    { label: t('evalRuns.allSets'), value: '' },
    ...hashes.map((hash) => ({ label: `${hash.slice(0, 12)}…`, value: hash }))
  ]
})
const metricOptions = computed(() =>
  METRICS.map((key) => ({ label: t(`evalRuns.trend.metrics.${key}`), value: key }))
)

function formatValue(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${(value * 100).toFixed(1)}%`
}

async function loadAll(): Promise<void> {
  error.value = ''
  try {
    const [listRes, trendRes] = await Promise.all([
      listEvalRuns({
        page: 1,
        page_size: 200,
        case_set_hash: selectedHash.value || undefined
      }),
      getEvalTrend({ case_set_hash: selectedHash.value || undefined, limit: 30 })
    ])
    const list = apiUnwrap<{ data: EvalRunSummary[]; total: number }>(listRes)
    runs.value = list?.data || []
    listTotal.value = list?.total ?? runs.value.length
    trend.value = apiUnwrap<EvalTrend>(trendRes) || null
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
    runs.value = []
    trend.value = null
  }
}

async function loadDetail(id: string): Promise<void> {
  if (!id) {
    detail.value = null
    return
  }
  try {
    detail.value = apiUnwrap<EvalRunDetail>(await getEvalRun(id)) || null
  } catch (e) {
    message.error(t('evalRuns.detail.loadFailed'))
    detail.value = null
  }
}

function onHashChange(value: string): void {
  router.replace({ query: { ...route.query, case_set_hash: value || undefined } })
}
function onMetricChange(value: string): void {
  router.replace({ query: { ...route.query, metric: value } })
}
async function selectRun(id: string): Promise<void> {
  await router.replace({ query: { ...route.query, run: id } })
}

const columns = computed<DataTableColumns<EvalRunSummary>>(() => [
  {
    title: t('evalRuns.columns.time'),
    key: 'created_at',
    render: (row) => h('span', { class: 'tabular-nums' }, row.created_at || '—')
  },
  {
    title: t('evalRuns.columns.hash'),
    key: 'case_set_hash',
    render: (row) => h('span', { class: 'font-mono text-xs' }, row.case_set_hash.slice(0, 12))
  },
  { title: t('evalRuns.columns.scope'), key: 'case_scope', render: (row) => row.case_scope === 'all' ? t('evalRuns.scopeAll') : t('evalRuns.scopeSubset') },
  {
    title: t('evalRuns.columns.topK'),
    key: 'top_k',
    render: (row) => h('span', { class: 'tabular-nums' }, String(row.top_k))
  },
  {
    title: t('evalRuns.columns.cases'),
    key: 'cases',
    render: (row) => h('span', { class: 'tabular-nums' }, String(row.counts.cases))
  },
  {
    title: t('evalRuns.columns.errors'),
    key: 'errors',
    render: (row) => h('span', { class: 'tabular-nums' }, String(row.counts.errors))
  },
  {
    title: t('evalRuns.columns.recall'),
    key: 'recall',
    render: (row) => h('span', { class: 'tabular-nums' }, formatValue(row.metrics.recall_at_k))
  },
  {
    title: t('evalRuns.columns.actions'),
    key: 'actions',
    render: (row) =>
      h(
        'button',
        {
          type: 'button',
          class: 'text-blue-600 underline dark:text-blue-500',
          onClick: () => selectRun(row.id)
        },
        t('evalRuns.viewDetail')
      )
  }
])

// Chart geometry: one axis, one series. Reactivity is keyed on metric/points only.
const chartWidth = 720
const chartHeight = 180
const padLeft = 44
const padRight = 16
const padTop = 12
const padBottom = 28
const chartPoints = computed(() =>
  (trend.value?.points || [])
    .map((point) => ({
      id: point.id,
      created_at: point.created_at || '',
      time: Date.parse(point.created_at || ''),
      value: point.metrics[metric.value]
    }))
    .filter(
      (point): point is { id: string; created_at: string; time: number; value: number } =>
        point.value !== null && Number.isFinite(point.time)
    )
)
// X 轴用真实时间定位；失败运行的 null 指标不进入折线，但参与时间跨度，
// 避免「缺一次运行就把后续点挤到相邻位置」的时间语义失真。
const timeBounds = computed(() => {
  const times = (trend.value?.points || [])
    .map((point) => Date.parse(point.created_at || ''))
    .filter(Number.isFinite)
  return { min: Math.min(...times), max: Math.max(...times) }
})
function xFor(index: number): number {
  const { min, max } = timeBounds.value
  const time = chartPoints.value[index]?.time
  if (time === undefined || max <= min) return (padLeft + chartWidth - padRight) / 2
  return padLeft + ((time - min) / (max - min)) * (chartWidth - padLeft - padRight)
}
function hitX(index: number): number {
  const points = chartPoints.value
  if (points.length <= 1) return Math.max(padLeft, xFor(index) - 12)
  const left = index === 0 ? padLeft : (xFor(index - 1) + xFor(index)) / 2
  return Math.max(padLeft, left)
}
function hitWidth(index: number): number {
  const points = chartPoints.value
  if (points.length <= 1) return 24
  const right = index === points.length - 1 ? chartWidth - padRight : (xFor(index) + xFor(index + 1)) / 2
  return Math.max(12, right - hitX(index))
}
function shortTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return `${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')} ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
}
function yFor(value: number): number {
  const clamped = Math.max(0, Math.min(1, value))
  return padTop + (1 - clamped) * (chartHeight - padTop - padBottom)
}
const linePath = computed(() =>
  chartPoints.value.map((point, index) => `${index === 0 ? 'M' : 'L'}${xFor(index)},${yFor(point.value)}`).join(' ')
)
const areaPath = computed(() => {
  if (!chartPoints.value.length) return ''
  const base = chartHeight - padBottom
  return `${linePath.value} L${xFor(chartPoints.value.length - 1)},${base} L${xFor(0)},${base} Z`
})
const tooltipLeft = computed(() => `${Math.min(Math.max(xFor(hovered.value ?? 0) - 40, 0), chartWidth - 160) / chartWidth * 100}%`)
const tooltipTop = computed(() => `${Math.max(yFor(chartPoints.value[hovered.value ?? 0]?.value ?? 0) - 44, 0)}px`)

async function exportRun(format: 'json' | 'csv'): Promise<void> {
  if (!detail.value) return
  try {
    const response = await exportEvalRun(detail.value.id, format)
    const blob = response.data as Blob
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `eval-run-${detail.value.id}.${format}`
    document.body.appendChild(anchor)
    anchor.click()
    document.body.removeChild(anchor)
    URL.revokeObjectURL(url)
  } catch {
    message.error(t('evalRuns.exportFailed'))
  }
}

async function replay(): Promise<void> {
  if (!detail.value || replaying.value) return
  replaying.value = true
  try {
    const report = apiUnwrap<{ run_id?: string }>(
      await runEval({ case_ids: detail.value.case_ids, top_k: detail.value.top_k, persist: true })
    )
    message.success(t('evalRuns.replaySuccess'))
    await loadAll()
    if (report?.run_id) await router.replace({ query: { ...route.query, run: report.run_id } })
  } catch {
    message.error(t('evalRuns.replayFailed'))
  } finally {
    replaying.value = false
  }
}

watch(
  () => [selectedHash.value, String(route.query.run || '')] as const,
  async ([, runId]) => {
    await Promise.all([loadAll(), loadDetail(runId)])
  }
)

onMounted(() => {
  void Promise.all([loadAll(), loadDetail(String(route.query.run || ''))])
})
</script>

<style scoped>
.eval-grid { stroke: #e2e8f0; stroke-width: 1; }
.eval-axis, .eval-end-label { fill: #64748b; font-size: 11px; font-variant-numeric: tabular-nums; }
.eval-line { fill: none; stroke: #2563eb; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
.eval-area { fill: #2563eb; opacity: 0.1; }
.eval-crosshair { stroke: #94a3b8; stroke-width: 1; }
.eval-dot { fill: #2563eb; stroke: #ffffff; stroke-width: 2; }
:global(.dark) .eval-grid { stroke: #334155; }
:global(.dark) .eval-axis, :global(.dark) .eval-end-label { fill: #94a3b8; }
:global(.dark) .eval-line, :global(.dark) .eval-dot { stroke: #3b82f6; }
:global(.dark) .eval-dot { fill: #3b82f6; stroke: #111827; }
:global(.dark) .eval-area { fill: #3b82f6; }
</style>
