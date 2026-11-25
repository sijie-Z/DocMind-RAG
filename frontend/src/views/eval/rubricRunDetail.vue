<template>
  <div class="p-4 md:p-6">
    <div class="max-w-7xl mx-auto">
      <PageBreadcrumb :show-back="true" class="mb-4" />
      <div class="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 class="text-2xl font-semibold tracking-tight text-slate-900 dark:text-slate-100 md:text-[28px]">
            {{ t('router.evalRubricRun') }}
          </h1>
          <p v-if="run" class="mt-1 font-mono text-xs text-slate-500 dark:text-slate-400">{{ run.id }}</p>
        </div>
        <div class="flex flex-wrap gap-2">
          <n-button v-if="run" secondary @click="openRubric">{{ t('rubricEval.run.backToRubric') }}</n-button>
          <n-button secondary :loading="loading" @click="load">{{ t('rubricEval.common.refresh') }}</n-button>
        </div>
      </div>

      <n-alert v-if="loadError" type="error" class="mb-4" :bordered="false" data-testid="rubric-run-load-failed">
        {{ t('rubricEval.run.loadFailed') }}
        <n-button text type="primary" class="ml-2" @click="load">{{ t('rubricEval.common.retry') }}</n-button>
      </n-alert>

      <n-spin :show="loading">
        <template v-if="run">
          <!-- 失败运行：显式机器码与「没有分数」，绝不展示 0 分 -->
          <n-alert
            v-if="run.status === 'failed'"
            type="error"
            :bordered="false"
            class="mb-4"
            data-testid="rubric-run-failed"
          >
            <p class="font-medium">{{ t('rubricEval.run.failedTitle') }}</p>
            <p class="mt-1 text-sm">{{ failedExplanation }}</p>
            <p v-if="run.error_code" class="mt-1 font-mono text-xs">{{ t('rubricEval.run.errorCode') }}: {{ run.error_code }}</p>
          </n-alert>

          <section class="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
            <div class="rounded-lg border border-slate-200 bg-white p-3 dark:border-slate-700 dark:bg-slate-900">
              <p class="text-xs text-slate-600 dark:text-slate-400">{{ t('rubricEval.run.score') }}</p>
              <p class="mt-1 text-xl font-semibold tabular-nums text-slate-900 dark:text-slate-100" data-testid="rubric-run-score">
                {{ formatScore(run.aggregate?.score ?? null) }}
              </p>
            </div>
            <div class="rounded-lg border border-slate-200 bg-white p-3 dark:border-slate-700 dark:bg-slate-900">
              <p class="text-xs text-slate-600 dark:text-slate-400">{{ t('rubricEval.run.mode') }}</p>
              <p class="mt-1 text-sm font-semibold text-slate-900 dark:text-slate-100">
                {{ t(`rubricEval.mode.${run.mode}`) }}
              </p>
              <p class="mt-0.5 text-[11px] text-slate-500 dark:text-slate-400">
                {{ run.mode === 'judge' ? t('rubricEval.run.judgeModelNote', { model: run.judge_model }) : t('rubricEval.run.fakeModelNote') }}
              </p>
            </div>
            <div class="rounded-lg border border-slate-200 bg-white p-3 dark:border-slate-700 dark:bg-slate-900">
              <p class="text-xs text-slate-600 dark:text-slate-400">{{ t('rubricEval.run.versionLabel') }}</p>
              <p class="mt-1 text-sm font-semibold tabular-nums text-slate-900 dark:text-slate-100">
                v{{ run.rubric_version_no }} · {{ run.case_count }} {{ t('rubricEval.run.casesUnit') }}
              </p>
            </div>
            <div class="rounded-lg border border-slate-200 bg-white p-3 dark:border-slate-700 dark:bg-slate-900">
              <p class="text-xs text-slate-600 dark:text-slate-400">{{ t('rubricEval.run.datasetHash') }}</p>
              <p class="mt-1 break-all font-mono text-[11px] text-slate-700 dark:text-slate-300">
                {{ run.dataset_hash.slice(0, 16) }}…
              </p>
            </div>
          </section>

          <!-- 逐问题通过率 -->
          <section
            v-if="run.aggregate"
            class="mb-4 rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900"
            data-testid="rubric-question-table"
          >
            <h2 class="text-sm font-semibold text-slate-900 dark:text-slate-100">{{ t('rubricEval.run.questionsTitle') }}</h2>
            <p class="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{{ t('rubricEval.run.questionsHint') }}</p>
            <div class="mt-3 overflow-x-auto">
              <table class="w-full border-collapse text-left text-xs">
                <thead>
                  <tr class="border-b border-slate-200 dark:border-slate-700">
                    <th class="py-1 pr-3">{{ t('rubricEval.detail.itemKey') }}</th>
                    <th class="py-1 pr-3">{{ t('rubricEval.detail.itemQuestion') }}</th>
                    <th class="py-1 pr-3">{{ t('rubricEval.detail.itemWeight') }}</th>
                    <th class="py-1 pr-3">{{ t('rubricEval.run.passRate') }}</th>
                    <th class="py-1">{{ t('rubricEval.run.verdictCounts') }}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="row in run.aggregate.questions"
                    :key="row.key"
                    class="border-b border-slate-100 dark:border-slate-800"
                    :data-testid="`rubric-question-${row.key}`"
                  >
                    <td class="py-1 pr-3 font-mono">{{ row.key }}</td>
                    <td class="py-1 pr-3">{{ row.question }}</td>
                    <td class="py-1 pr-3 tabular-nums">{{ row.weight }}</td>
                    <td class="py-1 pr-3 tabular-nums">{{ formatScore(row.pass_rate) }}</td>
                    <td class="py-1 tabular-nums">
                      {{ t('rubricEval.run.verdictCountsValue', {
                        yes: row.verdict_counts.yes,
                        no: row.verdict_counts.no,
                        unsure: row.verdict_counts.unsure
                      }) }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>

          <!-- 校准面板 -->
          <section
            class="mb-4 rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900"
            data-testid="rubric-calibration-panel"
          >
            <h2 class="text-sm font-semibold text-slate-900 dark:text-slate-100">{{ t('rubricEval.calibration.title') }}</h2>
            <template v-if="run.aggregate?.calibration">
              <p class="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{{ t('rubricEval.calibration.subtitle') }}</p>
              <div class="mt-3 grid grid-cols-2 gap-3 md:grid-cols-3">
                <div>
                  <p class="text-xs text-slate-600 dark:text-slate-400">{{ t('rubricEval.calibration.agreement') }}</p>
                  <p class="mt-1 text-xl font-semibold tabular-nums text-slate-900 dark:text-slate-100" data-testid="rubric-calibration-agreement">
                    {{ formatScore(run.aggregate.calibration.agreement_rate) }}
                  </p>
                </div>
                <div>
                  <p class="text-xs text-slate-600 dark:text-slate-400">{{ t('rubricEval.calibration.pairs') }}</p>
                  <p class="mt-1 text-sm tabular-nums text-slate-900 dark:text-slate-100">
                    {{ run.aggregate.calibration.pairs_agreed }} / {{ run.aggregate.calibration.pairs_compared }}
                  </p>
                </div>
                <div>
                  <p class="text-xs text-slate-600 dark:text-slate-400">{{ t('rubricEval.calibration.casesWithGold') }}</p>
                  <p class="mt-1 text-sm tabular-nums text-slate-900 dark:text-slate-100">
                    {{ run.aggregate.calibration.cases_with_gold }} / {{ run.aggregate.calibration.cases_total }}
                  </p>
                </div>
              </div>
              <div class="mt-4 overflow-x-auto">
                <table class="border-collapse text-left text-xs" data-testid="rubric-confusion-table">
                  <caption class="mb-1 text-left text-[11px] text-slate-500 dark:text-slate-400">
                    {{ t('rubricEval.calibration.confusionHint') }}
                  </caption>
                  <thead>
                    <tr class="border-b border-slate-200 dark:border-slate-700">
                      <th class="py-1 pr-3">{{ t('rubricEval.calibration.judgeRow') }}</th>
                      <th v-for="label in run.aggregate.calibration.labels" :key="label" class="py-1 pr-3">
                        {{ t(`rubricEval.verdict.${label}`) }}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr
                      v-for="judge in run.aggregate.calibration.labels"
                      :key="judge"
                      class="border-b border-slate-100 dark:border-slate-800"
                    >
                      <td class="py-1 pr-3 font-medium">{{ t(`rubricEval.verdict.${judge}`) }}</td>
                      <td v-for="gold in run.aggregate.calibration.labels" :key="gold" class="py-1 pr-3 tabular-nums">
                        {{ run.aggregate.calibration.confusion[judge]?.[gold] ?? 0 }}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <p class="mt-2 text-[11px] text-slate-500 dark:text-slate-400">{{ t('rubricEval.calibration.note') }}</p>
            </template>
            <div v-else-if="run.status === 'completed'" class="mt-2" data-testid="rubric-calibration-missing-gold">
              <p class="text-sm font-medium text-slate-700 dark:text-slate-200">{{ t('rubricEval.calibration.missingTitle') }}</p>
              <p class="mt-1 text-xs text-slate-500 dark:text-slate-400">{{ t('rubricEval.calibration.missingHint') }}</p>
            </div>
            <p v-else class="mt-2 text-xs text-slate-500 dark:text-slate-400">
              {{ t('rubricEval.calibration.unavailableOnFailure') }}
            </p>
          </section>

          <!-- 逐条结果 -->
          <section
            v-if="run.results"
            class="rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900"
            data-testid="rubric-run-results"
          >
            <h2 class="text-sm font-semibold text-slate-900 dark:text-slate-100">{{ t('rubricEval.run.resultsTitle') }}</h2>
            <div class="mt-3 overflow-x-auto">
              <table class="w-full border-collapse text-left text-xs">
                <thead>
                  <tr class="border-b border-slate-200 dark:border-slate-700">
                    <th class="py-1 pr-3">{{ t('rubricEval.run.caseId') }}</th>
                    <th class="py-1 pr-3">{{ t('rubricEval.run.caseName') }}</th>
                    <th class="py-1 pr-3">{{ t('rubricEval.run.caseScore') }}</th>
                    <th class="py-1">{{ t('rubricEval.run.caseVerdicts') }}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="result in run.results"
                    :key="result.case_id"
                    class="border-b border-slate-100 align-top dark:border-slate-800"
                    :data-testid="`rubric-result-${result.case_id}`"
                  >
                    <td class="py-1 pr-3 font-mono">{{ result.case_id }}</td>
                    <td class="py-1 pr-3">{{ result.name || '—' }}</td>
                    <td class="py-1 pr-3 tabular-nums">{{ formatScore(result.score) }}</td>
                    <td class="py-1">
                      <div class="flex flex-wrap gap-1">
                        <n-tag
                          v-for="item in result.items"
                          :key="item.key"
                          size="tiny"
                          :bordered="false"
                          :data-testid="`rubric-verdict-${result.case_id}-${item.key}`"
                        >
                          {{ item.key }}: {{ t(`rubricEval.verdict.${item.verdict}`) }}
                        </n-tag>
                      </div>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p class="mt-2 text-[11px] text-slate-500 dark:text-slate-400">{{ t('rubricEval.run.resultsNote') }}</p>
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
import { NAlert, NButton, NSpin, NTag } from 'naive-ui'
import PageBreadcrumb from '@/components/common/PageBreadcrumb.vue'
import { rubricEvalApi, type RubricRunDetail } from '@/api/rubricEval'
import { apiUnwrap } from '@/utils/apiUnwrap'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()

const runId = computed(() => String(route.params.id || ''))
const run = ref<RubricRunDetail | null>(null)
const loading = ref(false)
const loadError = ref(false)

const failedExplanation = computed(() => {
  if (!run.value) return ''
  return run.value.error_code === 'JUDGE_CONTRACT_VIOLATION'
    ? t('rubricEval.run.failedContract')
    : t('rubricEval.run.failedUnavailable')
})

function formatScore(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${(value * 100).toFixed(1)}%`
}

async function load(): Promise<void> {
  if (!runId.value) return
  loading.value = true
  loadError.value = false
  try {
    run.value = apiUnwrap<RubricRunDetail>(await rubricEvalApi.getRun(runId.value)) || null
  } catch {
    loadError.value = true
    run.value = null
  } finally {
    loading.value = false
  }
}

function openRubric(): void {
  if (!run.value) return
  void router.push(`/eval/rubrics/${run.value.rubric_id}`)
}

onMounted(load)

defineExpose({ run, load })
</script>
