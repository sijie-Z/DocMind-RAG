<template>
  <div class="p-4 md:p-6">
    <div class="mx-auto max-w-7xl">
      <PageBreadcrumb :show-back="true" class="mb-4" />
      <div class="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div class="min-w-0">
          <h1 class="text-2xl font-semibold tracking-tight text-slate-900 dark:text-slate-100 md:text-[28px]">
            {{ t('router.answerQualityRun') }}
          </h1>
          <p v-if="run" class="mt-1 break-all font-mono text-xs text-slate-500 dark:text-slate-400">
            {{ run.id }}
          </p>
        </div>
        <div class="flex flex-wrap gap-2">
          <n-button secondary data-testid="answer-quality-back" @click="backToList">
            {{ t('answerQualityEval.detail.backToList') }}
          </n-button>
          <n-button secondary :loading="loading" data-testid="answer-quality-detail-refresh" @click="load">
            {{ t('answerQualityEval.common.refresh') }}
          </n-button>
        </div>
      </div>

      <n-alert v-if="loadError" type="error" class="mb-4" :bordered="false" data-testid="answer-quality-detail-load-failed">
        {{ t('answerQualityEval.detail.loadFailed') }}
        <n-button text type="primary" class="ml-2" @click="load">
          {{ t('answerQualityEval.common.retry') }}
        </n-button>
      </n-alert>

      <n-spin :show="loading">
        <template v-if="run">
          <!-- 失败运行：显式机器码 +「没有分数」，绝不展示 0 分 -->
          <n-alert
            v-if="run.status === 'failed'"
            type="error"
            :bordered="false"
            class="mb-4"
            data-testid="answer-quality-run-failed"
          >
            <p class="font-medium">{{ t('answerQualityEval.detail.failedTitle') }}</p>
            <p class="mt-1 text-sm">{{ t('answerQualityEval.detail.failedUnavailable') }}</p>
            <p v-if="run.error_code" class="mt-1 font-mono text-xs">
              {{ t('answerQualityEval.detail.errorCode') }}: {{ run.error_code }}
            </p>
            <p class="mt-1 text-sm font-medium" data-testid="answer-quality-no-scores">
              {{ t('answerQualityEval.detail.noScores') }}
            </p>
          </n-alert>

          <n-alert type="info" :bordered="false" class="mb-4" data-testid="answer-quality-detail-scope-banner">
            {{ t('answerQualityEval.scopeBanner') }}
          </n-alert>

          <!-- 聚合指标：仅成功运行展示；失败运行 aggregate 为 null，不渲染任何分数 -->
          <section
            v-if="run.aggregate"
            class="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4"
            data-testid="answer-quality-aggregate"
          >
            <div
              v-for="metric in METRICS"
              :key="metric"
              class="rounded-lg border border-slate-200 bg-white p-3 dark:border-slate-700 dark:bg-slate-900"
              :data-testid="`answer-quality-aggregate-${metric}`"
            >
              <p class="text-xs text-slate-600 dark:text-slate-400">{{ metricLabel(metric) }}</p>
              <p class="mt-1 text-xl font-semibold tabular-nums text-slate-900 dark:text-slate-100">
                {{ formatMetric(run.aggregate[metric]) }}
              </p>
            </div>
            <div class="rounded-lg border border-slate-200 bg-white p-3 dark:border-slate-700 dark:bg-slate-900">
              <p class="text-xs text-slate-600 dark:text-slate-400">{{ t('answerQualityEval.detail.casesScored') }}</p>
              <p class="mt-1 text-xl font-semibold tabular-nums text-slate-900 dark:text-slate-100">
                {{ run.aggregate.cases_scored }}
              </p>
            </div>
          </section>

          <!-- 运行元数据：成功与失败都保留（失败运行也能回查模型/提示词版本） -->
          <section class="mb-4 rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900">
            <h2 class="text-sm font-semibold text-slate-900 dark:text-slate-100">
              {{ t('answerQualityEval.detail.metaTitle') }}
            </h2>
            <div class="mt-3 grid grid-cols-2 gap-3 text-xs md:grid-cols-4">
              <div>
                <p class="text-slate-600 dark:text-slate-400">{{ t('answerQualityEval.detail.judgeModel') }}</p>
                <p class="mt-1 font-mono text-[11px] text-slate-900 dark:text-slate-100" data-testid="answer-quality-judge-model">
                  {{ run.judge_model }}
                </p>
              </div>
              <div>
                <p class="text-slate-600 dark:text-slate-400">{{ t('answerQualityEval.detail.promptVersion') }}</p>
                <p class="mt-1 font-mono text-[11px] text-slate-900 dark:text-slate-100" data-testid="answer-quality-prompt-version">
                  {{ run.prompt_version }}
                </p>
              </div>
              <div>
                <p class="text-slate-600 dark:text-slate-400">{{ t('answerQualityEval.detail.mode') }}</p>
                <p class="mt-1 text-slate-900 dark:text-slate-100">{{ t(`answerQualityEval.mode.${run.mode}`) }}</p>
              </div>
              <div>
                <p class="text-slate-600 dark:text-slate-400">{{ t('answerQualityEval.detail.caseCount') }}</p>
                <p class="mt-1 tabular-nums text-slate-900 dark:text-slate-100">{{ run.case_count }}</p>
              </div>
              <div>
                <p class="text-slate-600 dark:text-slate-400">{{ t('answerQualityEval.detail.datasetHash') }}</p>
                <p class="mt-1 break-all font-mono text-[11px] text-slate-700 dark:text-slate-300">
                  {{ run.dataset_hash.slice(0, 16) }}…
                </p>
              </div>
              <div>
                <p class="text-slate-600 dark:text-slate-400">{{ t('answerQualityEval.detail.user') }}</p>
                <p class="mt-1 tabular-nums text-slate-900 dark:text-slate-100">{{ run.user_id }}</p>
              </div>
              <div>
                <p class="text-slate-600 dark:text-slate-400">{{ t('answerQualityEval.detail.createdAt') }}</p>
                <p class="mt-1 text-slate-900 dark:text-slate-100">{{ formatDate(run.created_at) }}</p>
              </div>
              <div>
                <p class="text-slate-600 dark:text-slate-400">{{ t('answerQualityEval.detail.completedAt') }}</p>
                <p class="mt-1 text-slate-900 dark:text-slate-100">{{ formatDate(run.completed_at) }}</p>
              </div>
            </div>
            <p v-if="run.aggregate" class="mt-3 text-[11px] text-slate-500 dark:text-slate-400">
              {{ t('answerQualityEval.detail.aggregateHint') }}
            </p>
          </section>

          <!-- 逐条结果：只含脱敏预览/理由/来源哈希 -->
          <section
            v-if="run.results"
            class="rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900"
            data-testid="answer-quality-results"
          >
            <h2 class="text-sm font-semibold text-slate-900 dark:text-slate-100">
              {{ t('answerQualityEval.detail.resultsTitle') }}
            </h2>
            <p class="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
              {{ t('answerQualityEval.detail.resultsHint') }}
            </p>
            <div class="mt-3 overflow-x-auto">
              <table class="w-full border-collapse text-left text-xs">
                <thead>
                  <tr class="border-b border-slate-200 dark:border-slate-700">
                    <th class="py-1 pr-3">{{ t('answerQualityEval.detail.caseId') }}</th>
                    <th v-for="metric in METRICS" :key="metric" class="py-1 pr-3">
                      {{ metricLabel(metric) }}
                    </th>
                    <th class="py-1 pr-3">{{ t('answerQualityEval.detail.reasons') }}</th>
                    <th class="py-1">{{ t('answerQualityEval.detail.sources') }}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="result in run.results"
                    :key="result.case_id"
                    class="border-b border-slate-100 align-top dark:border-slate-800"
                    :data-testid="`answer-quality-result-${result.case_id}`"
                  >
                    <td class="py-1 pr-3 font-mono">{{ result.case_id }}</td>
                    <td v-for="metric in METRICS" :key="metric" class="py-1 pr-3 tabular-nums">
                      {{ formatMetric(result.metrics[metric]) }}
                    </td>
                    <td class="py-1 pr-3">
                      <ul v-if="reasonEntries(result).length > 0" class="space-y-0.5">
                        <li
                          v-for="entry in reasonEntries(result)"
                          :key="entry.metric"
                          :data-testid="`answer-quality-reason-${result.case_id}-${entry.metric}`"
                        >
                          <span class="font-medium">{{ metricLabel(entry.metric) }}:</span>
                          {{ entry.reason }}
                        </li>
                      </ul>
                      <span v-else class="text-slate-500 dark:text-slate-400">
                        {{ t('answerQualityEval.detail.noReason') }}
                      </span>
                    </td>
                    <td class="py-1">
                      <ul v-if="result.source_refs.length > 0" class="space-y-0.5">
                        <li
                          v-for="(ref, index) in result.source_refs"
                          :key="`${result.case_id}-${index}`"
                          :data-testid="`answer-quality-source-${result.case_id}-${index}`"
                        >
                          <span class="font-mono text-[11px] text-slate-700 dark:text-slate-300">
                            {{ ref.masked_preview || '***' }}
                          </span>
                          <span class="ml-1 font-mono text-[10px] text-slate-400 dark:text-slate-500">
                            {{ ref.ref_sha256.slice(0, 12) }}…
                          </span>
                        </li>
                      </ul>
                      <span v-else class="text-slate-500 dark:text-slate-400">
                        {{ t('answerQualityEval.detail.noSources') }}
                      </span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p class="mt-2 text-[11px] text-slate-500 dark:text-slate-400">
              {{ t('answerQualityEval.list.maskedNote') }}
            </p>
          </section>
        </template>
      </n-spin>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { NAlert, NButton, NSpin } from 'naive-ui'
