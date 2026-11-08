<template>
  <div class="p-4 md:p-6">
    <div class="max-w-7xl mx-auto">
      <div class="mb-6 flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h1 class="text-2xl font-semibold tracking-tight text-slate-900 dark:text-slate-100 md:text-[28px]">
            {{ t('menu.reviews') }}
          </h1>
          <p class="text-slate-500 dark:text-slate-400 mt-1 text-sm flex items-center gap-2">
            <span class="w-6 h-0.5 bg-blue-600 rounded-full"></span>
            {{ t('reviews.subtitle') }}
          </p>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <n-input
            v-model:value="searchInput"
            :placeholder="t('reviews.searchPlaceholder')"
            :input-props="{ 'aria-label': t('reviews.searchLabel') }"
            :maxlength="200"
            clearable
            class="reviews-search"
            data-testid="reviews-search"
            @update:value="scheduleSearch"
            @keydown.enter="onSearchEnter"
          >
            <template #prefix>
              <n-icon class="text-slate-400"><SearchOutline /></n-icon>
            </template>
          </n-input>
          <n-select
            v-model:value="statusFilter"
            :options="statusOptions"
            :placeholder="t('reviews.allStatuses')"
            :aria-label="t('reviews.statusColumn')"
            clearable
            class="reviews-filter w-44"
            @update:value="applyFilter"
          />
          <n-select
            v-model:value="archivedFilter"
            :options="archivedOptions"
            :aria-label="t('reviews.archivedFilterLabel')"
            class="reviews-filter w-32"
            data-testid="archived-filter"
            @update:value="applyFilter"
          />
          <n-button type="primary" round class="create-case-btn" data-testid="create-case-open" @click="createVisible = true">
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
          class="flex flex-col items-center justify-center py-20 px-6 rounded-2xl border border-dashed border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-900"
          data-testid="reviews-empty-state"
        >
          <div class="mb-5 flex h-16 w-16 items-center justify-center rounded-2xl border border-slate-200 bg-slate-50 dark:border-slate-700 dark:bg-slate-800">
            <n-icon size="30" class="text-slate-500 dark:text-slate-400"><DocumentTextOutline /></n-icon>
          </div>
          <template v-if="hasActiveFilters">
            <h2 class="text-lg font-semibold text-slate-800 dark:text-slate-100 mb-2">
              {{ t('reviews.emptyFiltered') }}
            </h2>
            <p class="text-slate-500 dark:text-slate-400 mb-6 max-w-sm text-center text-sm leading-relaxed">
              {{ t('reviews.emptyFilteredHint') }}
            </p>
            <n-button type="primary" round data-testid="reviews-clear-filters" @click="clearFilters">
              {{ t('reviews.clearFilters') }}
            </n-button>
          </template>
          <template v-else>
            <h2 class="text-lg font-semibold text-slate-800 dark:text-slate-100 mb-2">
              {{ archivedFilter === 'archived' ? t('reviews.emptyArchived') : t('reviews.empty') }}
            </h2>
            <p class="text-slate-500 dark:text-slate-400 mb-6 max-w-sm text-center text-sm leading-relaxed">
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
              data-testid="create-case-empty"
              @click="createVisible = true"
            >
              {{ t('reviews.createCase') }}
            </n-button>
          </template>
        </div>

        <template v-else-if="rows.length > 0">
          <!-- 移动端：卡片列表（<768），避免宽表格被压成竖排 -->
          <div v-if="isCompact" class="space-y-3">
            <div
              v-for="row in rows"
              :key="row.id"
              class="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900"
              :data-testid="`review-card-${row.id}`"
            >
              <div class="flex items-start justify-between gap-3">
                <a
                  :href="`/reviews/${row.id}`"
                  class="min-w-0 text-sm font-semibold text-slate-900 hover:text-blue-600 dark:text-slate-100 dark:hover:text-blue-400"
                  @click.prevent="open(row)"
                >
                  {{ row.title }}
                </a>
                <n-button size="tiny" type="primary" secondary class="review-card-open" @click="open(row)">
                  {{ t('reviews.open') }}
                </n-button>
              </div>
              <div class="mt-2 flex flex-wrap items-center gap-1">
                <n-tag :type="statusTagType[row.status]" size="small">
                  {{ statusLabel[row.status] }}
                </n-tag>
                <n-tag v-if="row.archived_at" size="small" data-testid="row-archived-tag">
                  {{ t('reviews.archived') }}
                </n-tag>
              </div>
              <dl class="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-slate-500 dark:text-slate-400">
                <div class="flex justify-between gap-2">
                  <dt>{{ t('reviews.baseVersion') }}</dt>
                  <dd class="text-slate-700 dark:text-slate-300">{{ row.base_version_label || '—' }}</dd>
                </div>
                <div class="flex justify-between gap-2">
                  <dt>{{ t('reviews.revisedVersion') }}</dt>
                  <dd class="text-slate-700 dark:text-slate-300">{{ row.revised_version_label || '—' }}</dd>
                </div>
                <div class="col-span-2 flex justify-between gap-2">
                  <dt>{{ t('reviews.progressColumn') }}</dt>
                  <dd class="text-slate-700 dark:text-slate-300">{{ progressText(row) }}</dd>
                </div>
                <div class="col-span-2 flex justify-between gap-2">
                  <dt>{{ t('reviews.diffSummaryLabel') }}</dt>
                  <dd class="text-slate-700 dark:text-slate-300">{{ diffSummary(row) }}</dd>
                </div>
                <div class="col-span-2 flex justify-between gap-2">
                  <dt>{{ t('reviews.createdAt') }}</dt>
                  <dd class="text-slate-700 dark:text-slate-300">{{ formatDateTime(row.created_at) }}</dd>
                </div>
              </dl>
              <div
                v-if="row.blocking_reasons.length"
                class="mt-2 text-xs text-red-600 dark:text-red-400"
                :title="row.blocking_reasons.join(' · ')"
              >
                {{ t('reviews.blockedReasons') }}: {{ reasonsLabel(t, row.blocking_reasons) }}
              </div>
            </div>
          </div>

          <!-- 桌面端：表格 -->
          <div v-else>
            <n-data-table
              :columns="columns"
              :data="rows"
              :bordered="false"
              :single-line="false"
              :row-props="rowProps"
              :scroll-x="1120"
            />
          </div>

          <div class="mt-4 flex max-w-full justify-end overflow-x-auto">
            <n-pagination
              v-model:page="page"
              :page-size="pageSize"
              :item-count="total"
              :simple="isCompact"
              :page-slot="7"
              @update:page="handlePageChange"
            />
          </div>
        </template>
      </n-spin>
    </div>
    <CreateCaseModal v-model:show="createVisible" @created="onCreated" />
  </div>
