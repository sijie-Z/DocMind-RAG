<script setup lang="ts">
import { computed, h, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  NAlert,
  NButton,
  NDataTable,
  NEmpty,
  NInput,
  NRadioButton,
  NRadioGroup,
  NSelect,
  NSpin,
  NTag,
  useMessage,
  type DataTableColumns
} from 'naive-ui'
import {
  reviewsApi,
  type RedlineDisposition,
  type RedlineLedgerQuery,
  type RedlineLedgerResponse,
  type RedlineRevisionType,
  type RedlineRowResponse,
  type RedlineSubmissionList,
  type RedlineSubmissionSummary,
  type ReviewItem
} from '@/api/reviews'
import { apiUnwrap } from '@/utils/apiUnwrap'
import { parseApiDate } from '@/utils/format'
import { getErrorMessage } from '@/utils/apiError'

const props = defineProps<{
  caseId: string
  items: ReviewItem[]
  archived: boolean
}>()

const { t } = useI18n()
const message = useMessage()

const submissions = ref<RedlineSubmissionSummary[]>([])
const selectedSubmissionId = ref<string | null>(null)
const ledger = ref<RedlineLedgerResponse | null>(null)
const loading = ref(false)
const ledgerLoading = ref(false)
const uploading = ref(false)
const loadError = ref(false)
const ledgerLoadError = ref(false)
const revisionFilter = ref<RedlineRevisionType | ''>('')
const dispositionFilter = ref<RedlineDisposition | ''>('')
const page = ref(1)
const pageSize = ref(20)
const draftDisposition = ref<Record<string, RedlineDisposition>>({})
const draftNote = ref<Record<string, string>>({})
const draftItem = ref<Record<string, string | null>>({})
const draftEvidence = ref<Record<string, string | null>>({})
const savingRowId = ref('')
const fileInput = ref<HTMLInputElement | null>(null)
const submissionOptions = computed(() =>
  submissions.value.map((row) => ({
    label: `${row.filename} · #${row.sequence_no}`,
    value: row.id
  }))
)
const revisionLabelKeys: Record<RedlineRevisionType, string> = {
  insertion: 'reviews.redlines.revisionTypes.insertion',
  deletion: 'reviews.redlines.revisionTypes.deletion',
  comment: 'reviews.redlines.revisionTypes.comment',
  unparsed: 'reviews.redlines.revisionTypes.unparsed'
}
const dispositionLabelKeys: Record<RedlineDisposition, string> = {
  pending: 'reviews.redlines.dispositions.pending',
  reviewed: 'reviews.redlines.dispositions.reviewed',
  needs_clarification: 'reviews.redlines.dispositions.needsClarification',
  not_relevant: 'reviews.redlines.dispositions.notRelevant'
}

function dispositionKey(value: RedlineDisposition): string {
  return dispositionLabelKeys[value]
}

function revisionKey(value: RedlineRevisionType): string {
  return revisionLabelKeys[value]
}

const revisionOptions = computed(() => [
  { label: t('reviews.redlines.filters.all'), value: '' },
  ...(['insertion', 'deletion', 'comment', 'unparsed'] as const).map((value) => ({
    label: t(revisionLabelKeys[value]),
    value
  }))
])
const dispositionOptions = computed(() => [
  { label: t('reviews.redlines.filters.all'), value: '' },
  ...(['pending', 'reviewed', 'needs_clarification', 'not_relevant'] as const).map((value) => ({
    label: t(dispositionLabelKeys[value]),
    value
  }))
])
const itemOptions = computed(() =>
  props.items.map((item) => ({
    label: item.item_key,
    value: item.id
  }))
)
const evidenceOptions = computed(() => {
  const options: Array<{ label: string; value: string }> = []
  for (const item of props.items) {
    for (const side of ['policy', 'base', 'revised'] as const) {
      for (const evidence of item.evidence?.[side] ?? []) {
        options.push({ label: evidence.id, value: evidence.id })
      }
    }
  }
  return options
})

function formatTime(value?: string | null): string {
  const parsed = parseApiDate(value)
  return parsed ? parsed.toISOString() : ''
}

