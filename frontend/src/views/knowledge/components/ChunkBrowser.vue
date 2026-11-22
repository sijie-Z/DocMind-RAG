<template>
  <section
    class="mt-4 overflow-hidden rounded-lg border border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-900"
    data-testid="chunk-browser"
    aria-labelledby="chunk-browser-title"
  >
    <div class="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-5 py-3 dark:border-slate-800">
      <div class="min-w-0">
        <h3 id="chunk-browser-title" class="text-sm font-semibold text-slate-800 dark:text-slate-100">
          {{ t('knowledge.chunkBrowser.title') }}
        </h3>
        <p class="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
          {{ t('knowledge.chunkBrowser.subtitle') }}
        </p>
      </div>
      <span v-if="total" class="shrink-0 text-xs tabular-nums text-slate-500 dark:text-slate-400">
        {{ t('knowledge.chunkBrowser.total', { count: total }) }}
      </span>
    </div>

    <div class="flex flex-wrap items-end gap-3 border-b border-slate-100 px-5 py-3 dark:border-slate-800">
      <label class="flex min-w-[180px] flex-1 flex-col gap-1">
        <span class="text-xs text-slate-600 dark:text-slate-300">{{ t('knowledge.chunkBrowser.search') }}</span>
        <n-input
          v-model:value="searchInput"
          size="small"
          clearable
          data-testid="chunk-browser-search"
          :placeholder="t('knowledge.chunkBrowser.searchPlaceholder')"
          @keyup.enter="applyFilters"
        />
      </label>
      <label class="flex flex-col gap-1">
        <span class="text-xs text-slate-600 dark:text-slate-300">{{ t('knowledge.chunkBrowser.status') }}</span>
        <n-select
          v-model:value="enabledFilter"
          size="small"
          :options="enabledOptions"
          data-testid="chunk-browser-enabled"
          class="w-32"
        />
      </label>
      <label class="flex flex-col gap-1">
        <span class="text-xs text-slate-600 dark:text-slate-300">{{ t('knowledge.chunkBrowser.override') }}</span>
        <n-select
          v-model:value="overrideFilter"
          size="small"
          :options="overrideOptions"
          data-testid="chunk-browser-override-filter"
          class="w-32"
        />
      </label>
      <n-button size="small" secondary data-testid="chunk-browser-apply" @click="applyFilters">
        {{ t('knowledge.chunkBrowser.apply') }}
      </n-button>
    </div>

    <n-alert
      v-if="loadError"
      type="error"
      :bordered="false"
      class="m-4"
      data-testid="chunk-browser-error"
      role="alert"
    >
      {{ loadError }}
    </n-alert>

    <div v-else-if="loading" class="p-5" aria-busy="true" data-testid="chunk-browser-loading">
      <n-skeleton text :repeat="4" />
    </div>

    <div v-else-if="!items.length" class="p-8" data-testid="chunk-browser-empty">
      <n-empty :description="t('knowledge.chunkBrowser.empty')" />
    </div>

    <div v-else class="divide-y divide-slate-100 dark:divide-slate-800">
      <article
        v-for="item in items"
        :key="item.chunk_id"
        class="p-5"
        data-testid="chunk-browser-row"
      >
        <div class="flex flex-wrap items-center gap-2">
          <span class="text-sm font-medium tabular-nums text-slate-800 dark:text-slate-100">
            {{ t('knowledge.chunkBrowser.chunkIndex', { index: item.chunk_index }) }}
          </span>
          <n-tag size="small" :type="item.is_enabled ? 'success' : 'warning'" :bordered="false" round>
            {{ item.is_enabled ? t('knowledge.chunkBrowser.enabled') : t('knowledge.chunkBrowser.disabled') }}
          </n-tag>
          <n-tag size="small" quaternary round data-testid="chunk-provenance">
            {{ provenanceLabel(item.effective_text_provenance) }}
          </n-tag>
          <n-tag v-if="item.override_state === 'active'" size="small" quaternary round>
            {{ t('knowledge.chunkBrowser.overrideActive') }}
          </n-tag>
          <span class="text-xs tabular-nums text-slate-500 dark:text-slate-400">
            {{ locatorLabel(item) }}
          </span>
        </div>

        <p class="mt-2 whitespace-pre-wrap break-words rounded-md border border-slate-100 bg-slate-50/70 p-3 text-sm leading-6 text-slate-700 dark:border-slate-800 dark:bg-slate-800/40 dark:text-slate-200">
          {{ item.indexed_text }}
        </p>

        <details class="mt-2" data-testid="chunk-original-detail">
          <summary class="cursor-pointer text-xs font-medium text-slate-600 dark:text-slate-300">
            {{ t('knowledge.chunkBrowser.originalToggle') }}
          </summary>
          <p v-if="item.parser_original_available" class="mt-2 whitespace-pre-wrap break-words rounded-md border border-slate-100 p-3 text-xs leading-5 text-slate-600 dark:border-slate-800 dark:text-slate-300">
            {{ item.parser_original_text }}
          </p>
          <p v-else class="mt-2 text-xs text-amber-700 dark:text-amber-400">
            {{ t('knowledge.chunkBrowser.originalUnavailable') }}
          </p>
        </details>

        <div v-if="canEdit" class="mt-3 flex flex-wrap items-center gap-2">
          <n-button size="small" secondary data-testid="chunk-edit" @click="startEdit(item)">
            {{ t('knowledge.chunkBrowser.edit') }}
          </n-button>
          <n-button
            size="small"
            :type="item.is_enabled ? 'default' : 'primary'"
            secondary
            data-testid="chunk-toggle-enabled"
            :loading="busyId === item.chunk_id"
            @click="toggleEnabled(item)"
          >
            {{ item.is_enabled ? t('knowledge.chunkBrowser.disable') : t('knowledge.chunkBrowser.enable') }}
          </n-button>
          <n-button
            v-if="item.override_state === 'active'"
            size="small"
            quaternary
            data-testid="chunk-reset"
            :loading="busyId === item.chunk_id"
            @click="resetOverride(item)"
          >
            {{ t('knowledge.chunkBrowser.reset') }}
          </n-button>
        </div>

        <div v-if="editingId === item.chunk_id" class="mt-3 rounded-md border border-slate-200 p-3 dark:border-slate-700">
          <label class="flex flex-col gap-1">
            <span class="text-xs font-medium text-slate-700 dark:text-slate-200">
              {{ t('knowledge.chunkBrowser.editorLabel') }}
            </span>
            <n-input
              v-model:value="editText"
              type="textarea"
              :autosize="{ minRows: 3, maxRows: 12 }"
              data-testid="chunk-edit-text"
            />
          </label>
          <p class="mt-2 text-xs leading-5 text-slate-500 dark:text-slate-400">
            {{ t('knowledge.chunkBrowser.editorWarning') }}
          </p>
          <div class="mt-3 flex flex-wrap gap-2">
            <n-button
              type="primary"
              size="small"
              data-testid="chunk-save-active"
              :loading="busyId === item.chunk_id"
              @click="saveOverride(item, true)"
            >
              {{ t('knowledge.chunkBrowser.saveAndApply') }}
            </n-button>
            <n-button
              size="small"
              secondary
              data-testid="chunk-save-draft"
              :loading="busyId === item.chunk_id"
              @click="saveOverride(item, false)"
            >
              {{ t('knowledge.chunkBrowser.saveDraft') }}
            </n-button>
            <n-button size="small" quaternary data-testid="chunk-cancel-edit" @click="editingId = ''">
              {{ t('common.cancel') }}
            </n-button>
          </div>
        </div>
      </article>
    </div>

    <div v-if="pages > 1" class="flex justify-end border-t border-slate-100 px-5 py-3 dark:border-slate-800">
      <n-pagination
        v-model:page="page"
        :page-count="pages"
        :page-size="pageSize"
        data-testid="chunk-browser-pagination"
        @update:page="onPageChange"
      />
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  NAlert,
  NButton,
  NEmpty,
  NInput,
  NPagination,
  NSelect,
  NSkeleton,
  NTag
} from 'naive-ui'
import { useUserStore } from '@/stores/user'
import { useDedupedMessage } from '@/utils/message'
import { getErrorMessage } from '@/utils/apiError'
import {
  getDocumentChunks,
  resetDocumentChunkOverride,
  setDocumentChunkEnabled,
  setDocumentChunkOverride,
  type DocumentChunkBrowserItem
} from '@/api/knowledge'

