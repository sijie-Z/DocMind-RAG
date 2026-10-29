<template>
  <div class="min-h-screen bg-gradient-to-br from-slate-50 via-white to-blue-50 dark:from-gray-950 dark:via-gray-900 dark:to-slate-950 p-6">
    <div class="max-w-7xl mx-auto">
      <div class="mb-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 class="text-3xl font-bold text-gray-800 dark:text-white">{{ t('menu.reviews') }}</h1>
          <p class="mt-1 text-sm text-gray-500 dark:text-gray-400">{{ t('reviews.subtitle') }}</p>
        </div>
        <n-select
          v-model:value="statusFilter"
          :options="statusOptions"
          :placeholder="t('reviews.filterStatus')"
          clearable
          class="w-44"
          @update:value="load"
        />
      </div>

      <n-alert v-if="loadError" type="error" class="mb-4" :bordered="false">
        {{ t('reviews.loadFailed') }}
        <n-button text type="primary" class="ml-2" @click="load">{{ t('reviews.retry') }}</n-button>
      </n-alert>

      <n-spin :show="loading">
        <n-empty v-if="!loading && rows.length === 0" :description="t('reviews.empty')" class="py-20" />
        <n-data-table
          v-else
          :columns="columns"
          :data="rows"
          :bordered="false"
          :single-line="false"
          :row-props="rowProps"
        />
      </n-spin>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { NButton, NTag } from 'naive-ui'
import { reviewsApi, type ReviewCaseStatus, type ReviewCaseSummary } from '@/api/reviews'
import { apiUnwrap } from '@/utils/apiUnwrap'

const { t } = useI18n()
const router = useRouter()

const rows = ref<ReviewCaseSummary[]>([])
const loading = ref(false)
const loadError = ref(false)
const statusFilter = ref<ReviewCaseStatus | null>(null)

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
      page: 1,
      page_size: 50
    })
    const data = apiUnwrap<{ items: ReviewCaseSummary[] }>(response)
    rows.value = data?.items ?? []
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
    title: t('reviews.open'),
    key: 'title',
    render: (row: ReviewCaseSummary) =>
      h('div', [
        h('div', { class: 'font-medium text-gray-800 dark:text-gray-100' }, row.title),
        h('div', { class: 'text-xs text-gray-400' }, row.id.slice(0, 8))
      ])
  },
  {
    title: t('reviews.filterStatus'),
    key: 'status',
    render: (row: ReviewCaseSummary) =>
      h(NTag, { type: statusTagType[row.status], size: 'small' }, {
        default: () => statusLabel.value[row.status]
      })
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
        ? h('span', { class: 'text-xs text-red-500' }, row.blocking_reasons.join(' · '))
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

onMounted(load)
</script>