function locationLabel(row: RedlineRowResponse): string {
  const parts: string[] = []
  if (row.clause_no?.length) parts.push(row.clause_no.map((c) => `#${c}`).join(', '))
  if (row.section_no?.length) parts.push(...row.section_no)
  if (parts.length) return parts.join(' · ')
  if (row.location && typeof row.location === 'object') {
    return Object.entries(row.location)
      .map(([key, value]) => `${key}=${String(value)}`)
      .join(', ')
  }
  return ''
}

function currentDisposition(row: RedlineRowResponse): RedlineDisposition {
  return draftDisposition.value[row.id] ?? row.disposition
}

function currentNote(row: RedlineRowResponse): string {
  return draftNote.value[row.id] ?? row.note ?? ''
}

function currentItem(row: RedlineRowResponse): string | null {
  return row.id in draftItem.value ? draftItem.value[row.id] : row.item_id ?? null
}

function currentEvidence(row: RedlineRowResponse): string | null {
  return row.id in draftEvidence.value ? draftEvidence.value[row.id] : row.evidence_id ?? null
}

function setDraft(rowId: string, field: 'disposition' | 'note' | 'item' | 'evidence', value: unknown) {
  if (field === 'disposition') {
    draftDisposition.value = { ...draftDisposition.value, [rowId]: value as RedlineDisposition }
  } else if (field === 'note') {
    draftNote.value = { ...draftNote.value, [rowId]: value as string }
  } else if (field === 'item') {
    draftItem.value = { ...draftItem.value, [rowId]: value as string | null }
  } else {
    draftEvidence.value = { ...draftEvidence.value, [rowId]: value as string | null }
  }
}

function clearDrafts() {
  draftDisposition.value = {}
  draftNote.value = {}
  draftItem.value = {}
  draftEvidence.value = {}
}

const columns = computed<DataTableColumns<RedlineRowResponse>>(() => [
  {
    title: t('reviews.redlines.columns.ordinal'),
    key: 'ordinal',
    width: 70,
    render: (row) => String(row.ordinal)
  },
  {
    title: t('reviews.redlines.columns.revisionType'),
    key: 'revision_type',
    width: 100,
    render: (row) =>
      h(NTag, { size: 'small', bordered: false }, () => t(revisionKey(row.revision_type)))
  },
  {
    title: t('reviews.redlines.columns.before'),
    key: 'before_text',
    render: (row) => h('span', { 'data-testid': `redline-before-${row.id}` }, row.before_text ?? '—')
  },
  {
    title: t('reviews.redlines.columns.after'),
    key: 'after_text',
    render: (row) => h('span', { 'data-testid': `redline-after-${row.id}` }, row.after_text ?? '—')
  },
  {
    title: t('reviews.redlines.columns.author'),
    key: 'author',
    width: 140,
    render: (row) => {
      const author = row.author ?? row.comment_author ?? ''
      return h('div', {}, [
        h('div', author || '—'),
        row.authored_at || row.comment_date
          ? h('div', { class: 'text-xs text-slate-500' }, formatTime(row.authored_at || row.comment_date))
          : null
      ])
    }
  },
  {
    title: t('reviews.redlines.columns.location'),
    key: 'location',
    width: 160,
    render: (row) => h('span', { class: 'text-xs text-slate-600' }, locationLabel(row))
  },
  {
    title: t('reviews.redlines.columns.reason'),
    key: 'unparsed_reason',
    width: 180,
    render: (row) =>
      row.unparsed_reason?.length
        ? h(
            'div',
            { class: 'flex flex-wrap gap-1' },
            row.unparsed_reason.map((reason) =>
              h(NTag, { key: reason, size: 'small', bordered: false, type: 'warning' }, () => reason)
            )
          )
        : '—'
  },
  {
    title: t('reviews.redlines.columns.actions'),
    key: 'actions',
    width: 420,
    render: (row) =>
      h('div', { class: 'flex flex-col gap-2', 'data-testid': `redline-row-actions-${row.id}` }, [
        h('div', { class: 'flex flex-wrap items-center gap-2' }, [
          h(NSelect, {
            value: currentItem(row),
            options: itemOptions.value,
            placeholder: t('reviews.redlines.itemLink'),
            disabled: props.archived,
            clearable: true,
            style: 'width: 160px',
            'data-testid': `redline-item-select-${row.id}`,
            'aria-label': t('reviews.redlines.itemLink'),
            'onUpdate:value': (value: string | null) => setDraft(row.id, 'item', value)
          }),
          h(NSelect, {
            value: currentEvidence(row),
            options: evidenceOptions.value,
            placeholder: t('reviews.redlines.evidenceLink'),
            disabled: props.archived,
            clearable: true,
            style: 'width: 160px',
            'data-testid': `redline-evidence-select-${row.id}`,
            'aria-label': t('reviews.redlines.evidenceLink'),
            'onUpdate:value': (value: string | null) => setDraft(row.id, 'evidence', value)
          })
        ]),
        h(NRadioGroup, {
          value: currentDisposition(row),
          disabled: props.archived,
          size: 'small',
          'data-testid': `redline-disposition-${row.id}`,
          'onUpdate:value': (value: RedlineDisposition) => setDraft(row.id, 'disposition', value)
        }, () =>
          (['pending', 'reviewed', 'needs_clarification', 'not_relevant'] as const).map((value) =>
            h(NRadioButton, { key: value, value }, () => t(dispositionKey(value)))
          )
        ),
        h('div', { class: 'flex flex-wrap items-center gap-2' }, [
          h(NInput, {
            value: currentNote(row),
            placeholder: t('reviews.redlines.note'),
            disabled: props.archived,
            size: 'small',
            style: 'width: 220px',
            'data-testid': `redline-note-${row.id}`,
            'aria-label': t('reviews.redlines.note'),
            'onUpdate:value': (value: string) => setDraft(row.id, 'note', value)
          }),
          h(NButton, {
            size: 'small',
            type: 'primary',
            disabled: props.archived,
            loading: savingRowId.value === row.id,
            'data-testid': `redline-save-${row.id}`,
            onClick: () => saveDisposition(row)
          }, () => t('reviews.redlines.save'))
        ])
      ])
  }
])

