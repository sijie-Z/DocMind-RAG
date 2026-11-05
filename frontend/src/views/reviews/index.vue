<template>
  <div class="min-h-screen bg-gradient-to-br from-slate-50 via-white to-blue-50 dark:from-gray-950 dark:via-gray-900 dark:to-slate-950 p-6">
    <div class="max-w-7xl mx-auto">
      <div class="mb-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 class="text-2xl md:text-3xl font-bold tracking-tight text-slate-800 dark:text-slate-100">
            {{ t('menu.reviews') }}
          </h1>
          <p class="text-slate-500 dark:text-slate-400 mt-1 text-sm flex items-center gap-2">
            <span class="w-6 h-0.5 bg-blue-500 rounded-full"></span>
            {{ t('reviews.subtitle') }}
          </p>
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
          v-if="!loading && !loadError && rows.length === 0"
          class="flex flex-col items-center justify-center py-24 px-6 bg-white/80 dark:bg-gray-800/50 backdrop-blur-md rounded-3xl border border-dashed border-slate-200 dark:border-gray-600/50 shadow-inner"
          data-testid="reviews-empty-state"
        >
          <div
            class="w-24 h-24 rounded-2xl bg-gradient-to-br from-blue-100 to-slate-100 dark:from-blue-900/30 dark:to-slate-900/30 flex items-center justify-center mb-6 ring-4 ring-blue-100/50 dark:ring-blue-900/30"
          >
            <n-icon size="48" class="text-blue-500 dark:text-blue-400"><DocumentTextOutline /></n-icon>
          </div>
          <h2 class="text-xl font-bold text-slate-800 dark:text-slate-100 mb-2">
            {{ archivedFilter === 'archived' ? t('reviews.emptyArchived') : t('reviews.empty') }}
          </h2>
          <p class="text-slate-500 dark:text-slate-400 mb-8 max-w-sm text-center text-sm leading-relaxed">
            {{
              archivedFilter === 'archived'
                ? t('reviews.emptyArchivedHint')
                : t('reviews.emptyHint')
            }}
          </p>
          <n-button
            v-if="archivedFilter !== 'archived'"
            type="primary"
            round
            size="large"
            class="px-8 shadow-lg shadow-blue-500/20"
            data-testid="create-case-empty"
            @click="createVisible = true"
          >
            {{ t('reviews.createCase') }}
          </n-button>
        </div>
        <div v-else-if="rows.length > 0">
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
import { DocumentTextOutline } from '@vicons/ionicons5'
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
