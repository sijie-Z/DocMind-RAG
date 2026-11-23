<template>
  <div class="p-4 md:p-6">
    <div class="max-w-7xl mx-auto">
      <PageBreadcrumb :show-back="true" class="mb-4" />
      <div class="mb-6 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div class="min-w-0">
          <h1 class="truncate text-2xl font-semibold tracking-tight text-slate-900 dark:text-slate-100 md:text-[28px]">
            {{ detail?.name || t('router.reviewTemplates') }}
          </h1>
          <p v-if="detail?.description" class="mt-1 text-sm text-slate-500 dark:text-slate-400">
            {{ detail.description }}
          </p>
          <div class="mt-2 flex flex-wrap items-center gap-1">
            <n-tag
              v-if="detail"
              :type="detail.status === 'archived' ? 'default' : 'success'"
              size="small"
              data-testid="template-detail-status"
            >
              {{ detail.status === 'archived' ? t('reviewTemplates.statusArchived') : t('reviewTemplates.statusActive') }}
            </n-tag>
            <n-tag v-if="detail" size="small" :bordered="false">
              {{ t('reviewTemplates.versionTag', { n: detail.latest_version }) }}
            </n-tag>
          </div>
        </div>
        <div class="flex flex-wrap gap-2">
          <n-button secondary :loading="loading" @click="loadAll">
            {{ t('reviewTemplates.refresh') }}
          </n-button>
          <n-popconfirm
            v-if="detail && detail.status !== 'archived'"
            :positive-button-props="{ loading: archiving, disabled: archiving }"
            @positive-click="archiveTemplate"
          >
            <template #trigger>
              <n-button secondary type="warning" :loading="archiving" data-testid="template-archive">
                {{ t('reviewTemplates.archive') }}
              </n-button>
            </template>
            {{ t('reviewTemplates.archiveConfirm') }}
          </n-popconfirm>
        </div>
      </div>

      <n-alert v-if="loadError" type="error" class="mb-4" :bordered="false">
        {{ t('reviewTemplates.detailLoadFailed') }}
        <n-button text type="primary" class="ml-2" @click="loadAll">{{ t('reviews.retry') }}</n-button>
      </n-alert>
      <n-alert
        v-if="detail && detail.status === 'archived'"
        type="info"
        class="mb-4"
        :bordered="false"
        data-testid="template-archived-banner"
      >
        {{ t('reviewTemplates.archivedBanner') }}
      </n-alert>

      <n-spin :show="loading">
        <div class="grid gap-4 lg:grid-cols-[320px,1fr]">
          <!-- 版本历史 -->
          <section class="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900">
            <div class="mb-3 flex items-center justify-between">
              <h2 class="text-sm font-semibold text-slate-900 dark:text-slate-100">
                {{ t('reviewTemplates.versionHistory') }}
              </h2>
              <span class="text-xs tabular-nums text-slate-500 dark:text-slate-400">
                {{ t('reviewTemplates.versionCount', { n: versions.length }) }}
              </span>
            </div>
            <div v-if="versions.length === 0" class="text-xs text-slate-500 dark:text-slate-400" data-testid="versions-empty">
              {{ t('reviewTemplates.versionsEmpty') }}
            </div>
            <ul v-else class="space-y-1" data-testid="version-list">
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
                  :data-testid="`version-select-${row.version_no}`"
                  @click="selectVersion(row.version_no)"
                >
                  {{ row.version_label }}
                </n-button>
                <span class="text-[11px] tabular-nums text-slate-500 dark:text-slate-400">
                  {{ row.item_count }} {{ t('reviewTemplates.itemsUnit') }}
                </span>
                <span v-if="row.source === 'bootstrap'" class="text-[11px] text-amber-700 dark:text-amber-500">
                  {{ t('reviewTemplates.sourceBootstrap') }}
                </span>
              </li>
            </ul>
            <div class="mt-3 flex items-center gap-2">
              <n-button
                size="small"
                secondary
                :disabled="versions.length < 2 || archiveReadOnly"
                data-testid="diff-open"
                @click="openDiff"
              >
                {{ t('reviewTemplates.diffAction') }}
              </n-button>
              <span class="text-[11px] text-slate-500 dark:text-slate-400">
                {{ t('reviewTemplates.diffHint') }}
              </span>
            </div>
          </section>

          <!-- 版本内容 -->
          <section class="min-w-0 rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900">
            <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
              <h2 class="text-sm font-semibold text-slate-900 dark:text-slate-100">
                {{ t('reviewTemplates.versionContent') }}
                <span v-if="selectedVersionRow" class="ml-1 text-xs font-normal text-slate-500 dark:text-slate-400">
                  {{ selectedVersionRow.version_label }}
                </span>
              </h2>
              <n-tag v-if="selectedVersionRow && !selectedVersionRow.intact" type="error" size="small" data-testid="version-corrupt">
                {{ t('reviewTemplates.versionCorrupt') }}
              </n-tag>
            </div>
            <p v-if="selectedVersionRow?.change_note" class="mb-2 text-xs text-slate-500 dark:text-slate-400">
              {{ t('reviewTemplates.changeNoteLabel') }}: {{ selectedVersionRow.change_note }}
            </p>
            <div v-if="!selectedVersionRow" class="text-xs text-slate-500 dark:text-slate-400">
              {{ t('reviewTemplates.versionSelectHint') }}
            </div>
            <div v-else class="overflow-x-auto">
              <table class="w-full border-collapse text-left text-sm" data-testid="version-items-table">
                <thead>
                  <tr class="border-b border-slate-200 dark:border-slate-700">
                    <th class="py-1.5 pr-3 text-xs font-medium text-slate-500 dark:text-slate-400">
                      {{ t('reviewTemplates.itemKey') }}
                    </th>
                    <th class="py-1.5 pr-3 text-xs font-medium text-slate-500 dark:text-slate-400">
                      {{ t('reviewTemplates.itemLabel') }}
                    </th>
                    <th class="py-1.5 pr-3 text-xs font-medium text-slate-500 dark:text-slate-400">
                      {{ t('reviewTemplates.itemKeywords') }}
                    </th>
                    <th class="py-1.5 text-xs font-medium text-slate-500 dark:text-slate-400">
                      {{ t('reviewTemplates.itemKind') }}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="item in selectedVersionRow.items"
                    :key="item.key"
                    class="border-b border-slate-100 align-top dark:border-slate-800"
                    :data-testid="`version-item-${item.key}`"
                  >
                    <td class="py-1.5 pr-3 font-mono text-xs text-slate-700 dark:text-slate-300">{{ item.key }}</td>
                    <td class="py-1.5 pr-3 text-xs text-slate-700 dark:text-slate-300">{{ item.label || '—' }}</td>
                    <td class="py-1.5 pr-3 text-xs text-slate-600 dark:text-slate-400">
                      {{ (item.keywords || []).join(' · ') || '—' }}
                    </td>
                    <td class="py-1.5 text-xs">
                      <n-tag size="tiny" :bordered="false">
                        {{ item.configurable ? t('reviewTemplates.itemKindCustom') : t('reviewTemplates.itemKindBuiltin') }}
                      </n-tag>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>
        </div>
      </n-spin>
    </div>

    <!-- 只读 diff：两个版本之间的规则差异（按 item key 对齐，关键词按集合语义） -->
    <n-modal
      :show="diffVisible"
      preset="card"
      :title="t('reviewTemplates.diffTitle')"
      class="max-w-3xl"
      data-testid="diff-modal"
      @update:show="(value: boolean) => (!value ? (diffVisible = false) : undefined)"
    >
      <div class="flex flex-col gap-3">
        <div class="flex flex-wrap items-end gap-3">
          <div>
            <div class="mb-1 text-xs text-slate-500 dark:text-slate-400">{{ t('reviewTemplates.diffBase') }}</div>
            <n-select
              v-model:value="diffBase"
              :options="versionOptions"
              class="w-36"
              size="small"
              data-testid="diff-base-select"
              @update:value="runDiff"
            />
          </div>
          <div>
            <div class="mb-1 text-xs text-slate-500 dark:text-slate-400">{{ t('reviewTemplates.diffTarget') }}</div>
            <n-select
              v-model:value="diffTarget"
              :options="versionOptions"
              class="w-36"
              size="small"
              data-testid="diff-target-select"
              @update:value="runDiff"
            />
          </div>
          <n-button
            size="small"
            secondary
            :loading="diffLoading"
            data-testid="diff-run"
            @click="runDiff"
          >
            {{ t('reviewTemplates.diffRun') }}
          </n-button>
        </div>
        <n-alert v-if="diffError" type="error" :bordered="false" data-testid="diff-error">{{ diffError }}</n-alert>
        <n-spin :show="diffLoading">
          <div v-if="diff">
            <p class="mb-2 text-xs text-slate-600 dark:text-slate-300" data-testid="diff-summary">
              {{
                t('reviewTemplates.diffSummary', {
                  added: diff.summary.added,
                  removed: diff.summary.removed,
                  changed: diff.summary.changed,
                  unchanged: diff.summary.unchanged
                })
              }}
            </p>
            <p v-if="diff.identical" class="mb-2 text-xs text-emerald-700 dark:text-emerald-500" data-testid="diff-identical">
              {{ t('reviewTemplates.diffIdentical') }}
            </p>
            <div v-if="diff.added.length" class="mb-2" data-testid="diff-added">
              <div class="text-xs font-medium text-emerald-700 dark:text-emerald-500">
                + {{ t('reviewTemplates.diffAdded') }}
              </div>
              <div v-for="item in diff.added" :key="item.key" class="ml-3 text-xs text-slate-700 dark:text-slate-300">
                <span class="font-mono">{{ item.key }}</span> · {{ (item.keywords || []).join(' · ') }}
              </div>
            </div>
            <div v-if="diff.removed.length" class="mb-2" data-testid="diff-removed">
              <div class="text-xs font-medium text-red-600 dark:text-red-400">
                − {{ t('reviewTemplates.diffRemoved') }}
              </div>
              <div v-for="item in diff.removed" :key="item.key" class="ml-3 text-xs text-slate-700 dark:text-slate-300">
                <span class="font-mono">{{ item.key }}</span> · {{ (item.keywords || []).join(' · ') }}
              </div>
            </div>
            <div v-if="diff.changed.length" data-testid="diff-changed">
              <div class="text-xs font-medium text-amber-700 dark:text-amber-500">
                ~ {{ t('reviewTemplates.diffChanged') }}
              </div>
              <div
                v-for="entry in diff.changed"
                :key="entry.key"
                class="ml-3 mb-1 text-xs text-slate-700 dark:text-slate-300"
                :data-testid="`diff-changed-${entry.key}`"
              >
                <span class="font-mono">{{ entry.key }}</span>
                <span v-if="entry.fields.keywords" class="ml-2">
                  +{{ (entry.fields.keywords.added || []).join(' · ') || '—' }}
                  / −{{ (entry.fields.keywords.removed || []).join(' · ') || '—' }}
                </span>
                <span v-if="entry.fields.label" class="ml-2">
                  {{ t('reviewTemplates.diffLabelChange', { base: entry.fields.label.base, target: entry.fields.label.target }) }}
                </span>
              </div>
            </div>
          </div>
        </n-spin>
      </div>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  NAlert,
  NButton,
  NModal,
  NPopconfirm,
  NSelect,
  NSpin,
  NTag,
  useMessage
} from 'naive-ui'
import PageBreadcrumb from '@/components/common/PageBreadcrumb.vue'
import {
  reviewsApi,
  type ReviewTemplateDetail,
  type ReviewTemplateDiff,
  type ReviewTemplateVersion,
  type ReviewTemplateVersionSummary
} from '@/api/reviews'
import { apiUnwrap } from '@/utils/apiUnwrap'
import { getErrorMessage } from '@/utils/apiError'

