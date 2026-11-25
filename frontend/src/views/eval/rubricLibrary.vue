<template>
  <div class="p-4 md:p-6">
    <div class="max-w-7xl mx-auto">
      <PageBreadcrumb :show-back="true" class="mb-4" />
      <div class="mb-6 flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h1 class="text-2xl font-semibold tracking-tight text-slate-900 dark:text-slate-100 md:text-[28px]">
            {{ t('router.evalRubrics') }}
          </h1>
          <p class="text-slate-500 dark:text-slate-400 mt-1 text-sm flex items-center gap-2">
            <span class="w-6 h-0.5 bg-blue-600 rounded-full"></span>
            {{ t('rubricEval.library.subtitle') }}
          </p>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <n-select
            v-model:value="statusFilter"
            :options="statusOptions"
            :aria-label="t('rubricEval.library.statusColumn')"
            class="w-40"
            data-testid="rubric-status-filter"
            @update:value="applyFilter"
          />
          <n-button type="primary" round data-testid="rubric-create-open" @click="openCreate">
            {{ t('rubricEval.library.create') }}
          </n-button>
          <n-button
            secondary
            round
            data-testid="rubric-bootstrap"
            :loading="bootstrapping"
            @click="bootstrap"
          >
            {{ t('rubricEval.library.bootstrap') }}
          </n-button>
        </div>
      </div>

      <!-- 口径横幅：rubric 评答案、judge 是模型判定、不替代检索评测 -->
      <n-alert type="info" :bordered="false" class="mb-4" data-testid="rubric-scope-banner">
        {{ t('rubricEval.library.scopeBanner') }}
      </n-alert>

      <n-alert v-if="loadError" type="error" class="mb-4" :bordered="false">
        {{ t('rubricEval.library.loadFailed') }}
        <n-button text type="primary" class="ml-2" @click="load">{{ t('rubricEval.common.retry') }}</n-button>
      </n-alert>

      <n-spin :show="loading">
        <div
          v-if="!loading && !loadError && rows.length === 0"
          class="flex flex-col items-center justify-center rounded-2xl border border-dashed border-slate-200 bg-white px-6 py-20 dark:border-slate-700 dark:bg-slate-900"
          data-testid="rubrics-empty-state"
        >
          <div class="mb-5 flex h-16 w-16 items-center justify-center rounded-2xl border border-slate-200 bg-slate-50 dark:border-slate-700 dark:bg-slate-800">
            <n-icon size="30" class="text-slate-500 dark:text-slate-400"><DocumentTextOutline /></n-icon>
          </div>
          <h2 class="text-lg font-semibold text-slate-800 dark:text-slate-100 mb-2">
            {{ t('rubricEval.library.empty') }}
          </h2>
          <p class="text-slate-500 dark:text-slate-400 mb-6 max-w-md text-center text-sm leading-relaxed">
            {{ t('rubricEval.library.emptyHint') }}
          </p>
          <div class="flex flex-wrap justify-center gap-2">
            <n-button type="primary" data-testid="rubric-create-empty" @click="openCreate">
              {{ t('rubricEval.library.create') }}
            </n-button>
            <n-button secondary :loading="bootstrapping" @click="bootstrap">
              {{ t('rubricEval.library.bootstrap') }}
            </n-button>
          </div>
        </div>

        <template v-else-if="rows.length > 0">
          <div class="space-y-3">
            <div
              v-for="row in rows"
              :key="row.id"
              class="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900"
              :data-testid="`rubric-row-${row.id}`"
            >
              <div class="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                <div class="min-w-0">
                  <a
                    :href="`/eval/rubrics/${row.id}`"
                    class="text-sm font-semibold text-slate-900 hover:text-blue-600 dark:text-slate-100 dark:hover:text-blue-400"
                    @click.prevent="openDetail(row)"
                  >
                    {{ row.name }}
                  </a>
                  <p v-if="row.description" class="mt-1 line-clamp-2 text-xs text-slate-500 dark:text-slate-400">
                    {{ row.description }}
                  </p>
                  <p class="mt-1 text-[11px] text-slate-400 dark:text-slate-500">
                    {{ t('rubricEval.library.updatedAt') }}: {{ formatDate(row.updated_at || row.created_at) }}
                  </p>
                </div>
                <div class="flex shrink-0 items-center gap-2">
                  <n-tag :type="row.status === 'archived' ? 'default' : 'success'" size="small">
                    {{ row.status === 'archived' ? t('rubricEval.common.statusArchived') : t('rubricEval.common.statusActive') }}
                  </n-tag>
                  <span class="text-xs tabular-nums text-slate-500 dark:text-slate-400">
                    {{ t('rubricEval.library.versionTag', { n: row.latest_version }) }}
                  </span>
                  <n-button text type="primary" size="small" @click="openDetail(row)">
                    {{ t('rubricEval.library.open') }}
                  </n-button>
                  <n-button
                    text
                    size="small"
                    data-testid="rubric-open-runs"
                    @click="openRuns(row)"
                  >
                    {{ t('rubricEval.library.runs') }}
                  </n-button>
                </div>
              </div>
            </div>
          </div>
          <div class="mt-4 flex justify-end">
            <n-pagination
              v-model:page="page"
              :page-size="pageSize"
              :item-count="total"
              @update:page="load"
            />
          </div>
        </template>
      </n-spin>

      <!-- 新建评分表：名称 + 说明 + 首版评分项 -->
      <n-modal
        :show="createVisible"
        preset="card"
        :title="t('rubricEval.library.create')"
        class="max-w-2xl"
        :mask-closable="!creating"
        :closable="!creating"
        data-testid="rubric-create-modal"
        @update:show="(value: boolean) => (!value ? (createVisible = false) : undefined)"
      >
        <div class="flex flex-col gap-3">
          <div>
            <div class="mb-1 text-xs font-medium text-slate-500 dark:text-slate-400">
              {{ t('rubricEval.library.name') }}
            </div>
            <n-input
              v-model:value="form.name"
              :placeholder="t('rubricEval.library.namePlaceholder')"
              :input-props="{ 'aria-label': t('rubricEval.library.name') }"
              maxlength="100"
              data-testid="rubric-name-input"
            />
          </div>
          <div>
            <div class="mb-1 text-xs font-medium text-slate-500 dark:text-slate-400">
              {{ t('rubricEval.library.description') }}
            </div>
            <n-input
              v-model:value="form.description"
              type="textarea"
              :placeholder="t('rubricEval.library.descriptionPlaceholder')"
              maxlength="500"
              :autosize="{ minRows: 2, maxRows: 4 }"
              data-testid="rubric-description-input"
            />
          </div>
          <div>
            <div class="mb-1 flex items-center justify-between text-xs font-medium text-slate-500 dark:text-slate-400">
              <span>{{ t('rubricEval.library.items') }}</span>
              <n-button text size="small" type="primary" data-testid="rubric-add-item" @click="addItem">
                {{ t('rubricEval.library.addItem') }}
              </n-button>
            </div>
            <p v-if="form.items.length === 0" class="text-[11px] text-slate-500 dark:text-slate-400">
              {{ t('rubricEval.library.itemsEmpty') }}
            </p>
            <div
              v-for="(item, index) in form.items"
              :key="index"
              class="mb-2 rounded-lg border border-slate-200 p-2 dark:border-slate-700"
              :data-testid="`rubric-item-${index}`"
            >
              <div class="flex flex-col gap-2">
                <n-input
                  v-model:value="item.question"
                  :placeholder="t('rubricEval.library.questionPlaceholder')"
                  :input-props="{ 'aria-label': t('rubricEval.library.question') }"
                  maxlength="300"
                  :data-testid="`rubric-item-question-${index}`"
                />
                <div class="flex flex-col gap-2 sm:flex-row">
                  <n-input
                    v-model:value="item.key"
                    :placeholder="t('rubricEval.library.itemKeyPlaceholder')"
                    maxlength="32"
                    class="sm:w-44"
                    :data-testid="`rubric-item-key-${index}`"
                  />
                  <n-input-number
                    v-model:value="item.weight"
                    :min="1"
                    :max="10"
                    :step="1"
                    class="sm:w-32"
                    :aria-label="t('rubricEval.library.weight')"
                    :data-testid="`rubric-item-weight-${index}`"
                  />
                  <n-select
                    v-model:value="item.strictness"
                    :options="strictnessOptions"
                    class="sm:w-40"
                    :aria-label="t('rubricEval.library.strictness')"
                    :data-testid="`rubric-item-strictness-${index}`"
                  />
                  <n-button
                    text
                    type="error"
                    :aria-label="t('rubricEval.library.removeItem')"
                    @click="removeItem(index)"
                  >
                    {{ t('rubricEval.library.removeItem') }}
                  </n-button>
                </div>
              </div>
            </div>
            <p class="text-[11px] text-slate-500 dark:text-slate-400">
              {{ t('rubricEval.library.strictnessHint') }}
            </p>
          </div>
          <div>
            <div class="mb-1 text-xs font-medium text-slate-500 dark:text-slate-400">
              {{ t('rubricEval.library.changeNote') }}
            </div>
            <n-input
              v-model:value="form.changeNote"
              :placeholder="t('rubricEval.library.changeNotePlaceholder')"
              maxlength="200"
            />
          </div>
          <n-alert v-if="createError" type="error" :bordered="false" data-testid="rubric-create-error">
            {{ createError }}
          </n-alert>
        </div>
        <template #footer>
          <div class="flex justify-end gap-2">
            <n-button quaternary :disabled="creating" @click="createVisible = false">
              {{ t('rubricEval.common.cancel') }}
            </n-button>
            <n-button
              type="primary"
              :loading="creating"
              :disabled="creating"
              data-testid="rubric-create-submit"
              @click="submitCreate"
            >
              {{ t('rubricEval.library.createSubmit') }}
            </n-button>
          </div>
        </template>
      </n-modal>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  NAlert,
  NButton,
  NIcon,
  NInput,
  NInputNumber,
  NModal,
  NPagination,
  NSelect,
  NSpin,
  NTag,
  useMessage
} from 'naive-ui'
import { DocumentTextOutline } from '@vicons/ionicons5'
import PageBreadcrumb from '@/components/common/PageBreadcrumb.vue'
import { rubricEvalApi, type RubricItem, type RubricStrictness, type RubricSummary } from '@/api/rubricEval'
import { apiUnwrap } from '@/utils/apiUnwrap'
import { getErrorMessage } from '@/utils/apiError'
import { parseApiDate } from '@/utils/format'