const props = defineProps<{ documentId: string }>()

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const message = useDedupedMessage()

// RBAC 只控制入口显隐；真正的权限永远由后端 scope + permission + ACL 判定。
// 独立页/轻量宿主可能未安装 Pinia：未知一律按可写展示（后端仍会拒绝越权）。
const userStore = (() => {
  try {
    return useUserStore()
  } catch {
    return null
  }
})()
const canEdit = computed(() => userStore?.can('document:update') !== false)

const items = ref<DocumentChunkBrowserItem[]>([])
const total = ref(0)
const pages = ref(0)
const loading = ref(false)
const loadError = ref('')
const busyId = ref('')
const editingId = ref('')
const editText = ref('')

const queryString = (value: unknown): string =>
  typeof value === 'string' ? value : Array.isArray(value) && typeof value[0] === 'string' ? value[0] : ''
const readPage = () => {
  const parsed = Number.parseInt(queryString(route.query.chunkPage) || '1', 10)
  return Number.isFinite(parsed) && parsed > 0 ? Math.min(parsed, 1_000_000) : 1
}
const page = ref(readPage())
const pageSize = 20

const enabledFilter = ref<'all' | 'enabled' | 'disabled'>(
  queryString(route.query.chunkEnabled) === 'enabled' || queryString(route.query.chunkEnabled) === 'disabled'
    ? (queryString(route.query.chunkEnabled) as 'enabled' | 'disabled')
    : 'all'
)
const overrideFilter = ref<'all' | 'active' | 'draft' | 'disabled'>(
  ['active', 'draft', 'disabled'].includes(queryString(route.query.chunkOverride))
    ? (queryString(route.query.chunkOverride) as 'active' | 'draft' | 'disabled')
    : 'all'
)
const searchInput = ref(queryString(route.query.chunkQuery))
const appliedQuery = ref(searchInput.value)

