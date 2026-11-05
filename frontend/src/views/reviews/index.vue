<template>
  <div class="min-h-screen bg-gradient-to-br from-slate-50 via-white to-blue-50 dark:from-gray-950 dark:via-gray-900 dark:to-slate-950 p-6">
    <div class="max-w-7xl mx-auto">
      <div class="mb-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 class="text-3xl font-bold text-gray-800 dark:text-white">{{ t('menu.reviews') }}</h1>
          <span class="mt-2 block h-0.5 w-6 rounded-full bg-blue-500"></span>
          <p class="mt-2 text-sm text-gray-500 dark:text-gray-400">{{ t('reviews.subtitle') }}</p>
        </div>
        <div class="flex flex-wrap items-center gap-3">
          <n-select
            v-model:value="statusFilter"
            :options="statusOptions"
            :placeholder="t('reviews.allStatuses')"
            clearable
            class="w-44"
            @update:value="applyFilter"
          />
          <span class="text-xs text-gray-500 dark:text-gray-400">
            {{ t('reviews.archivedFilterLabel') }}
          </span>
          <n-select
            v-model:value="archivedFilter"
            :options="archivedOptions"
            class="w-36"
            data-testid="archived-filter"
            @update:value="applyFilter"
          />
          <n-button type="primary" data-testid="create-case-open" @click="createVisible = true">
            {{ t('reviews.createCase') }}
          </n-button>
        </div>
      </div>

      <n-alert v-if="loadError" type="error" class="mb-4" :bordered="false">
        {{ t('reviews.loadFailed') }}
        <n-button text type="primary" class="ml-2" @click="load">{{ t('reviews.retry') }}</n-button>
      </n-alert>

      <n-spin :show="loading">
        <div
          v-if="!loading && rows.length === 0"
          class="mt-2 rounded-2xl border border-dashed border-slate-200/80 bg-white/70 px-6 py-16 text-center dark:border-slate-700 dark:bg-slate-900/40"
          data-testid="reviews-empty-state"
        >
          <div
            class="mx-auto flex h-16 w-16 items-center justify-center rounded-2xl bg-blue-50 text-blue-500 dark:bg-blue-500/10 dark:text-blue-300"
          >
            <svg
              class="h-8 w-8"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.6"
              stroke-linecap="round"
              stroke-linejoin="round"
              aria-hidden="true"
            >
              <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
              <path d="M14 3v5h5" />
              <path d="M9 13h6M9 17h4" />
            </svg>
          </div>
          <h2 class="mt-5 text-lg font-semibold text-slate-800 dark:text-slate-100">
            {{ archivedFilter === 'archived' ? t('reviews.emptyArchived') : t('reviews.empty') }}
          </h2>
          <p class="mx-auto mt-2 max-w-lg text-sm leading-6 text-slate-500 dark:text-slate-400">
            {{
              archivedFilter === 'archived'
                ? t('reviews.emptyArchivedHint')
                : t('reviews.emptyHint')
            }}
          </p>
          <n-button
            v-if="archivedFilter !== 'archived'"
            type="primary"
            size="large"
            class="mt-6"
            data-testid="create-case-empty"
            @click="createVisible = true"
          >
            {{ t('reviews.createCase') }}
          </n-button>
        </div>
        <div v-else>
          <n-data-table
            :columns="columns"
            :data="rows"
            :bordered="false"
            :single-line="false"
            :row-props="rowProps"
          />
          <div class="mt-4 flex justify-end">
            <n-pagination
              v-model:page="page"
              :page-size="pageSize"
              :item-count="total"
              :page-slot="7"
              @update:page="load"
            />
          </div>
        </div>
      </n-spin>
    </div>
    <CreateCaseModal v-model:show="createVisible" @created="onCreated" />
  </div>
</template>

<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { NButton, NPagination, NTag } from 'naive-ui'
import { reviewsApi, type ReviewCaseStatus, type ReviewCaseSummary } from '@/api/reviews'
import { apiUnwrap } from '@/utils/apiUnwrap'
import { reasonsLabel } from './reviewReasons'
import CreateCaseModal from './components/CreateCaseModal.vue'

const { t } = useI18n()
const router = useRouter()