const { t, locale } = useI18n()
const router = useRouter()
const message = useMessage()

const RUBRIC_KEY_PATTERN = /^[a-z][a-z0-9_]{1,31}$/

const rows = ref<RubricSummary[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 20
const loading = ref(false)
const loadError = ref(false)
const statusFilter = ref<'active' | 'archived' | 'all'>('active')
const bootstrapping = ref(false)

const createVisible = ref(false)
const creating = ref(false)
const createError = ref('')
const form = reactive({
  name: '',
  description: '',
  changeNote: '',
  items: [] as Array<{ key: string; question: string; weight: number; strictness: RubricStrictness }>
})

const statusOptions = computed(() => [
  { label: t('rubricEval.common.statusActive'), value: 'active' },
  { label: t('rubricEval.common.statusArchived'), value: 'archived' },
  { label: t('rubricEval.common.statusAll'), value: 'all' }
])

const strictnessOptions = computed(() => [
  { label: t('rubricEval.strictness.neutral'), value: 'neutral' },
  { label: t('rubricEval.strictness.strict'), value: 'strict' },
  { label: t('rubricEval.strictness.lenient'), value: 'lenient' }
])

function formatDate(value: string | null): string {
  const date = parseApiDate(value)
  if (!date) return '—'
  return date.toLocaleString(locale.value)
}

async function load(): Promise<void> {
  loading.value = true
  loadError.value = false
  try {
    const res = await rubricEvalApi.list({
      status: statusFilter.value,
      page: page.value,
      page_size: pageSize
    })
    const data = apiUnwrap<{ items: RubricSummary[]; total: number }>(res)
    rows.value = data?.items ?? []
    total.value = data?.total ?? rows.value.length
  } catch {
    loadError.value = true
    rows.value = []
  } finally {
    loading.value = false
  }
}

function applyFilter(): void {
  page.value = 1
  void load()
}

function openDetail(row: RubricSummary): void {
  void router.push(`/eval/rubrics/${row.id}`)
}

function openRuns(row: RubricSummary): void {
  void router.push({ path: '/eval/rubrics/' + row.id, query: { tab: 'runs' } })
}

function openCreate(): void {
  form.name = ''
  form.description = ''
  form.changeNote = ''
  form.items = []
  createError.value = ''
  createVisible.value = true
}

function addItem(): void {
  form.items.push({
    key: `item_${form.items.length + 1}`,
    question: '',
    weight: 1,
    strictness: 'neutral'
  })
}

function removeItem(index: number): void {
  form.items.splice(index, 1)
}

function normalizeItems(): RubricItem[] | null {
  const items: RubricItem[] = []
  const seen = new Set<string>()
  for (const item of form.items) {
    const key = item.key.trim()
    if (!RUBRIC_KEY_PATTERN.test(key)) {
      createError.value = t('rubricEval.library.itemKeyInvalid')
      return null
    }
    if (seen.has(key)) {
      createError.value = t('rubricEval.library.itemKeyDuplicate')
      return null
    }
    seen.add(key)
    const question = item.question.trim()
    if (!question) {
      createError.value = t('rubricEval.library.questionRequired')
      return null
    }
    items.push({ key, question, weight: item.weight || 1, strictness: item.strictness })
  }
  return items
}

async function submitCreate(): Promise<void> {
  if (creating.value) return
  createError.value = ''
  if (!form.name.trim()) {
    createError.value = t('rubricEval.library.nameRequired')
    return
  }
  const items = normalizeItems()
  if (items === null) return
  if (items.length === 0) {
    createError.value = t('rubricEval.library.itemsRequired')
    return
  }
  creating.value = true
  try {
    const res = await rubricEvalApi.create({
      name: form.name.trim(),
      description: form.description.trim() || null,
      items,
      change_note: form.changeNote.trim() || null
    })
    const created = apiUnwrap<{ id: string }>(res)
    message.success(t('rubricEval.library.createDone'))
    createVisible.value = false
    if (created?.id) void router.push(`/eval/rubrics/${created.id}`)
    else await load()
  } catch (error) {
    createError.value = getErrorMessage(error, t('rubricEval.library.createFailed'))
  } finally {
    creating.value = false
  }
}

async function bootstrap(): Promise<void> {
  if (bootstrapping.value) return
  bootstrapping.value = true
  try {
    const res = await rubricEvalApi.bootstrap({ name: t('rubricEval.library.bootstrapName') })
    const created = apiUnwrap<{ id: string }>(res)
    message.success(t('rubricEval.library.bootstrapDone'))
    if (created?.id) void router.push(`/eval/rubrics/${created.id}`)
    else await load()
  } catch (error) {
    message.error(getErrorMessage(error, t('rubricEval.library.bootstrapFailed')))
  } finally {
    bootstrapping.value = false
  }
}

onMounted(load)

defineExpose({ rows, form, load, openCreate, addItem, removeItem, submitCreate, bootstrap, statusFilter })
</script>
