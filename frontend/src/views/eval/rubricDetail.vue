<template>
  <div class="p-4 md:p-6">
    <div class="max-w-7xl mx-auto">
      <PageBreadcrumb :show-back="true" class="mb-4" />
      <div class="mb-6 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div class="min-w-0">
          <h1 class="truncate text-2xl font-semibold tracking-tight text-slate-900 dark:text-slate-100 md:text-[28px]">
            {{ detail?.name || t('router.evalRubrics') }}
          </h1>
          <p v-if="detail?.description" class="mt-1 text-sm text-slate-500 dark:text-slate-400">
            {{ detail.description }}
          </p>
          <div class="mt-2 flex flex-wrap items-center gap-1">
            <n-tag
              v-if="detail"
              :type="detail.status === 'archived' ? 'default' : 'success'"
              size="small"
              data-testid="rubric-detail-status"
            >
              {{ detail.status === 'archived' ? t('rubricEval.common.statusArchived') : t('rubricEval.common.statusActive') }}
            </n-tag>
            <n-tag v-if="detail" size="small" :bordered="false">
              {{ t('rubricEval.library.versionTag', { n: detail.latest_version }) }}
            </n-tag>
          </div>
        </div>
        <div class="flex flex-wrap gap-2">
          <n-button secondary :loading="loading" @click="loadAll">
            {{ t('rubricEval.common.refresh') }}
          </n-button>
          <n-button
            type="primary"
            :disabled="archiveReadOnly || !selectedVersionRow"
            data-testid="rubric-run-open"
            @click="openRun"
          >
            {{ t('rubricEval.detail.runAction') }}
          </n-button>
          <n-popconfirm
            v-if="detail && !archiveReadOnly"
            :positive-button-props="{ loading: archiving, disabled: archiving }"
            @positive-click="archiveRubric"
          >
            <template #trigger>
              <n-button secondary type="warning" :loading="archiving" data-testid="rubric-archive">
                {{ t('rubricEval.detail.archive') }}
              </n-button>
            </template>
            {{ t('rubricEval.detail.archiveConfirm') }}
          </n-popconfirm>
        </div>
      </div>

      <n-alert type="info" :bordered="false" class="mb-4" data-testid="rubric-judge-disclaimer">
        {{ t('rubricEval.detail.judgeDisclaimer') }}
      </n-alert>

      <n-alert v-if="loadError" type="error" class="mb-4" :bordered="false">
        {{ t('rubricEval.detail.loadFailed') }}
        <n-button text type="primary" class="ml-2" @click="loadAll">{{ t('rubricEval.common.retry') }}</n-button>
      </n-alert>
      <n-alert
        v-if="detail && archiveReadOnly"
        type="info"
        class="mb-4"
        :bordered="false"
        data-testid="rubric-archived-banner"
      >
        {{ t('rubricEval.detail.archivedBanner') }}
      </n-alert>

      <n-spin :show="loading">
        <div class="grid gap-4 lg:grid-cols-[320px,1fr]">
          <!-- 版本历史 -->
          <section class="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900">
            <div class="mb-3 flex items-center justify-between">
              <h2 class="text-sm font-semibold text-slate-900 dark:text-slate-100">
                {{ t('rubricEval.detail.versionHistory') }}
              </h2>
              <span class="text-xs tabular-nums text-slate-500 dark:text-slate-400">
                {{ t('rubricEval.detail.versionCount', { n: versions.length }) }}
              </span>
            </div>
            <div v-if="versions.length === 0" class="text-xs text-slate-500 dark:text-slate-400" data-testid="rubric-versions-empty">
              {{ t('rubricEval.detail.versionsEmpty') }}
            </div>
            <template v-else>
              <ul class="space-y-1" data-testid="rubric-version-list">
                <li
                  v-for="row in versions"
                  :key="row.id"
                  class="flex items-center justify-between gap-2 rounded-lg border border-transparent px-2 py-1.5"
                  :class="row.version_no === selectedVersion ? 'border-blue-200 bg-blue-50 dark:border-blue-900 dark:bg-blue-950/40' : ''"
                >
                  <n-button
                    text
                    type="primary"
                    size="small"
                    :data-testid="`rubric-version-select-${row.version_no}`"
                    @click="selectVersion(row.version_no)"
                  >
                    v{{ row.version_no }}
                  </n-button>
                  <span class="text-[11px] tabular-nums text-slate-500 dark:text-slate-400">
                    {{ row.item_count }} {{ t('rubricEval.detail.itemsUnit') }}
                  </span>
                  <span v-if="row.source === 'bootstrap'" class="text-[11px] text-amber-700 dark:text-amber-500">
                    {{ t('rubricEval.detail.sourceBootstrap') }}
                  </span>
                </li>
              </ul>
              <div class="mt-3 flex flex-col gap-2">
                <div class="flex items-center gap-2">
                  <n-button
                    size="small"
                    secondary
                    :disabled="versions.length < 2"
                    data-testid="rubric-diff-open"
                    @click="openDiff"
                  >
                    {{ t('rubricEval.detail.diffAction') }}
                  </n-button>
                  <span class="text-[11px] text-slate-500 dark:text-slate-400">
                    {{ t('rubricEval.detail.diffHint') }}
                  </span>
                </div>
                <n-button
                  size="small"
                  secondary
                  :disabled="archiveReadOnly"
                  data-testid="rubric-new-version-open"
                  @click="openNewVersion"
                >
                  {{ t('rubricEval.detail.newVersionAction') }}
                </n-button>
              </div>
            </template>
          </section>

          <!-- 版本内容 -->
          <section class="min-w-0 rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900">
            <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
              <h2 class="text-sm font-semibold text-slate-900 dark:text-slate-100">
                {{ t('rubricEval.detail.versionContent') }}
                <span v-if="selectedVersionRow" class="ml-1 text-xs font-normal text-slate-500 dark:text-slate-400">
                  v{{ selectedVersionRow.version_no }}
                </span>
              </h2>
              <n-tag
                v-if="selectedVersionRow && selectedVersionRow.intact === false"
                type="error"
                size="small"
                data-testid="rubric-version-corrupt"
              >
                {{ t('rubricEval.detail.versionCorrupt') }}
              </n-tag>
            </div>
            <p v-if="selectedVersionRow?.change_note" class="mb-2 text-xs text-slate-500 dark:text-slate-400">
              {{ t('rubricEval.detail.changeNoteLabel') }}: {{ selectedVersionRow.change_note }}
            </p>
            <div v-if="!selectedVersionRow" class="text-xs text-slate-500 dark:text-slate-400">
              {{ t('rubricEval.detail.versionSelectHint') }}
            </div>
            <div v-else class="overflow-x-auto">
              <table class="w-full border-collapse text-left text-sm" data-testid="rubric-items-table">
                <thead>
                  <tr class="border-b border-slate-200 dark:border-slate-700">
                    <th class="py-1.5 pr-3 text-xs font-medium text-slate-500 dark:text-slate-400">
                      {{ t('rubricEval.detail.itemKey') }}
                    </th>
                    <th class="py-1.5 pr-3 text-xs font-medium text-slate-500 dark:text-slate-400">
                      {{ t('rubricEval.detail.itemQuestion') }}
                    </th>
                    <th class="py-1.5 pr-3 text-xs font-medium text-slate-500 dark:text-slate-400">
                      {{ t('rubricEval.detail.itemWeight') }}
                    </th>
                    <th class="py-1.5 text-xs font-medium text-slate-500 dark:text-slate-400">
                      {{ t('rubricEval.detail.itemStrictness') }}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="item in selectedVersionRow.items || []"
                    :key="item.key"
                    class="border-b border-slate-100 align-top dark:border-slate-800"
                    :data-testid="`rubric-item-${item.key}`"
                  >
                    <td class="py-1.5 pr-3 font-mono text-xs text-slate-700 dark:text-slate-300">{{ item.key }}</td>
                    <td class="py-1.5 pr-3 text-xs text-slate-700 dark:text-slate-300">{{ item.question }}</td>
                    <td class="py-1.5 pr-3 text-xs tabular-nums text-slate-700 dark:text-slate-300">{{ item.weight }}</td>
                    <td class="py-1.5 text-xs">
                      <n-tag size="tiny" :bordered="false">
                        {{ t(`rubricEval.strictness.${item.strictness}`) }}
                      </n-tag>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>
        </div>

        <!-- 运行历史 -->
        <section class="mt-4 rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900">
          <div class="mb-3 flex items-center justify-between">
            <h2 class="text-sm font-semibold text-slate-900 dark:text-slate-100">
              {{ t('rubricEval.detail.runsTitle') }}
            </h2>
            <n-button text size="small" type="primary" @click="loadRuns">
              {{ t('rubricEval.common.refresh') }}
            </n-button>
          </div>
          <p v-if="runs.length === 0" class="text-xs text-slate-500 dark:text-slate-400" data-testid="rubric-runs-empty">
            {{ t('rubricEval.detail.runsEmpty') }}
          </p>
          <div v-else class="overflow-x-auto">
            <table class="w-full border-collapse text-left text-xs" data-testid="rubric-runs-table">
              <thead>
                <tr class="border-b border-slate-200 dark:border-slate-700">
                  <th class="py-1 pr-3">{{ t('rubricEval.detail.runTime') }}</th>
                  <th class="py-1 pr-3">{{ t('rubricEval.detail.runMode') }}</th>
                  <th class="py-1 pr-3">{{ t('rubricEval.detail.runVersion') }}</th>
                  <th class="py-1 pr-3">{{ t('rubricEval.detail.runScore') }}</th>
                  <th class="py-1 pr-3">{{ t('rubricEval.detail.runStatus') }}</th>
                  <th class="py-1"></th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="run in runs" :key="run.id" class="border-b border-slate-100 dark:border-slate-800">
                  <td class="py-1 pr-3 tabular-nums">{{ formatDate(run.created_at) }}</td>
                  <td class="py-1 pr-3">{{ t(`rubricEval.mode.${run.mode}`) }}</td>
                  <td class="py-1 pr-3 tabular-nums">v{{ run.rubric_version_no }}</td>
                  <td class="py-1 pr-3 tabular-nums">{{ formatScore(run.score) }}</td>
                  <td class="py-1 pr-3">
                    <span v-if="run.status === 'failed'" class="text-red-600 dark:text-red-400">
                      ! {{ run.error_code || t('rubricEval.run.statusFailed') }}
                    </span>
                    <span v-else>{{ t('rubricEval.run.statusCompleted') }}</span>
                  </td>
                  <td class="py-1 text-right">
                    <n-button text type="primary" size="small" @click="openRunDetail(run.id)">
                      {{ t('rubricEval.detail.runOpen') }}
                    </n-button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
      </n-spin>
    </div>

    <!-- 新版本（编辑=新增不可变版本） -->
    <n-modal
      :show="newVersionVisible"
      preset="card"
      :title="t('rubricEval.detail.newVersionAction')"
      class="max-w-2xl"
      data-testid="rubric-new-version-modal"
      @update:show="(value: boolean) => (!value ? (newVersionVisible = false) : undefined)"
    >
      <div class="flex flex-col gap-3">
        <div v-for="(item, index) in versionForm.items" :key="index" class="rounded-lg border border-slate-200 p-2 dark:border-slate-700">
          <n-input
            v-model:value="item.question"
            :placeholder="t('rubricEval.library.questionPlaceholder')"
            maxlength="300"
            :data-testid="`rubric-nv-question-${index}`"
          />
          <div class="mt-2 flex flex-col gap-2 sm:flex-row">
            <n-input v-model:value="item.key" maxlength="32" class="sm:w-44" :placeholder="t('rubricEval.library.itemKeyPlaceholder')" />
            <n-input-number v-model:value="item.weight" :min="1" :max="10" class="sm:w-32" />
            <n-select v-model:value="item.strictness" :options="strictnessOptions" class="sm:w-40" />
            <n-button text type="error" @click="versionForm.items.splice(index, 1)">
              {{ t('rubricEval.library.removeItem') }}
            </n-button>
          </div>
        </div>
        <n-button text type="primary" size="small" @click="addVersionItem">
          {{ t('rubricEval.library.addItem') }}
        </n-button>
        <n-input
          v-model:value="versionForm.changeNote"
          :placeholder="t('rubricEval.library.changeNotePlaceholder')"
          maxlength="200"
        />
        <n-alert v-if="versionError" type="error" :bordered="false" data-testid="rubric-new-version-error">
          {{ versionError }}
        </n-alert>
      </div>
      <template #footer>
        <div class="flex justify-end gap-2">
          <n-button quaternary @click="newVersionVisible = false">{{ t('rubricEval.common.cancel') }}</n-button>
          <n-button type="primary" :loading="savingVersion" data-testid="rubric-new-version-submit" @click="submitNewVersion">
            {{ t('rubricEval.detail.newVersionSubmit') }}
          </n-button>
        </div>
      </template>
    </n-modal>

    <!-- 只读 diff -->
    <n-modal
      :show="diffVisible"
      preset="card"
      :title="t('rubricEval.detail.diffTitle')"
      class="max-w-3xl"
      data-testid="rubric-diff-modal"
      @update:show="(value: boolean) => (!value ? (diffVisible = false) : undefined)"
    >
      <div class="flex flex-col gap-3">
        <div class="flex flex-wrap items-end gap-3">
          <div>
            <div class="mb-1 text-xs text-slate-500 dark:text-slate-400">{{ t('rubricEval.detail.diffBase') }}</div>
            <n-select v-model:value="diffBase" :options="versionOptions" class="w-32" size="small" data-testid="rubric-diff-base" @update:value="runDiff" />
          </div>
          <div>
            <div class="mb-1 text-xs text-slate-500 dark:text-slate-400">{{ t('rubricEval.detail.diffTarget') }}</div>
            <n-select v-model:value="diffTarget" :options="versionOptions" class="w-32" size="small" data-testid="rubric-diff-target" @update:value="runDiff" />
          </div>
          <n-button size="small" secondary :loading="diffLoading" data-testid="rubric-diff-run" @click="runDiff">
            {{ t('rubricEval.detail.diffRun') }}
          </n-button>
        </div>
        <n-alert v-if="diffError" type="error" :bordered="false" data-testid="rubric-diff-error">{{ diffError }}</n-alert>
        <n-spin :show="diffLoading">
          <div v-if="diff">
            <p class="mb-2 text-xs text-slate-600 dark:text-slate-300" data-testid="rubric-diff-summary">
              {{
                t('rubricEval.detail.diffSummary', {
                  added: diff.summary.added,
                  removed: diff.summary.removed,
                  changed: diff.summary.changed,
                  unchanged: diff.summary.unchanged
                })
              }}
            </p>
            <p v-if="diff.identical" class="mb-2 text-xs text-emerald-700 dark:text-emerald-500" data-testid="rubric-diff-identical">
              {{ t('rubricEval.detail.diffIdentical') }}
            </p>
            <div v-if="diff.added.length" class="mb-2" data-testid="rubric-diff-added">
              <div class="text-xs font-medium text-emerald-700 dark:text-emerald-500">+ {{ t('rubricEval.detail.diffAdded') }}</div>
              <div v-for="item in diff.added" :key="item.key" class="ml-3 text-xs text-slate-700 dark:text-slate-300">
                <span class="font-mono">{{ item.key }}</span> · {{ item.question }}
              </div>
            </div>
            <div v-if="diff.removed.length" class="mb-2" data-testid="rubric-diff-removed">
              <div class="text-xs font-medium text-red-600 dark:text-red-400">− {{ t('rubricEval.detail.diffRemoved') }}</div>
              <div v-for="item in diff.removed" :key="item.key" class="ml-3 text-xs text-slate-700 dark:text-slate-300">
                <span class="font-mono">{{ item.key }}</span> · {{ item.question }}
              </div>
            </div>
            <div v-if="diff.changed.length" data-testid="rubric-diff-changed">
              <div class="text-xs font-medium text-amber-700 dark:text-amber-500">~ {{ t('rubricEval.detail.diffChanged') }}</div>
              <div
                v-for="entry in diff.changed"
                :key="entry.key"
                class="ml-3 mb-1 text-xs text-slate-700 dark:text-slate-300"
                :data-testid="`rubric-diff-changed-${entry.key}`"
              >
                <span class="font-mono">{{ entry.key }}</span>
                <span v-for="(pair, field) in entry.fields" :key="field" class="ml-2">
                  {{ t(`rubricEval.detail.diffField.${field}`) }}: {{ String(pair.base ?? '—') }} → {{ String(pair.target ?? '—') }}
                </span>
              </div>
            </div>
          </div>
        </n-spin>
      </div>
    </n-modal>

    <!-- 运行创建：选版本 + judge/fake + 用例（JSON 粘贴，最多 50 条） -->
    <n-modal
      :show="runVisible"
      preset="card"
      :title="t('rubricEval.run.createTitle')"
      class="max-w-3xl"
      data-testid="rubric-run-modal"
      @update:show="(value: boolean) => (!value ? (runVisible = false) : undefined)"
    >
      <div class="flex flex-col gap-3">
        <div class="flex flex-wrap items-end gap-3">
          <div>
            <div class="mb-1 text-xs text-slate-500 dark:text-slate-400">{{ t('rubricEval.run.version') }}</div>
            <n-select
              v-model:value="runForm.versionNo"
              :options="versionOptions"
              class="w-32"
              size="small"
              data-testid="rubric-run-version"
            />
          </div>
          <div>
            <div class="mb-1 text-xs text-slate-500 dark:text-slate-400">{{ t('rubricEval.run.mode') }}</div>
            <n-select
              v-model:value="runForm.mode"
              :options="modeOptions"
              class="w-40"
              size="small"
              data-testid="rubric-run-mode"
            />
          </div>
        </div>
        <n-alert type="warning" :bordered="false" data-testid="rubric-run-mode-note">
          {{ runForm.mode === 'judge' ? t('rubricEval.run.judgeNote') : t('rubricEval.run.fakeNote') }}
        </n-alert>
        <div>
          <div class="mb-1 text-xs text-slate-500 dark:text-slate-400">{{ t('rubricEval.run.casesJson') }}</div>
          <n-input
            v-model:value="runForm.casesJson"
            type="textarea"
            :placeholder="casesPlaceholder"
            :autosize="{ minRows: 8, maxRows: 18 }"
            data-testid="rubric-run-cases"
          />
          <p class="mt-1 text-[11px] text-slate-500 dark:text-slate-400">{{ t('rubricEval.run.casesHint') }}</p>
        </div>
        <n-alert v-if="runError" type="error" :bordered="false" data-testid="rubric-run-error">
          {{ runError }}
        </n-alert>
      </div>
      <template #footer>
        <div class="flex justify-end gap-2">
          <n-button quaternary :disabled="running" @click="runVisible = false">{{ t('rubricEval.common.cancel') }}</n-button>
          <n-button type="primary" :loading="running" :disabled="running" data-testid="rubric-run-submit" @click="submitRun">
            {{ t('rubricEval.run.submit') }}
          </n-button>
        </div>
      </template>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  NAlert,
  NButton,
  NInput,
  NInputNumber,
  NModal,
  NPopconfirm,
  NSelect,
  NSpin,
  NTag,
  useMessage
} from 'naive-ui'
import PageBreadcrumb from '@/components/common/PageBreadcrumb.vue'
import {
  rubricEvalApi,
  type RubricDiff,
  type RubricDetail,
  type RubricItem,
  type RubricRunCasePayload,
  type RubricRunMode,
  type RubricRunSummary,
  type RubricStrictness,
  type RubricVersion,
  type RubricVersionSummary
} from '@/api/rubricEval'
import { apiUnwrap } from '@/utils/apiUnwrap'
import { getErrorMessage } from '@/utils/apiError'
import { parseApiDate } from '@/utils/format'