const enabledOptions = computed(() => [
  { label: t('knowledge.chunkBrowser.statusAll'), value: 'all' },
  { label: t('knowledge.chunkBrowser.enabled'), value: 'enabled' },
  { label: t('knowledge.chunkBrowser.disabled'), value: 'disabled' }
])
const overrideOptions = computed(() => [
  { label: t('knowledge.chunkBrowser.overrideAll'), value: 'all' },
  { label: t('knowledge.chunkBrowser.overrideActive'), value: 'active' },
  { label: t('knowledge.chunkBrowser.overrideDraft'), value: 'draft' },
  { label: t('knowledge.chunkBrowser.disabled'), value: 'disabled' }
])

/** URL 是列表状态真相；过滤/分页变化写回 query，前进后退可复现。 */
const syncUrl = async () => {
  const next = { ...route.query }
  const put = (key: string, value: string, fallback: string) => {
    if (value === fallback || !value) delete next[key]
    else next[key] = value
  }
  put('chunkPage', String(page.value), '1')
  put('chunkEnabled', enabledFilter.value, 'all')
  put('chunkOverride', overrideFilter.value, 'all')
  put('chunkQuery', appliedQuery.value, '')
  if (JSON.stringify(next) === JSON.stringify(route.query)) return
  await router.replace({ name: route.name as string, params: route.params, query: next })
}