</template>

<script setup lang="ts">
import { computed, h, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { parseApiDate } from '@/utils/format'
import { NButton, NInput, NPagination, NTag } from 'naive-ui'
import { DocumentTextOutline, SearchOutline } from '@vicons/ionicons5'
import { reviewsApi, type ReviewCaseStatus, type ReviewCaseSummary } from '@/api/reviews'
import { apiUnwrap } from '@/utils/apiUnwrap'
import { reasonsLabel } from './reviewReasons'
import CreateCaseModal from './components/CreateCaseModal.vue'

const { t, locale } = useI18n()
const route = useRoute()
const router = useRouter()

const rows = ref<ReviewCaseSummary[]>([])
const total = ref(0)
const loading = ref(false)
const loadError = ref(false)
const statusFilter = ref<ReviewCaseStatus | null>(null)
const q = ref('')
const searchInput = ref('')
const statusCounts = ref<Record<string, number> | null>(null)
let searchTimer: ReturnType<typeof setTimeout> | null = null
let loadSeq = 0
const page = ref(1)
const pageSize = 20
const createVisible = ref(false)
const archivedFilter = ref<'active' | 'archived' | 'all'>('active')

const viewportWidth = ref(typeof window === 'undefined' ? 1280 : window.innerWidth)
// 侧栏 256px 后内容区在 1200 以下放不下 8 列表格：改用卡片，避免关键列被裁切。
const isCompact = computed(() => viewportWidth.value < 1200)
const handleResize = () => { viewportWidth.value = window.innerWidth }

const STATUS_VALUES: ReviewCaseStatus[] = ['blocked', 'ready', 'in_review', 'completed']
const ARCHIVED_VALUES = ['active', 'archived', 'all'] as const

// 归档视图本身有专属空态；状态筛选/搜索/翻页都要给「筛选无结果」出口。
const hasActiveFilters = computed(
  () => statusFilter.value !== null || q.value !== '' || page.value > 1
)

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
  (Object.keys(statusLabel.value) as ReviewCaseStatus[]).map((value) => {
    const count = statusCounts.value?.[value]
    return {
      value,
      label:
        typeof count === 'number'
          ? `${statusLabel.value[value]} (${count})`
          : statusLabel.value[value]
    }
  })
)

const dateFormatter = computed(
  () => new Intl.DateTimeFormat(locale.value, { dateStyle: 'medium', timeStyle: 'short' })
)
function formatDateTime(value?: string | null) {
  const date = parseApiDate(value)
  return date ? dateFormatter.value.format(date) : '—'
}

/** 已决策/总项数；没有核验项时显示 —，不伪装成 0/0 进度。 */
function progressText(row: ReviewCaseSummary) {
  if (!row.item_count) return '—'
  return t('reviews.progressValue', { decided: row.decided_count ?? 0, total: row.item_count })
}