const { t, locale } = useI18n()
const route = useRoute()
const router = useRouter()
const message = useMessage()

const RUBRIC_KEY_PATTERN = /^[a-z][a-z0-9_]{1,31}$/

const rubricId = computed(() => String(route.params.id || ''))
const detail = ref<RubricDetail | null>(null)
const versions = ref<RubricVersionSummary[]>([])
const selectedVersion = ref<number | null>(null)
const selectedVersionRow = ref<RubricVersion | null>(null)
const runs = ref<RubricRunSummary[]>([])
const loading = ref(false)
const loadError = ref(false)
const archiving = ref(false)

const diffVisible = ref(false)
const diffLoading = ref(false)
const diffError = ref('')
const diff = ref<RubricDiff | null>(null)
const diffBase = ref<number | null>(null)
const diffTarget = ref<number | null>(null)

const newVersionVisible = ref(false)
const savingVersion = ref(false)
const versionError = ref('')
const versionForm = reactive({
  changeNote: '',
  items: [] as Array<{ key: string; question: string; weight: number; strictness: RubricStrictness }>
})

const runVisible = ref(false)
const running = ref(false)
const runError = ref('')
const runForm = reactive({
  versionNo: null as number | null,
  mode: 'judge' as RubricRunMode,
  casesJson: ''
})

