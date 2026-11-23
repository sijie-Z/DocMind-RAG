<template>
  <div class="p-4 md:p-6">
    <div class="max-w-7xl mx-auto">
      <PageBreadcrumb :show-back="true" class="mb-4" />
      <div class="mb-6 flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h1 class="text-2xl font-semibold tracking-tight text-slate-900 dark:text-slate-100 md:text-[28px]">
            {{ t('router.reviewTemplates') }}
          </h1>
          <p class="text-slate-500 dark:text-slate-400 mt-1 text-sm flex items-center gap-2">
            <span class="w-6 h-0.5 bg-blue-600 rounded-full"></span>
            {{ t('reviewTemplates.subtitle') }}
          </p>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <n-select
            v-model:value="statusFilter"
            :options="statusOptions"
            :aria-label="t('reviewTemplates.statusColumn')"
            class="w-40"
            data-testid="template-status-filter"
            @update:value="applyFilter"
          />
          <n-button type="primary" round data-testid="template-create-open" @click="openCreate">
            {{ t('reviewTemplates.create') }}
          </n-button>
          <n-button secondary round data-testid="template-bootstrap" :loading="bootstrapping" @click="bootstrap">
            {{ t('reviewTemplates.bootstrap') }}
          </n-button>
        </div>
      </div>

      <n-alert v-if="loadError" type="error" class="mb-4" :bordered="false">
        {{ t('reviewTemplates.loadFailed') }}
        <n-button text type="primary" class="ml-2" @click="load">{{ t('reviews.retry') }}</n-button>
      </n-alert>

      <n-spin :show="loading">
        <div
          v-if="!loading && !loadError && rows.length === 0"
          class="flex flex-col items-center justify-center rounded-2xl border border-dashed border-slate-200 bg-white px-6 py-20 dark:border-slate-700 dark:bg-slate-900"
          data-testid="templates-empty-state"
        >
          <div class="mb-5 flex h-16 w-16 items-center justify-center rounded-2xl border border-slate-200 bg-slate-50 dark:border-slate-700 dark:bg-slate-800">
            <n-icon size="30" class="text-slate-500 dark:text-slate-400"><DocumentTextOutline /></n-icon>
          </div>
          <h2 class="text-lg font-semibold text-slate-800 dark:text-slate-100 mb-2">
            {{ t('reviewTemplates.empty') }}
          </h2>
          <p class="text-slate-500 dark:text-slate-400 mb-6 max-w-md text-center text-sm leading-relaxed">
            {{ t('reviewTemplates.emptyHint') }}
          </p>
          <div class="flex flex-wrap justify-center gap-2">
            <n-button type="primary" data-testid="template-create-empty" @click="openCreate">
              {{ t('reviewTemplates.create') }}
            </n-button>
            <n-button secondary :loading="bootstrapping" @click="bootstrap">
              {{ t('reviewTemplates.bootstrap') }}
            </n-button>
          </div>
        </div>

        <template v-else-if="rows.length > 0">
          <div class="space-y-3">
            <div
              v-for="row in rows"
              :key="row.id"
              class="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900"
              :data-testid="`template-row-${row.id}`"
            >
              <div class="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                <div class="min-w-0">
                  <a
                    :href="`/reviews/templates/${row.id}`"
                    class="text-sm font-semibold text-slate-900 hover:text-blue-600 dark:text-slate-100 dark:hover:text-blue-400"
                    @click.prevent="openDetail(row)"
                  >
                    {{ row.name }}
                  </a>
                  <p v-if="row.description" class="mt-1 line-clamp-2 text-xs text-slate-500 dark:text-slate-400">
                    {{ row.description }}
                  </p>
                </div>
                <div class="flex flex-wrap items-center gap-1">
                  <n-tag
                    :type="row.status === 'archived' ? 'default' : 'success'"
                    size="small"
                    data-testid="template-status-tag"
                  >
                    {{ row.status === 'archived' ? t('reviewTemplates.statusArchived') : t('reviewTemplates.statusActive') }}
                  </n-tag>
                  <n-tag size="small" :bordered="false">
                    {{ t('reviewTemplates.versionTag', { n: row.latest_version }) }}
                  </n-tag>
                </div>
              </div>
              <div class="mt-3 flex flex-wrap items-center gap-2">
                <n-button size="small" secondary type="primary" @click="openDetail(row)">
                  {{ t('reviewTemplates.open') }}
                </n-button>
                <span class="text-xs tabular-nums text-slate-500 dark:text-slate-400">
                  {{ t('reviewTemplates.updatedAt') }}: {{ formatDateTime(row.updated_at || row.created_at) }}
                </span>
              </div>
            </div>
          </div>
          <div class="mt-4 flex max-w-full justify-end overflow-x-auto">
            <n-pagination
              v-model:page="page"
              :page-size="pageSize"
              :item-count="total"
              :page-slot="7"
              @update:page="load"
            />
          </div>
        </template>
      </n-spin>
    </div>

    <!-- 新建模板：名称 + 说明 + 首版自定义项（内置 4 项由服务端固定补齐） -->
    <n-modal
      :show="createVisible"
      preset="card"
      :title="t('reviewTemplates.create')"
      class="max-w-2xl"
      :mask-closable="!creating"
      :closable="!creating"
      data-testid="template-create-modal"
      @update:show="(value: boolean) => (!value ? (createVisible = false) : undefined)"
    >
      <div class="flex flex-col gap-3">
        <div>
          <div class="mb-1 text-xs font-medium text-slate-500 dark:text-slate-400">
            {{ t('reviewTemplates.name') }}
          </div>
          <n-input
            v-model:value="form.name"
            :placeholder="t('reviewTemplates.namePlaceholder')"
            :input-props="{ 'aria-label': t('reviewTemplates.name') }"
            maxlength="100"
            data-testid="template-name-input"
          />
        </div>
        <div>
          <div class="mb-1 text-xs font-medium text-slate-500 dark:text-slate-400">
            {{ t('reviewTemplates.description') }}
          </div>
          <n-input
            v-model:value="form.description"
            type="textarea"
            :placeholder="t('reviewTemplates.descriptionPlaceholder')"
            maxlength="500"
            :autosize="{ minRows: 2, maxRows: 4 }"
            data-testid="template-description-input"
          />
        </div>
        <div class="rounded-lg border border-slate-200 p-3 dark:border-slate-700">
          <div class="text-xs font-medium text-slate-600 dark:text-slate-300">
            {{ t('reviewTemplates.builtinTitle') }}
          </div>
          <p class="mt-1 text-[11px] text-slate-500 dark:text-slate-400">
            {{ t('reviewTemplates.builtinHint') }}
          </p>
          <div class="mt-2 flex flex-wrap gap-1">
            <n-tag v-for="item in BUILTIN_ITEMS" :key="item.key" size="small" :bordered="false">
              {{ t(item.labelKey) }}
            </n-tag>
          </div>
        </div>
        <div>
          <div class="mb-1 flex items-center justify-between text-xs font-medium text-slate-500 dark:text-slate-400">
            <span>{{ t('reviewTemplates.customItems') }}</span>
            <n-button text size="small" type="primary" data-testid="template-add-item" @click="addItem">
              {{ t('reviewTemplates.addItem') }}
            </n-button>
          </div>
          <p v-if="form.items.length === 0" class="text-[11px] text-slate-500 dark:text-slate-400">
            {{ t('reviewTemplates.customItemsEmpty') }}
          </p>
          <div
            v-for="(item, index) in form.items"
            :key="index"
            class="mb-2 rounded-lg border border-slate-200 p-2 dark:border-slate-700"
            :data-testid="`template-item-${index}`"
          >
            <div class="flex flex-col gap-2 sm:flex-row">
              <n-input
                v-model:value="item.key"
                :placeholder="t('reviewTemplates.itemKeyPlaceholder')"
                maxlength="32"
                class="sm:w-44"
                :data-testid="`template-item-key-${index}`"
              />
              <n-input
                v-model:value="item.label"
                :placeholder="t('reviewTemplates.itemLabelPlaceholder')"
                maxlength="100"
                class="sm:w-48"
                :data-testid="`template-item-label-${index}`"
              />
              <n-input
                v-model:value="item.keywordsText"
                :placeholder="t('reviewTemplates.itemKeywordsPlaceholder')"
                :input-props="{ 'aria-label': t('reviewTemplates.itemKeywords') }"
                :data-testid="`template-item-keywords-${index}`"
              />
              <n-button text type="error" :aria-label="t('reviewTemplates.removeItem')" @click="removeItem(index)">
                {{ t('reviewTemplates.removeItem') }}
              </n-button>
            </div>
          </div>
        </div>
        <div>
          <div class="mb-1 text-xs font-medium text-slate-500 dark:text-slate-400">
            {{ t('reviewTemplates.changeNote') }}
          </div>
          <n-input
            v-model:value="form.changeNote"
            :placeholder="t('reviewTemplates.changeNotePlaceholder')"
            maxlength="200"
          />
        </div>
        <n-alert v-if="createError" type="error" :bordered="false" data-testid="template-create-error">
          {{ createError }}
        </n-alert>
      </div>
      <template #footer>
        <div class="flex justify-end gap-2">
          <n-button quaternary :disabled="creating" @click="createVisible = false">
            {{ t('reviews.createCancel') }}
          </n-button>
          <n-button
            type="primary"
            :loading="creating"
            :disabled="creating"
            data-testid="template-create-submit"
            @click="submitCreate"
          >
            {{ t('reviewTemplates.createSubmit') }}
          </n-button>
        </div>
      </template>
    </n-modal>
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
  NModal,
  NPagination,
  NSelect,
  NSpin,
  NTag,
  useMessage
} from 'naive-ui'
import { DocumentTextOutline } from '@vicons/ionicons5'
import PageBreadcrumb from '@/components/common/PageBreadcrumb.vue'
import { reviewsApi, type ReviewTemplateSummary } from '@/api/reviews'
import { apiUnwrap } from '@/utils/apiUnwrap'
import { getErrorMessage } from '@/utils/apiError'
import { parseApiDate } from '@/utils/format'