const rows = ref<ReviewCaseSummary[]>([])
const total = ref(0)
const loading = ref(false)
const loadError = ref(false)
const statusFilter = ref<ReviewCaseStatus | null>(null)
const page = ref(1)
const pageSize = 20
const createVisible = ref(false)
const archivedFilter = ref<'active' | 'archived' | 'all'>('active')

const statusLabel = computed<Record<ReviewCaseStatus, string>>(() => ({
  blocked: t('reviews.statusBlocked'),
  ready: t('reviews.statusReady'),
  in_review: t('reviews.statusInReview'),
  completed: t('reviews.statusCompleted')
}))
const statusTagType: Record<ReviewCaseStatus, 'error' | 'info' | 'warning' | 'success'> = {
  blocked: 'error',
  ready: 'info',
  in_review: 'warning',
  completed: 'success'
}
const archivedOptions = computed(() => [
  { value: 'active', label: t('reviews.filterActive') },
  { value: 'archived', label: t('reviews.archived') },
  { value: 'all', label: t('reviews.filterAll') }
])
const statusOptions = computed(() =>
  (Object.keys(statusLabel.value) as ReviewCaseStatus[]).map((value) => ({
    value,
    label: statusLabel.value[value]
  }))
)

async function load() {
  loading.value = true
  loadError.value = false
  try {
    const response = await reviewsApi.list({
      status: statusFilter.value ?? undefined,
      archived: archivedFilter.value,
      page: page.value,
      page_size: pageSize
    })
    const data = apiUnwrap<{ items: ReviewCaseSummary[]; total?: number }>(response)
    rows.value = data?.items ?? []
    total.value = data?.total ?? rows.value.length
  } catch {
    loadError.value = true
  } finally {
    loading.value = false
  }
}

function open(row: ReviewCaseSummary) {
  router.push(`/reviews/${row.id}`)
}

const columns = computed(() => [
  {
    title: t('reviews.caseColumn'),
    key: 'title',
    render: (row: ReviewCaseSummary) =>
      h('div', [
        h('div', { class: 'font-medium text-gray-800 dark:text-gray-100' }, row.title),
        h('div', { class: 'text-xs text-gray-400' }, row.id.slice(0, 8))
      ])
  },
  {
    title: t('reviews.statusColumn'),
    key: 'status',
    render: (row: ReviewCaseSummary) =>
      h('div', { class: 'flex flex-wrap gap-1' }, [
        h(NTag, { type: statusTagType[row.status], size: 'small' }, {
          default: () => statusLabel.value[row.status]
        }),
        row.archived_at
          ? h('span', { 'data-testid': 'row-archived-tag' }, [
              h(NTag, { type: 'default', size: 'small' }, {
                default: () => t('reviews.archived')
              })
            ])
          : null
      ])
  },
  {
    title: t('reviews.baseVersion'),
    key: 'base_version_label',
    render: (row: ReviewCaseSummary) => row.base_version_label || '—'
  },
  {
    title: t('reviews.revisedVersion'),
    key: 'revised_version_label',
    render: (row: ReviewCaseSummary) => row.revised_version_label || '—'
  },
  {
    title: t('reviews.blockedReasons'),
    key: 'blocking_reasons',
    render: (row: ReviewCaseSummary) =>
      row.blocking_reasons.length
        ? h(
            'span',
            {
              class: 'text-xs text-red-500 dark:text-red-400',
              title: row.blocking_reasons.join(' · ')
            },
            reasonsLabel(t, row.blocking_reasons)
          )
        : '—'
  },
  {
    title: t('reviews.createdAt'),
    key: 'created_at',
    render: (row: ReviewCaseSummary) =>
      row.created_at ? new Date(row.created_at).toLocaleString() : '—'
  },
  {
    title: '',
    key: 'actions',
    render: (row: ReviewCaseSummary) =>
      h(NButton, { text: true, type: 'primary', onClick: () => open(row) }, {
        default: () => t('reviews.open')
      })
  }
])

function rowProps(row: ReviewCaseSummary) {
  return {
    style: 'cursor: pointer',
    onClick: () => open(row)
  }
}

function applyFilter() {
  page.value = 1
  load()
}

function onCreated(caseId: string) {
  createVisible.value = false
  router.push(`/reviews/${caseId}`)
}

onMounted(load)
</script>