const { t } = useI18n()
const route = useRoute()
const message = useMessage()

const templateId = computed(() => String(route.params.id || ''))
const detail = ref<ReviewTemplateDetail | null>(null)
const versions = ref<ReviewTemplateVersionSummary[]>([])
const selectedVersion = ref<number | null>(null)
const selectedVersionRow = ref<ReviewTemplateVersion | null>(null)
const loading = ref(false)
const loadError = ref(false)
const archiving = ref(false)

const diffVisible = ref(false)
const diffLoading = ref(false)
const diffError = ref('')
const diff = ref<ReviewTemplateDiff | null>(null)
const diffBase = ref<number | null>(null)
const diffTarget = ref<number | null>(null)

const archiveReadOnly = computed(() => detail.value?.status === 'archived')

const versionOptions = computed(() =>
  versions.value.map((row) => ({ value: row.version_no, label: row.version_label }))
)

async function loadAll() {
  if (!templateId.value) return
  loading.value = true
  loadError.value = false
  try {
    const [detailRes, versionsRes] = await Promise.all([
      reviewsApi.templates.get(templateId.value),
      reviewsApi.templates.versions(templateId.value, { page: 1, page_size: 100 })
    ])
    detail.value = apiUnwrap<ReviewTemplateDetail>(detailRes)
    const list = apiUnwrap<{ items: ReviewTemplateVersionSummary[] }>(versionsRes)
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
  } catch {
    loadError.value = true
  } finally {
    loading.value = false
  }
}