const { t, locale } = useI18n()
const router = useRouter()
const message = useMessage()

/**
 * 内置 4 项：关键词与抽取器由服务端固定，界面只展示、不可编辑。
 * 与服务端 review_checklist.DEFAULT_ITEM_KEYWORDS 一一对应；模板只能追加自定义项。
 */
const BUILTIN_ITEMS = [
  { key: 'payment_term', labelKey: 'reviews.itemPaymentTerm' },
  { key: 'start_condition', labelKey: 'reviews.itemStartCondition' },
  { key: 'acceptance_invoice', labelKey: 'reviews.itemAcceptanceInvoice' },
  { key: 'exception', labelKey: 'reviews.itemException' }
] as const

const TEMPLATE_KEY_PATTERN = /^[a-z][a-z0-9_]{1,31}$/

const rows = ref<ReviewTemplateSummary[]>([])
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
  items: [] as Array<{ key: string; label: string; keywordsText: string }>
})

const statusOptions = computed(() => [
  { value: 'active', label: t('reviewTemplates.statusActive') },
  { value: 'archived', label: t('reviewTemplates.statusArchived') },
  { value: 'all', label: t('reviewTemplates.statusAll') }
])

const dateFormatter = computed(
  () => new Intl.DateTimeFormat(locale.value, { dateStyle: 'medium', timeStyle: 'short' })
)
function formatDateTime(value?: string | null) {
  const date = parseApiDate(value)
  return date ? dateFormatter.value.format(date) : '—'
}