async function loadSubmissions() {
  loading.value = true
  loadError.value = false
  try {
    const response = await reviewsApi.redlines.list(props.caseId)
    const data = apiUnwrap<RedlineSubmissionList | RedlineSubmissionSummary[]>(response)
    const items = Array.isArray(data) ? data : data?.items ?? []
    submissions.value = items
    if (!submissions.value.some((row) => row.id === selectedSubmissionId.value)) {
      selectedSubmissionId.value = submissions.value[0]?.id ?? null
    }
  } catch {
    loadError.value = true
  } finally {
    loading.value = false
  }
}

async function loadLedger() {
  if (!selectedSubmissionId.value) {
    ledger.value = null
    return
  }
  ledgerLoading.value = true
  ledgerLoadError.value = false
  try {
    // Empty filters must be omitted: FastAPI Literal|None rejects "" with 422.
    const params: RedlineLedgerQuery = { page: page.value, page_size: pageSize.value }
    if (revisionFilter.value) params.revision_type = revisionFilter.value
    if (dispositionFilter.value) params.disposition = dispositionFilter.value
    const response = await reviewsApi.redlines.get(
      props.caseId,
      selectedSubmissionId.value,
      params
    )
    ledger.value = apiUnwrap<RedlineLedgerResponse>(response)
    clearDrafts()
  } catch {
    ledgerLoadError.value = true
  } finally {
    ledgerLoading.value = false
  }
}

async function uploadFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file || props.archived) return
  uploading.value = true
  try {
    await reviewsApi.redlines.upload(props.caseId, file)
    message.success(t('reviews.redlines.uploadSuccess'))
    await loadSubmissions()
    await loadLedger()
  } catch (error) {
    message.error(getErrorMessage(error, t('reviews.redlines.uploadFailed')))
  } finally {
    uploading.value = false
    input.value = ''
  }
}

async function saveDisposition(row: RedlineRowResponse) {
  if (!selectedSubmissionId.value || props.archived) return
  savingRowId.value = row.id
  try {
    await reviewsApi.redlines.updateDisposition(
      props.caseId,
      selectedSubmissionId.value,
      row.id,
      {
        disposition: currentDisposition(row),
        note: currentNote(row) || null,
        expected_lock_version: row.lock_version,
        item_id: currentItem(row),
        evidence_id: currentEvidence(row)
      }
    )
    message.success(t('reviews.redlines.saveSuccess'))
    await loadLedger()
  } catch (error) {
    message.error(getErrorMessage(error, t('reviews.redlines.saveFailed')))
  } finally {
    savingRowId.value = ''
  }
}