/** 分析前不报告差异数；分析后差异与未识别分开，未识别不并入差异。 */
function diffSummary(row: ReviewCaseSummary) {
  if (!row.item_count) return '—'
  if (!row.all_analyzed) return t('reviews.notAnalyzed')
  const parts = [t('reviews.diffCount', { n: row.changed_count ?? 0 })]
  if (row.unidentified_count) {
    parts.push(t('reviews.unidentifiedCount', { n: row.unidentified_count }))
  }
  return parts.join(' · ')
}

function readQueryState() {
  const query = (route.query || {}) as Record<string, unknown>
  const status = typeof query.status === 'string' && STATUS_VALUES.includes(query.status as ReviewCaseStatus)
    ? (query.status as ReviewCaseStatus)
    : null
  const archived = typeof query.archived === 'string' && (ARCHIVED_VALUES as readonly string[]).includes(query.archived)
    ? (query.archived as 'active' | 'archived' | 'all')
    : 'active'
  const parsedPage = Number.parseInt(String(query.page ?? '1'), 10)
  const pageValue = Number.isFinite(parsedPage) && parsedPage > 0 ? parsedPage : 1
  const qValue = typeof query.q === 'string' ? query.q.trim().slice(0, 200) : ''
  return { status, archived, page: pageValue, q: qValue }
}

function applyQueryState() {
  const state = readQueryState()
  statusFilter.value = state.status
  archivedFilter.value = state.archived
  page.value = state.page
  q.value = state.q
  searchInput.value = state.q
}

function syncQuery() {
  const query: Record<string, string> = {
    ...((route.query || {}) as Record<string, string>)
  }
  if (statusFilter.value) query.status = statusFilter.value
  else delete query.status
  if (archivedFilter.value !== 'active') query.archived = archivedFilter.value
  else delete query.archived
  if (q.value) query.q = q.value
  else delete query.q
  if (page.value > 1) query.page = String(page.value)
  else delete query.page
  // 测试/无路由替代实现下 replace 可能不存在；URL 同步失败不影响取数。
  void (router as unknown as { replace?: (location: { query: Record<string, string> }) => Promise<unknown> })
    .replace?.({ query })
}

async function load() {
  const seq = ++loadSeq
  loading.value = true
  loadError.value = false
  try {
    const response = await reviewsApi.list({
      status: statusFilter.value ?? undefined,
      archived: archivedFilter.value,
      ...(q.value ? { q: q.value } : {}),
      page: page.value,
      page_size: pageSize
    })
    // 防抖搜索/快速切筛选会并发多个请求；只允许最新一次写状态，
    // 否则先发后到的旧响应会覆盖新结果，URL 与列表不一致。
    if (seq !== loadSeq) return
    const data = apiUnwrap<{
      items: ReviewCaseSummary[]
      total?: number
      status_counts?: Record<string, number>
    }>(response)
    rows.value = data?.items ?? []
    total.value = data?.total ?? rows.value.length
    statusCounts.value = data?.status_counts ?? null
  } catch {
    if (seq === loadSeq) loadError.value = true
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

function open(row: ReviewCaseSummary) {
  router.push(`/reviews/${row.id}`)
}

const columns = computed(() => [
  {
    title: t('reviews.caseColumn'),
    key: 'title',
    minWidth: 240,
    render: (row: ReviewCaseSummary) =>
      h('div', [
        h(
          'a',
          {
            href: `/reviews/${row.id}`,
            class: 'font-medium text-gray-800 hover:text-blue-600 dark:text-gray-100 dark:hover:text-blue-400',
            onClick: (event: MouseEvent) => {
              event.preventDefault()
              open(row)
            }
          },
          row.title
        ),
        h('div', { class: 'text-xs text-gray-500 dark:text-gray-400 tabular-nums' }, row.id.slice(0, 8))
      ])
  },
  {
    title: t('reviews.statusColumn'),
    key: 'status',
    minWidth: 90,
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
    title: t('reviews.progressColumn'),
    key: 'progress',
    minWidth: 150,
    render: (row: ReviewCaseSummary) =>
      h('div', [
        h(
          'div',
          { class: 'text-sm tabular-nums text-slate-700 dark:text-slate-300' },
          progressText(row)
        ),
        h('div', { class: 'text-xs text-slate-500 dark:text-slate-400' }, diffSummary(row))
      ])
  },
  {
    title: t('reviews.baseVersion'),
    key: 'base_version_label',
    minWidth: 90,
    render: (row: ReviewCaseSummary) => row.base_version_label || '—'
  },
  {
    title: t('reviews.revisedVersion'),
    key: 'revised_version_label',
    minWidth: 90,
    render: (row: ReviewCaseSummary) => row.revised_version_label || '—'
  },
  {
    title: t('reviews.blockedReasons'),
    key: 'blocking_reasons',
    minWidth: 160,
    render: (row: ReviewCaseSummary) =>
      row.blocking_reasons.length
        ? h(
            'span',
            {
              class: 'text-xs text-red-600 dark:text-red-400',
              title: row.blocking_reasons.join(' · ')
            },
            reasonsLabel(t, row.blocking_reasons)
          )
        : '—'
  },
  {
    title: t('reviews.createdAt'),
    key: 'created_at',
    align: 'right',
    minWidth: 150,
    render: (row: ReviewCaseSummary) =>
      h('span', { class: 'tabular-nums' }, formatDateTime(row.created_at))
  },
  {
    title: '',
    key: 'actions',
    minWidth: 64,
    render: (row: ReviewCaseSummary) =>
      h(NButton, { text: true, type: 'primary', onClick: () => open(row) }, {
        default: () => t('reviews.open')
      })
  }
])

function rowProps(row: ReviewCaseSummary) {
  // 行点击是鼠标增强；键盘路径由标题 <a> 与「打开」按钮承担，
  // 不再给 <tr> 加 role/tabindex（会覆盖表格 row 语义并造成一停三焦点）。
  return {
    style: 'cursor: pointer',
    onClick: () => open(row)
  }
}

function applyFilter() {
  page.value = 1
  syncQuery()
  load()
}

function commitSearch() {
  if (searchTimer) {
    clearTimeout(searchTimer)
    searchTimer = null
  }
  const next = searchInput.value.trim().slice(0, 200)
  if (searchInput.value !== next) searchInput.value = next
  if (next === q.value) return
  q.value = next
  page.value = 1
  syncQuery()
  void load()
}

function scheduleSearch() {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    searchTimer = null
    commitSearch()
  }, 300)
}