const archiveReadOnly = computed(() => detail.value?.status === 'archived')

const versionOptions = computed(() =>
  versions.value.map((row) => ({ value: row.version_no, label: `v${row.version_no}` }))
)

const strictnessOptions = computed(() => [
  { label: t('rubricEval.strictness.neutral'), value: 'neutral' },
  { label: t('rubricEval.strictness.strict'), value: 'strict' },
  { label: t('rubricEval.strictness.lenient'), value: 'lenient' }
])

const modeOptions = computed(() => [
  { label: t('rubricEval.mode.judge'), value: 'judge' },
  { label: t('rubricEval.mode.fake'), value: 'fake' }
])

const casesPlaceholder = computed(() => t('rubricEval.run.casesPlaceholder'))

function formatDate(value: string | null): string {
  const date = parseApiDate(value)
  if (!date) return '—'
  return date.toLocaleString(locale.value)
}

function formatScore(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${(value * 100).toFixed(1)}%`
}

async function loadAll(): Promise<void> {
  if (!rubricId.value) return
  loading.value = true
  loadError.value = false
  try {
    const [detailRes, versionsRes] = await Promise.all([
      rubricEvalApi.get(rubricId.value),
      rubricEvalApi.versions(rubricId.value, { page: 1, page_size: 100 })
    ])
    detail.value = apiUnwrap<RubricDetail>(detailRes)
    const list = apiUnwrap<{ items: RubricVersionSummary[] }>(versionsRes)
    versions.value = list?.items ?? []
    const latest = versions.value[0]?.version_no ?? null
    if (selectedVersion.value === null || !versions.value.some((row) => row.version_no === selectedVersion.value)) {
      selectedVersion.value = latest
    }
    if (selectedVersion.value !== null) {
      await loadVersion(selectedVersion.value)
    } else {
      selectedVersionRow.value = null
    }
    await loadRuns()
  } catch {
    loadError.value = true
  } finally {
    loading.value = false
  }
}

async function loadVersion(versionNo: number): Promise<void> {
  try {
    const res = await rubricEvalApi.getVersion(rubricId.value, versionNo)
    selectedVersionRow.value = apiUnwrap<RubricVersion>(res)
  } catch {
    selectedVersionRow.value = null
  }
}

async function selectVersion(versionNo: number): Promise<void> {
  selectedVersion.value = versionNo
  await loadVersion(versionNo)
}

async function loadRuns(): Promise<void> {
  try {
    const res = await rubricEvalApi.listRuns({ rubric_id: rubricId.value, page: 1, page_size: 20 })
    const list = apiUnwrap<{ items: RubricRunSummary[] }>(res)
    runs.value = list?.items ?? []
  } catch {
    runs.value = []
  }
}

function openRunDetail(runId: string): void {
  void router.push(`/eval/runs/${runId}`)
}

function openDiff(): void {
  if (versions.value.length < 2) return
  diffBase.value = versions.value[1].version_no
  diffTarget.value = versions.value[0].version_no
  diffVisible.value = true
  void runDiff()
}

async function runDiff(): Promise<void> {
  if (diffBase.value === null || diffTarget.value === null) return
  diffLoading.value = true
  diffError.value = ''
  try {
    const res = await rubricEvalApi.diff(rubricId.value, diffBase.value, diffTarget.value)
    diff.value = apiUnwrap<RubricDiff>(res)
  } catch (error) {
    diff.value = null
    diffError.value = getErrorMessage(error, t('rubricEval.detail.diffFailed'))
  } finally {
    diffLoading.value = false
  }
}

function openNewVersion(): void {
  const items = selectedVersionRow.value?.items ?? []
  versionForm.items = items.map((item) => ({
    key: item.key,
    question: item.question,
    weight: item.weight,
    strictness: item.strictness
  }))
  versionForm.changeNote = ''
  versionError.value = ''
  newVersionVisible.value = true
}

function addVersionItem(): void {
  versionForm.items.push({
    key: `item_${versionForm.items.length + 1}`,
    question: '',
    weight: 1,
    strictness: 'neutral'
  })
}

async function submitNewVersion(): Promise<void> {
  if (savingVersion.value) return
  versionError.value = ''
  const items: RubricItem[] = []
  const seen = new Set<string>()
  for (const item of versionForm.items) {
    const key = item.key.trim()
    if (!RUBRIC_KEY_PATTERN.test(key)) {
      versionError.value = t('rubricEval.library.itemKeyInvalid')
      return
    }
    if (seen.has(key)) {
      versionError.value = t('rubricEval.library.itemKeyDuplicate')
      return
    }
    seen.add(key)
    if (!item.question.trim()) {
      versionError.value = t('rubricEval.library.questionRequired')
      return
    }
    items.push({ key, question: item.question.trim(), weight: item.weight || 1, strictness: item.strictness })
  }
  if (items.length === 0) {
    versionError.value = t('rubricEval.library.itemsRequired')
    return
  }
  savingVersion.value = true
  try {
    await rubricEvalApi.createVersion(rubricId.value, {
      items,
      change_note: versionForm.changeNote.trim() || null
    })
    message.success(t('rubricEval.detail.newVersionDone'))
    newVersionVisible.value = false
    selectedVersion.value = null
    await loadAll()
  } catch (error) {
    versionError.value = getErrorMessage(error, t('rubricEval.detail.newVersionFailed'))
  } finally {
    savingVersion.value = false
  }
}

function openRun(): void {
  runForm.versionNo = selectedVersion.value ?? versions.value[0]?.version_no ?? null
  runForm.mode = 'judge'
  runForm.casesJson = ''
  runError.value = ''
  runVisible.value = true
}

function parseCases(): RubricRunCasePayload[] | null {
  let parsed: unknown
  try {
    parsed = JSON.parse(runForm.casesJson)
  } catch {
    runError.value = t('rubricEval.run.casesInvalidJson')
    return null
  }
  if (!Array.isArray(parsed) || parsed.length === 0) {
    runError.value = t('rubricEval.run.casesInvalidShape')
    return null
  }
  if (parsed.length > 50) {
    runError.value = t('rubricEval.run.casesTooMany')
    return null
  }
  const cases: RubricRunCasePayload[] = []
  for (const entry of parsed) {
    if (!entry || typeof entry !== 'object') {
      runError.value = t('rubricEval.run.casesInvalidShape')
      return null
    }
    const row = entry as Record<string, unknown>
    if (typeof row.case_id !== 'string' || typeof row.answer !== 'string') {
      runError.value = t('rubricEval.run.casesInvalidShape')
      return null
    }
    cases.push({
      case_id: row.case_id,
      name: typeof row.name === 'string' ? row.name : null,
      answer: row.answer,
      gold_verdicts: (row.gold_verdicts as RubricRunCasePayload['gold_verdicts']) ?? null,
      fake_verdicts: (row.fake_verdicts as RubricRunCasePayload['fake_verdicts']) ?? null
    })
  }
  return cases
}

async function submitRun(): Promise<void> {
  if (running.value) return
  runError.value = ''
  const cases = parseCases()
  if (cases === null) return
  running.value = true
  try {
    const res = await rubricEvalApi.createRun(rubricId.value, {
      rubric_version_no: runForm.versionNo,
      mode: runForm.mode,
      cases
    })
    const run = apiUnwrap<{ id: string }>(res)
    message.success(t('rubricEval.run.done'))
    runVisible.value = false
    if (run?.id) void router.push(`/eval/runs/${run.id}`)
  } catch (error) {
    runError.value = getErrorMessage(error, t('rubricEval.run.failed'))
  } finally {
    running.value = false
  }
}

async function archiveRubric(): Promise<void> {
  if (archiving.value) return
  archiving.value = true
  try {
    await rubricEvalApi.archive(rubricId.value)
    message.success(t('rubricEval.detail.archiveDone'))
    await loadAll()
  } catch (error) {
    message.error(getErrorMessage(error, t('rubricEval.detail.archiveFailed')))
  } finally {
    archiving.value = false
  }
}

onMounted(loadAll)

defineExpose({
  detail,
  versions,
  selectedVersion,
  selectedVersionRow,
  runs,
  diff,
  diffVisible,
  diffBase,
  diffTarget,
  versionForm,
  runForm,
  openDiff,
  runDiff,
  openNewVersion,
  submitNewVersion,
  openRun,
  submitRun,
  archiveRubric,
  loadAll,
  selectVersion
})
</script>