import PageBreadcrumb from '@/components/common/PageBreadcrumb.vue'
import {
  ANSWER_QUALITY_METRICS,
  answerQualityEvalApi,
  type AnswerQualityCaseResult,
  type AnswerQualityMetric,
  type AnswerQualityRunDetail
} from '@/api/answerQualityEval'
import { apiUnwrap } from '@/utils/apiUnwrap'
import { parseApiDate } from '@/utils/format'

const { t, locale } = useI18n()
const route = useRoute()
const router = useRouter()

const METRICS = ANSWER_QUALITY_METRICS
const runId = computed(() => String(route.params.id || ''))
const run = ref<AnswerQualityRunDetail | null>(null)
const loading = ref(false)
const loadError = ref(false)

interface ReasonEntry {
  metric: AnswerQualityMetric
  reason: string
}

function reasonEntries(result: AnswerQualityCaseResult): ReasonEntry[] {
  const entries: ReasonEntry[] = []
  for (const metric of METRICS) {
    const reason = result.reasons[metric]
    if (reason) entries.push({ metric, reason })
  }
  return entries
}

function metricLabel(metric: AnswerQualityMetric): string {
  return t(`answerQualityEval.metrics.${metric}`)
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

async function load(): Promise<void> {
  if (!runId.value) return
  loading.value = true
  loadError.value = false
  try {
    run.value = apiUnwrap<AnswerQualityRunDetail>(await answerQualityEvalApi.get(runId.value)) || null
  } catch {
    loadError.value = true
    run.value = null
  } finally {
    loading.value = false
  }
}

function backToList(): void {
  void router.push('/eval/answer-quality/runs')
}

onMounted(load)

defineExpose({ run, loading, loadError, load, backToList, reasonEntries })
</script>