function runSearchNow() {
  commitSearch()
}

function onSearchEnter(event: KeyboardEvent) {
  // IME 组合中的 Enter 用于确认候选词（keyCode 229），不提交搜索。
  if (event.isComposing || event.keyCode === 229) return
  runSearchNow()
}

function clearFilters() {
  statusFilter.value = null
  archivedFilter.value = 'active'
  q.value = ''
  searchInput.value = ''
  page.value = 1
  syncQuery()
  load()
}

function handlePageChange() {
  syncQuery()
  load()
}

function onCreated(caseId: string) {
  createVisible.value = false
  router.push(`/reviews/${caseId}`)
}

watch(
  () => route.query,
  () => {
    const state = readQueryState()
    if (
      state.status === statusFilter.value &&
      state.archived === archivedFilter.value &&
      state.page === page.value &&
      state.q === q.value
    ) {
      return
    }
    // 导航（前进/后退）以 URL 为准：丢弃尚未提交的输入，避免防抖计时器
    // 在导航后把旧输入再提交一次。
    if (searchTimer) {
      clearTimeout(searchTimer)
      searchTimer = null
    }
    statusFilter.value = state.status
    archivedFilter.value = state.archived
    page.value = state.page
    q.value = state.q
    searchInput.value = state.q
    void load()
  },
  { deep: true }
)

onMounted(() => {
  applyQueryState()
  // 非法 query 回退后规范化 URL（bogus -> 默认值），避免刷新后继续带着无效参数。
  syncQuery()
  window.addEventListener('resize', handleResize)
  void load()
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  if (searchTimer) {
    clearTimeout(searchTimer)
    searchTimer = null
  }
})
</script>

<style scoped>
.reviews-search {
  width: 15rem;
  flex: none;
}
/* 非聚焦态边界需可辨识（WCAG 1.4.11 非文本对比 3:1）；仅本页搜索框加深。 */
.reviews-search :deep(.n-input__border) {
  border-color: #64748b;
}
.reviews-search :deep(.n-input__input-el) {
  font-size: 14px;
}
@media (max-width: 1199.98px) {
  .reviews-search {
    width: 100%;
  }
  /* NInput 根节点自身就是 .n-input，--n-height 必须落在根上。 */
  .reviews-search.n-input {
    --n-height: 44px !important;
  }
  /* iOS 对 <16px 输入框聚焦会自动放大页面。 */
  .reviews-search :deep(.n-input__input-el) {
    font-size: 16px;
  }
  [data-testid="reviews-clear-filters"].n-button {
    height: 44px !important;
  }
  .review-card-open.n-button {
    height: 44px !important;
    min-width: 72px;
  }
  .reviews-filter :deep(.n-base-selection) {
    --n-height: 44px !important;
  }
  .create-case-btn.n-button {
    height: 44px !important;
  }
}
</style>