function resetFilters() {
  page.value = 1
  void loadLedger()
}

watch(selectedSubmissionId, () => {
  page.value = 1
  void loadLedger()
})
watch([revisionFilter, dispositionFilter], () => {
  resetFilters()
})
onMounted(() => {
  void loadSubmissions()
})
</script>

<template>
  <section class="mt-8" data-testid="redline-ledger">
    <div class="mb-2 text-base font-semibold text-gray-700 dark:text-gray-200">
      {{ t('reviews.redlines.title') }}
    </div>

    <div class="mb-3 flex flex-wrap items-center gap-2">
      <label class="text-sm text-slate-600 dark:text-slate-300">
        {{ t('reviews.redlines.uploadLabel') }}
        <input
          ref="fileInput"
          type="file"
          class="ml-2 text-xs"
          :disabled="archived || uploading"
          data-testid="redline-upload"
          @change="uploadFile"
        />
      </label>
      <n-button
        size="small"
        type="primary"
        secondary
        :loading="uploading"
        :disabled="archived"
        data-testid="redline-upload-button"
        @click="fileInput?.click()"
      >
        {{ t('reviews.redlines.uploadButton') }}
      </n-button>
    </div>

    <n-alert v-if="loadError" type="error" class="mb-3" :bordered="false">
      {{ t('reviews.redlines.loadFailed') }}
    </n-alert>

    <n-empty
      v-if="!loading && submissions.length === 0"
      class="review-redline-empty"
      :description="t('reviews.redlines.noSubmissions')"
    />

    <template v-else>
      <div class="mb-3 flex flex-wrap items-center gap-2">
        <n-select
          v-model:value="selectedSubmissionId"
          :options="submissionOptions"
          :placeholder="t('reviews.redlines.selectSubmission')"
          :aria-label="t('reviews.redlines.selectSubmission')"
          class="w-64"
          data-testid="redline-submission-select"
        />
        <n-select
          v-model:value="revisionFilter"
          :options="revisionOptions"
          :aria-label="t('reviews.redlines.filters.revisionType')"
          class="w-40"
          data-testid="redline-revision-filter"
        />
        <n-select
          v-model:value="dispositionFilter"
          :options="dispositionOptions"
          :aria-label="t('reviews.redlines.filters.disposition')"
          class="w-40"
          data-testid="redline-disposition-filter"
        />
      </div>

      <n-alert v-if="ledgerLoadError" type="error" class="mb-3" :bordered="false">
        {{ t('reviews.redlines.ledgerLoadFailed') }}
      </n-alert>
      <n-alert v-if="ledger?.has_unparsed" type="warning" class="mb-3" :bordered="false">
        {{ t('reviews.redlines.hasUnparsed') }}
      </n-alert>
      <div v-if="ledger?.counts" class="mb-2 flex flex-wrap gap-3 text-xs text-slate-600 dark:text-slate-300">
        <span>{{ t('reviews.redlines.counts.revision', { count: ledger.counts.revision }) }}</span>
        <span>{{ t('reviews.redlines.counts.comment', { count: ledger.counts.comment }) }}</span>
        <span>{{ t('reviews.redlines.counts.unparsed', { count: ledger.counts.unparsed }) }}</span>
      </div>

      <n-spin :show="ledgerLoading">
        <n-data-table
          :columns="columns"
          :data="ledger?.items ?? []"
          :bordered="false"
          :single-line="false"
          :scroll-x="1280"
          data-testid="redline-table"
        />
      </n-spin>
    </template>

    <div class="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300" data-testid="redline-disclaimer">
      <div>{{ t('reviews.redlines.disclaimerZh') }}</div>
      <div>{{ t('reviews.redlines.disclaimerEn') }}</div>
      <div>{{ t('reviews.redlines.disclaimerJa') }}</div>
      <div>{{ t('reviews.redlines.disclaimerFr') }}</div>
    </div>
  </section>
</template>