async function loadVersion(versionNo: number) {
  try {
    const res = await reviewsApi.templates.getVersion(templateId.value, versionNo)
    selectedVersionRow.value = apiUnwrap<ReviewTemplateVersion>(res)
  } catch {
    selectedVersionRow.value = null
  }
}

async function selectVersion(versionNo: number) {
  selectedVersion.value = versionNo
  await loadVersion(versionNo)
}

function openDiff() {
  if (versions.value.length < 2) return
  const latest = versions.value[0].version_no
  const previous = versions.value[1].version_no
  diffBase.value = previous
  diffTarget.value = latest
  diffVisible.value = true
  void runDiff()
}

async function runDiff() {
  if (diffBase.value === null || diffTarget.value === null) return
  diffLoading.value = true
  diffError.value = ''
  try {
    const res = await reviewsApi.templates.diff(templateId.value, diffBase.value, diffTarget.value)
    diff.value = apiUnwrap<ReviewTemplateDiff>(res)
  } catch (error) {
    diff.value = null
    diffError.value = getErrorMessage(error, t('reviewTemplates.diffFailed'))
  } finally {
    diffLoading.value = false
  }
}

async function archiveTemplate() {
  if (archiving.value) return
  archiving.value = true
  try {
    await reviewsApi.templates.archive(templateId.value)
    message.success(t('reviewTemplates.archiveDone'))
    await loadAll()
  } catch (error) {
    message.error(getErrorMessage(error, t('reviewTemplates.archiveFailed')))
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
  diff,
  diffVisible,
  diffBase,
  diffTarget,
  openDiff,
  runDiff,
  archiveTemplate,
  loadAll,
  selectVersion
})
</script>