const load = async () => {
  const docId = props.documentId
  if (!docId) return
  const seq = ++loadSeq
  loading.value = true
  loadError.value = ''
  try {
    const res = await getDocumentChunks(
      docId,
      {
        page: page.value,
        page_size: pageSize,
        enabled: enabledFilter.value === 'all' ? undefined : enabledFilter.value === 'enabled',
        override_state: overrideFilter.value === 'all' ? undefined : overrideFilter.value,
        q: appliedQuery.value || undefined
      },
      { silent: true }
    )
    const data = (res.data as { data?: { items?: DocumentChunkBrowserItem[]; total?: number; pages?: number } })?.data
    if (!data || !Array.isArray(data.items)) throw new Error('invalid chunk browser payload')
    if (seq !== loadSeq) return
    items.value = data.items
    total.value = data.total ?? data.items.length
    pages.value = data.pages ?? 0
    editingId.value = ''
  } catch (error) {
    if (seq !== loadSeq) return
    items.value = []
    total.value = 0
    pages.value = 0
    loadError.value = t('knowledge.chunkBrowser.loadFailed', { reason: getErrorMessage(error, '') })
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

let loadSeq = 0

const applyFilters = async () => {
  appliedQuery.value = searchInput.value.trim()
  page.value = 1
  await syncUrl()
  await load()
}

const onPageChange = async () => {
  await syncUrl()
  await load()
}

const provenanceLabel = (value: string): string => {
  if (value === 'parser_original') return t('knowledge.chunkBrowser.provenanceOriginal')
  if (value === 'manual_override') return t('knowledge.chunkBrowser.provenanceManual')
  return t('knowledge.chunkBrowser.provenanceLegacy')
}

const locatorLabel = (item: DocumentChunkBrowserItem): string => {
  const parts: string[] = []
  if (item.page_number) parts.push(t('knowledge.documentDetail.pageMeta', { page: item.page_number }))
  if (item.clause_no.length) parts.push(t('knowledge.chunkBrowser.clause', { no: item.clause_no.join(',') }))
  if (item.section_no.length) parts.push(t('knowledge.chunkBrowser.section', { no: item.section_no.join(',') }))
  if (item.start_pos !== null && item.end_pos !== null) {
    parts.push(`${item.start_pos}-${item.end_pos}`)
  }
  return parts.join(' · ')
}

const replaceItem = (updated: DocumentChunkBrowserItem) => {
  const idx = items.value.findIndex((row) => row.chunk_id === updated.chunk_id)
  if (idx >= 0) items.value.splice(idx, 1, updated)
}

const startEdit = (item: DocumentChunkBrowserItem) => {
  editingId.value = item.chunk_id
  editText.value = item.override_text ?? item.indexed_text
}

const saveOverride = async (item: DocumentChunkBrowserItem, apply: boolean) => {
  if (busyId.value) return
  if (!editText.value.trim()) {
    message.warning(t('knowledge.chunkBrowser.emptyText'))
    return
  }
  busyId.value = item.chunk_id
  try {
    const res = await setDocumentChunkOverride(props.documentId, {
      chunk_id: item.chunk_id,
      text: editText.value,
      apply_to_retrieval: apply
    })
    const updated = (res.data as { data?: DocumentChunkBrowserItem })?.data
    if (!updated) throw new Error('invalid override payload')
    replaceItem(updated)
    editingId.value = ''
    message.success(t(apply ? 'knowledge.chunkBrowser.savedApplied' : 'knowledge.chunkBrowser.savedDraft'))
  } catch (error) {
    message.error(t('knowledge.chunkBrowser.saveFailed', { reason: getErrorMessage(error, '') }))
  } finally {
    busyId.value = ''
  }
}

const toggleEnabled = async (item: DocumentChunkBrowserItem) => {
  if (busyId.value) return
  busyId.value = item.chunk_id
  try {
    const res = await setDocumentChunkEnabled(props.documentId, {
      chunk_id: item.chunk_id,
      enabled: !item.is_enabled
    })
    const updated = (res.data as { data?: DocumentChunkBrowserItem })?.data
    if (!updated) throw new Error('invalid enabled payload')
    replaceItem(updated)
    message.success(t(updated.is_enabled ? 'knowledge.chunkBrowser.enabledDone' : 'knowledge.chunkBrowser.disabledDone'))
  } catch (error) {
    message.error(t('knowledge.chunkBrowser.saveFailed', { reason: getErrorMessage(error, '') }))
  } finally {
    busyId.value = ''
  }
}

const resetOverride = async (item: DocumentChunkBrowserItem) => {
  if (busyId.value) return
  busyId.value = item.chunk_id
  try {
    const res = await resetDocumentChunkOverride(props.documentId, item.chunk_id)
    const updated = (res.data as { data?: DocumentChunkBrowserItem })?.data
    if (!updated) throw new Error('invalid reset payload')
    replaceItem(updated)
    message.success(t('knowledge.chunkBrowser.resetDone'))
  } catch (error) {
    message.error(t('knowledge.chunkBrowser.saveFailed', { reason: getErrorMessage(error, '') }))
  } finally {
    busyId.value = ''
  }
}

watch(
  () => props.documentId,
  () => {
    page.value = readPage()
    editingId.value = ''
    void load()
  },
  { immediate: true }
)
</script>