async function load() {
  loading.value = true
  loadError.value = false
  try {
    const data = apiUnwrap<{ items: ReviewTemplateSummary[]; total?: number }>(
      await reviewsApi.templates.list({
        status: statusFilter.value,
        page: page.value,
        page_size: pageSize
      })
    )
    rows.value = data?.items ?? []
    total.value = data?.total ?? rows.value.length
  } catch {
    loadError.value = true
  } finally {
    loading.value = false
  }
}

function applyFilter() {
  page.value = 1
  void load()
}

function openDetail(row: ReviewTemplateSummary) {
  router.push(`/reviews/templates/${row.id}`)
}

function openCreate() {
  form.name = ''
  form.description = ''
  form.changeNote = ''
  form.items = []
  createError.value = ''
  createVisible.value = true
}

function addItem() {
  form.items.push({ key: '', label: '', keywordsText: '' })
}

function removeItem(index: number) {
  form.items.splice(index, 1)
}

/** 拆关键词：支持逗号/顿号/换行；去空去重（重复词服务端也会拒）。 */
function splitKeywords(text: string): string[] {
  const parts = text
    .split(/[,，、\n]/)
    .map((part) => part.trim())
    .filter(Boolean)
  return Array.from(new Set(parts))
}

function customItems() {
  return form.items.map((item) => ({
    key: item.key.trim(),
    keywords: splitKeywords(item.keywordsText),
    label: item.label.trim()
  }))
}

async function submitCreate() {
  if (creating.value) return
  if (!form.name.trim()) {
    createError.value = t('reviewTemplates.nameRequired')
    return
  }
  const items = customItems()
  for (const item of items) {
    if (!TEMPLATE_KEY_PATTERN.test(item.key)) {
      createError.value = t('reviewTemplates.itemKeyInvalid')
      return
    }
    if (item.keywords.length === 0) {
      createError.value = t('reviewTemplates.itemKeywordsRequired')
      return
    }
  }
  if (new Set(items.map((item) => item.key)).size !== items.length) {
    createError.value = t('reviewTemplates.itemKeyDuplicate')
    return
  }
  createError.value = ''
  creating.value = true
  try {
    const payload: {
      name: string
      description?: string | null
      items?: Array<{ key: string; keywords: string[]; label?: string | null }>
      change_note?: string | null
    } = {
      name: form.name.trim(),
      description: form.description.trim() || null
    }
    if (items.length > 0) {
      payload.items = items
      if (form.changeNote.trim()) payload.change_note = form.changeNote.trim()
    }
    const created = apiUnwrap<{ id: string }>(await reviewsApi.templates.create(payload))
    message.success(t('reviewTemplates.createDone'))
    createVisible.value = false
    if (created?.id) {
      router.push(`/reviews/templates/${created.id}`)
    } else {
      await load()
    }
  } catch (error) {
    createError.value = getErrorMessage(error, t('reviewTemplates.createFailed'))
  } finally {
    creating.value = false
  }
}

async function bootstrap() {
  if (bootstrapping.value) return
  bootstrapping.value = true
  try {
    const created = apiUnwrap<{ id: string }>(
      await reviewsApi.templates.bootstrap({ name: t('reviewTemplates.bootstrapName') })
    )
    message.success(t('reviewTemplates.bootstrapDone'))
    if (created?.id) {
      router.push(`/reviews/templates/${created.id}`)
    } else {
      await load()
    }
  } catch (error) {
    message.error(getErrorMessage(error, t('reviewTemplates.bootstrapFailed')))
  } finally {
    bootstrapping.value = false
  }
}

onMounted(load)

defineExpose({ form, rows, submitCreate, addItem, removeItem, load, bootstrap, splitKeywords })
</script>
