<template>
  <div class="scheduled-tasks-container h-full p-4 lg:p-6 overflow-y-auto">
    <div class="bg-white/70 dark:bg-gray-900/70 backdrop-blur-xl rounded-3xl border border-white/20 dark:border-gray-800/30 shadow-2xl shadow-blue-500/10 h-full flex flex-col overflow-hidden">
      <!-- 页面头部 -->
      <div class="p-6 border-b border-gray-200/50 dark:border-gray-800/50 flex flex-col md:flex-row justify-between items-start md:items-center gap-4 bg-gradient-to-r from-slate-50/30 to-slate-50/30 dark:from-slate-950/10 dark:to-slate-950/10">
        <div>
          <h1 class="text-2xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-blue-600 to-blue-600 dark:from-blue-400 dark:to-blue-400">
            {{ t('scheduler.title') }}
          </h1>
          <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">
            {{ t('scheduler.subtitle') }}
          </p>
        </div>

        <div class="flex flex-wrap items-center gap-3">
          <n-select
            v-model:value="enabledFilter"
            :options="filterOptions"
            class="w-36"
            round
            @update:value="reloadFromFirstPage"
          />
          <n-button
            type="primary"
            round
            class="shadow-lg shadow-blue-500/30 hover:shadow-blue-500/50 transition-all duration-300"
            @click="openCreateModal"
          >
            <template #icon>
              <n-icon><add-outline /></n-icon>
            </template>
            {{ t('scheduler.create') }}
          </n-button>
          <n-button
            secondary
            circle
            class="hover:rotate-180 transition-all duration-500"
            @click="loadTasks"
          >
            <template #icon>
              <n-icon><refresh-outline /></n-icon>
            </template>
          </n-button>
        </div>
      </div>

      <!-- 表格内容区域 -->
      <div class="flex-1 p-6 overflow-hidden">
        <n-spin :show="loading" class="h-full">
          <template #description>
            <span>{{ t('common.loading') }}</span>
          </template>

          <div v-if="loadError" class="flex items-center justify-center h-full">
            <n-result status="error" :title="t('scheduler.loadFailed')" :description="loadErrorMsg">
              <template #footer>
                <n-button type="primary" round @click="loadTasks">{{ t('common.refresh') }}</n-button>
              </template>
            </n-result>
          </div>

          <div v-else-if="!loading && tasks.length === 0" class="flex items-center justify-center h-full">
            <n-empty :description="t('scheduler.noData')">
              <template #extra>
                <n-button type="primary" round @click="openCreateModal">{{ t('scheduler.create') }}</n-button>
              </template>
            </n-empty>
          </div>

          <n-data-table
            v-else
            :columns="columns"
            :data="tasks"
            :pagination="pagination"
            :row-key="(row: ScheduledTask) => row.id"
            :bordered="false"
            class="modern-table"
          />
        </n-spin>
      </div>
    </div>

    <!-- 新建定时任务弹窗 -->
    <n-modal
      v-model:show="createModal.show"
      :title="t('scheduler.modal.title')"
      preset="card"
      class="max-w-2xl rounded-3xl shadow-2xl"
      @after-leave="resetCreateForm"
    >
      <n-spin :show="saving">
        <n-form ref="formRef" :model="form" :rules="formRules" label-placement="top" class="mt-4">
          <n-form-item :label="t('scheduler.modal.name')" path="name">
            <n-input v-model:value="form.name" :placeholder="t('scheduler.modal.namePlaceholder')" round />
          </n-form-item>

          <n-form-item :label="t('scheduler.modal.scheduleType')" path="schedule_type">
            <n-radio-group v-model:value="form.schedule_type" name="schedule-type-group">
              <n-space>
                <n-radio-button value="cron">{{ t('scheduler.modal.type.cron') }}</n-radio-button>
                <n-radio-button value="interval">{{ t('scheduler.modal.type.interval') }}</n-radio-button>
                <n-radio-button value="once">{{ t('scheduler.modal.type.once') }}</n-radio-button>
              </n-space>
            </n-radio-group>
          </n-form-item>

          <template v-if="form.schedule_type === 'cron'">
            <n-form-item :label="t('scheduler.modal.cronExpr')" path="cron_expr">
              <n-input v-model:value="form.cron_expr" :placeholder="t('scheduler.modal.cronPlaceholder')" round />
            </n-form-item>
            <div class="-mt-2 mb-4 text-xs text-gray-500 dark:text-gray-400">
              {{ t('scheduler.modal.cronPreview') }}:
              <span class="font-medium text-blue-600 dark:text-blue-400">{{ cronPreview }}</span>
            </div>
            <n-form-item :label="t('scheduler.modal.timezone')" path="timezone">
              <n-select v-model:value="form.timezone" :options="timezoneOptions" round />
            </n-form-item>
          </template>

          <template v-else-if="form.schedule_type === 'interval'">
            <n-form-item :label="t('scheduler.modal.intervalSeconds')" path="interval_seconds">
              <n-input-number
                v-model:value="form.interval_seconds"
                :min="MIN_INTERVAL_SECONDS"
                :step="60"
                class="w-full"
                :placeholder="t('scheduler.modal.intervalHint', { n: MIN_INTERVAL_SECONDS })"
              />
            </n-form-item>
            <div class="-mt-2 mb-4 text-xs text-gray-500 dark:text-gray-400">
              {{ t('scheduler.modal.intervalHint', { n: MIN_INTERVAL_SECONDS }) }}
            </div>
          </template>

          <template v-else>
            <n-form-item :label="t('scheduler.modal.onceAt')" path="once_at">
              <n-date-picker
                v-model:value="form.once_at"
                type="datetime"
                clearable
                class="w-full"
                :placeholder="t('scheduler.modal.onceAtHint')"
              />
            </n-form-item>
          </template>

          <n-form-item :label="t('scheduler.modal.prompt')" path="prompt">
            <n-input
              v-model:value="form.prompt"
              type="textarea"
              :autosize="{ minRows: 4, maxRows: 10 }"
              :placeholder="t('scheduler.modal.promptPlaceholder')"
            />
          </n-form-item>

          <n-form-item :label="t('scheduler.modal.enabled')" path="enabled">
            <n-switch v-model:value="form.enabled" />
          </n-form-item>
        </n-form>
      </n-spin>
      <template #footer>
        <div class="flex justify-end gap-3">
          <n-button round @click="createModal.show = false">{{ t('common.cancel') }}</n-button>
          <n-button
            type="primary"
            round
            :loading="saving"
            class="px-8 shadow-lg shadow-blue-500/20"
            @click="submitCreate"
          >
            {{ t('common.save') }}
          </n-button>
        </div>
      </template>
    </n-modal>

    <!-- 运行结果（Agent 任务）弹窗 -->
    <n-modal
      v-model:show="resultModal.show"
      :title="t('scheduler.result.title')"
      preset="card"
      class="max-w-xl rounded-3xl shadow-2xl"
    >
      <n-spin :show="resultModal.loading">
        <div v-if="resultModal.error" class="py-4">
          <n-result status="warning" :title="t('scheduler.result.unavailable')" :description="resultModal.error" size="small" />
        </div>
        <n-descriptions v-else-if="resultModal.view" label-placement="left" bordered :column="1" class="mt-2">
          <n-descriptions-item :label="t('scheduler.result.taskId')">
            <div class="flex items-center gap-2">
              <span class="font-mono text-xs">{{ resultModal.taskId }}</span>
              <n-button text type="primary" size="tiny" @click="copyTaskId">{{ t('common.copy') }}</n-button>
            </div>
          </n-descriptions-item>
          <n-descriptions-item :label="t('scheduler.result.state')">
            <n-tag :type="taskStateTagType(resultModal.view.state)" size="small" round :bordered="false">
              {{ taskStateText(resultModal.view.state) }}
            </n-tag>
          </n-descriptions-item>
          <n-descriptions-item :label="t('scheduler.result.progress')">
            {{ resultModal.view.completed_steps }} / {{ resultModal.view.total_steps ?? '-' }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('scheduler.result.query')">
            <span class="text-sm break-all">{{ resultModal.view.query }}</span>
          </n-descriptions-item>
          <n-descriptions-item v-if="resultModal.view.last_error" :label="t('scheduler.result.error')">
            <span class="text-sm break-all text-red-500">{{ resultModal.view.last_error }}</span>
          </n-descriptions-item>
        </n-descriptions>
      </n-spin>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, h, onMounted, reactive, ref, type VNode } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  NButton,
  NIcon,
  NSpace,
  NTag,
  NTooltip,
  useDialog,
  type DataTableColumns,
  type FormInst,
  type FormRules,
} from 'naive-ui'
import {
  AddOutline,
  FlashOutline,
  PauseOutline,
  PlayOutline,
  RefreshOutline,
  TrashOutline,
} from '@vicons/ionicons5'
import { agentApi } from '@/api/agent'
import {
  createScheduledTask,
  deleteScheduledTask,
  listScheduledTasks,
  MAX_CRON_LENGTH,
  MAX_NAME_LENGTH,
  MAX_PROMPT_LENGTH,
  MIN_INTERVAL_SECONDS,
  pauseScheduledTask,
  resumeScheduledTask,
  runNowScheduledTask,
  type ScheduledTask,
  type ScheduleType,
} from '@/api/scheduledTask'
import type { AgentTaskState, AgentTaskView } from '@/types/agent'
import { extractApiError } from '@/utils/apiError'
import { cronToHuman } from '@/utils/cronHuman'
import { copyToClipboard, formatDate } from '@/utils/format'
import { useDedupedMessage } from '@/utils/message'

const { t, te } = useI18n()
const message = useDedupedMessage()
const dialog = useDialog()

// ── 列表 ──────────────────────────────────────────────────────────────

const loading = ref(false)
const loadError = ref(false)
const loadErrorMsg = ref('')
const tasks = ref<ScheduledTask[]>([])
const enabledFilter = ref<'all' | 'enabled' | 'paused'>('all')

const filterOptions = computed(() => [
  { label: t('scheduler.filterAll'), value: 'all' },
  { label: t('scheduler.filterEnabled'), value: 'enabled' },
  { label: t('scheduler.filterPaused'), value: 'paused' },
])

const pagination = reactive({
  page: 1,
  pageSize: 10,
  itemCount: 0,
  showSizePicker: true,
  pageSizes: [10, 20, 50],
  onChange: (page: number) => {
    pagination.page = page
    loadTasks()
  },
  onUpdatePageSize: (pageSize: number) => {
    pagination.pageSize = pageSize
    pagination.page = 1
    loadTasks()
  },
})

async function loadTasks() {
  loading.value = true
  loadError.value = false
  try {
    const params: { skip: number; limit: number; enabled?: boolean } = {
      skip: (pagination.page - 1) * pagination.pageSize,
      limit: pagination.pageSize,
    }
    if (enabledFilter.value !== 'all') params.enabled = enabledFilter.value === 'enabled'
    const res = await listScheduledTasks(params)
    tasks.value = res.data.items
    pagination.itemCount = res.data.total
  } catch (error: unknown) {
    loadError.value = true
    loadErrorMsg.value = extractApiError(error, t('common.failed'))
  } finally {
    loading.value = false
  }
}

function reloadFromFirstPage() {
  pagination.page = 1
  loadTasks()
}

// ── 计划/状态展示 ─────────────────────────────────────────────────────

/** 间隔秒数 → 人话（分钟/小时整倍数收敛，否则秒）。 */
function intervalText(seconds: number): string {
  if (seconds % 3600 === 0) return t('scheduler.plan.intervalHours', { n: seconds / 3600 })
  if (seconds % 60 === 0) return t('scheduler.plan.intervalMinutes', { n: seconds / 60 })
  return t('scheduler.plan.intervalSeconds', { n: seconds })
}

/** 计划主文案：cron → 人话（子集外回落原始表达式）；interval/once → 人话。 */
function planText(task: ScheduledTask): string {
  if (task.schedule_type === 'cron' && task.cron_expr) {
    return cronToHuman(task.cron_expr, t) ?? task.cron_expr
  }
  if (task.schedule_type === 'interval' && task.interval_seconds != null) {
    return intervalText(task.interval_seconds)
  }
  if (task.schedule_type === 'once') {
    return t('scheduler.plan.onceAt', { time: formatDate(task.once_at) })
  }
  return '-'
}

/** 计划副文案：原始表达式/秒数 + 时区（仅 cron 的时区影响解释）。 */
function planSubText(task: ScheduledTask): string {
  if (task.schedule_type === 'cron' && task.cron_expr) {
    return `${task.cron_expr} · ${task.timezone}`
  }
  if (task.schedule_type === 'interval' && task.interval_seconds != null) {
    return `${task.interval_seconds}s`
  }
  if (task.schedule_type === 'once') {
    return formatDate(task.once_at)
  }
  return ''
}

/** 已完成的单次任务：enabled=false 且无下次运行且跑过。 */
function isCompletedOnce(task: ScheduledTask): boolean {
  return task.schedule_type === 'once' && task.last_run_at != null && task.next_run_at == null
}

const runStatusTagType = (status: string): 'success' | 'error' | 'info' =>
  status === 'success' ? 'success' : status === 'failed' ? 'error' : 'info'

function statusCell(task: ScheduledTask): VNode {
  const tags: VNode[] = []
  if (isCompletedOnce(task)) {
    tags.push(
      h(NTag, { type: 'info', size: 'small', round: true, bordered: false }, {
        default: () => t('scheduler.statusFinished'),
      }),
    )
  } else {
    tags.push(
      h(NTag, { type: task.enabled ? 'success' : 'default', size: 'small', round: true, bordered: false }, {
        default: () => (task.enabled ? t('scheduler.statusEnabled') : t('scheduler.statusPaused')),
      }),
    )
  }
  if (task.last_status) {
    const label = te(`scheduler.lastStatus.${task.last_status}`)
      ? t(`scheduler.lastStatus.${task.last_status}`)
      : task.last_status
    const tag = h(
      NTag,
      { type: runStatusTagType(task.last_status), size: 'small', round: true, bordered: false },
      { default: () => label },
    )
    tags.push(task.last_status === 'failed' && task.last_error ? h(NTooltip, null, {
      trigger: () => tag,
      default: () => h('div', { class: 'max-w-xs break-all' }, task.last_error as string),
    }) : tag)
  }
  if (task.skipped_overlap_count > 0) {
    tags.push(
      h(NTag, { type: 'warning', size: 'small', round: true, bordered: false }, {
        default: () => t('scheduler.skippedOverlap', { n: task.skipped_overlap_count }),
      }),
    )
  }
  return h('div', { class: 'flex flex-wrap gap-1' }, tags)
}

// ── 行操作 ────────────────────────────────────────────────────────────

const actionPending = ref<string | null>(null)

function reasonText(reason: string): string {
  const key = `scheduler.runNowReason.${reason}`
  return te(key) ? t(key) : t('scheduler.runNowReason.unknown', { reason })
}

async function handleRunNow(task: ScheduledTask) {
  actionPending.value = task.id
  try {
    const res = await runNowScheduledTask(task.id)
    if (res.data.triggered) {
      message.success(t('scheduler.runNowTriggered'))
    } else {
      // 后端语义如实呈现：overlap = 上一轮未完，跳过不排队
      message.warning(reasonText(res.data.reason))
    }
    loadTasks()
  } catch (error: unknown) {
    message.error(extractApiError(error, t('common.failed')))
  } finally {
    actionPending.value = null
  }
}

async function handleToggle(task: ScheduledTask) {
  actionPending.value = task.id
  try {
    if (task.enabled) {
      await pauseScheduledTask(task.id)
      message.success(t('common.success'))
    } else {
      // 409（单次已跑完）/ 400 等由后端原文呈现
      await resumeScheduledTask(task.id)
      message.success(t('common.success'))
    }
    loadTasks()
  } catch (error: unknown) {
    message.error(extractApiError(error, t('common.failed')))
  } finally {
    actionPending.value = null
  }
}

function handleDelete(task: ScheduledTask) {
  dialog.warning({
    title: t('scheduler.deleteTitle'),
    content: t('scheduler.deleteConfirm', { name: task.name }),
    positiveText: t('common.confirm'),
    negativeText: t('common.cancel'),
    onPositiveClick: async () => {
      try {
        await deleteScheduledTask(task.id)
        message.success(t('common.success'))
        loadTasks()
      } catch (error: unknown) {
        message.error(extractApiError(error, t('common.failed')))
      }
    },
  })
}

// ── 运行结果（Agent 任务）弹窗 ────────────────────────────────────────

const resultModal = reactive({
  show: false,
  loading: false,
  error: '',
  taskId: '',
  view: null as AgentTaskView | null,
})

async function openResult(taskId: string) {
  resultModal.show = true
  resultModal.loading = true
  resultModal.error = ''
  resultModal.taskId = taskId
  resultModal.view = null
  try {
    const res = await agentApi.getTask(taskId)
    resultModal.view = res.data.data
  } catch (error: unknown) {
    // agent 任务端点有独立的 org/归属闸门：读不到就如实说"不可用"
    resultModal.error = extractApiError(error, t('scheduler.result.unavailable'))
  } finally {
    resultModal.loading = false
  }
}

function taskStateText(state: AgentTaskState): string {
  const key = `scheduler.result.taskState.${state}`
  return te(key) ? t(key) : state
}

function taskStateTagType(state: AgentTaskState): 'success' | 'error' | 'info' | 'warning' | 'default' {
  if (state === 'completed') return 'success'
  if (state === 'failed' || state === 'cancelled' || state === 'rejected' || state === 'abandoned') return 'error'
  if (state === 'pending' || state === 'waiting_tool' || state === 'waiting_human') return 'default'
  if (state === 'running') return 'info'
  return 'warning'
}

async function copyTaskId() {
  const ok = await copyToClipboard(resultModal.taskId)
  message[ok ? 'success' : 'error'](t(ok ? 'common.copySuccess' : 'common.copyFailed'))
}

// ── 列定义 ────────────────────────────────────────────────────────────

const columns = computed<DataTableColumns<ScheduledTask>>(() => [
  {
    title: t('scheduler.columns.name'),
    key: 'name',
    minWidth: 180,
    ellipsis: { tooltip: true },
    render: (row) =>
      h('div', [
        h('div', { class: 'font-medium' }, row.name),
        h('div', { class: 'text-xs text-gray-400 truncate' }, row.prompt),
      ]),
  },
  {
    title: t('scheduler.columns.schedule'),
    key: 'schedule',
    minWidth: 220,
    render: (row) =>
      h('div', [
        h('div', { class: 'text-sm' }, planText(row)),
        h('div', { class: 'text-xs text-gray-400' }, planSubText(row)),
      ]),
  },
  {
    title: t('scheduler.columns.status'),
    key: 'status',
    minWidth: 150,
    render: (row) => statusCell(row),
  },
  {
    title: t('scheduler.columns.lastRun'),
    key: 'last_run_at',
    minWidth: 170,
    render: (row) => {
      const children: VNode[] = [
        h(
          'div',
          { class: 'text-sm' },
          row.last_run_at ? formatDate(row.last_run_at) : t('scheduler.neverRun'),
        ),
      ]
      if (row.last_task_id) {
        children.push(
          h(
            NButton,
            {
              text: true,
              type: 'primary',
              size: 'tiny',
              class: 'view-result-btn',
              onClick: () => openResult(row.last_task_id as string),
            },
            { default: () => t('scheduler.viewResult') },
          ),
        )
      }
      return h('div', children)
    },
  },
  {
    title: t('scheduler.columns.nextRun'),
    key: 'next_run_at',
    minWidth: 150,
    render: (row) =>
      h('div', { class: 'text-sm' }, row.next_run_at ? formatDate(row.next_run_at) : t('scheduler.noNextRun')),
  },
  {
    title: t('scheduler.columns.actions'),
    key: 'actions',
    width: 240,
    render: (row) =>
      h(NSpace, { size: 4 }, {
        default: () => [
          h(
            NButton,
            {
              size: 'small',
              quaternary: true,
              type: 'primary',
              class: 'row-run-btn',
              loading: actionPending.value === row.id,
              onClick: () => handleRunNow(row),
            },
            { icon: () => h(NIcon, null, { default: () => h(FlashOutline) }), default: () => t('scheduler.runNow') },
          ),
          h(
            NButton,
            {
              size: 'small',
              quaternary: true,
              class: 'row-toggle-btn',
              loading: actionPending.value === row.id,
              onClick: () => handleToggle(row),
            },
            {
              icon: () =>
                h(NIcon, null, { default: () => h(row.enabled ? PauseOutline : PlayOutline) }),
              default: () => (row.enabled ? t('scheduler.pause') : t('scheduler.resume')),
            },
          ),
          h(
            NButton,
            {
              size: 'small',
              quaternary: true,
              type: 'error',
              class: 'row-delete-btn',
              onClick: () => handleDelete(row),
            },
            { icon: () => h(NIcon, null, { default: () => h(TrashOutline) }), default: () => t('scheduler.delete') },
          ),
        ],
      }),
  },
])

// ── 创建表单 ──────────────────────────────────────────────────────────

const createModal = reactive({ show: false })
const formRef = ref<FormInst | null>(null)
const saving = ref(false)

function defaultForm() {
  return {
    name: '',
    prompt: '',
    schedule_type: 'cron' as ScheduleType,
    cron_expr: '',
    interval_seconds: 3600 as number | null,
    once_at: null as number | null,
    timezone: '',
    enabled: true,
  }
}
const form = reactive(defaultForm())

const timezoneOptions = computed(() => [
  { label: t('scheduler.modal.timezoneDefault'), value: '' },
  { label: 'UTC', value: 'UTC' },
  { label: 'Asia/Shanghai', value: 'Asia/Shanghai' },
  { label: 'Asia/Tokyo', value: 'Asia/Tokyo' },
  { label: 'Europe/Paris', value: 'Europe/Paris' },
  { label: 'America/New_York', value: 'America/New_York' },
])

const cronPreview = computed(() => {
  const expr = form.cron_expr.trim()
  if (!expr) return '-'
  return cronToHuman(expr, t) ?? t('scheduler.modal.cronPreviewUnknown')
})

const formRules = computed<FormRules>(() => ({
  name: [
    { required: true, message: t('validation.required'), trigger: ['input', 'blur'] },
    {
      validator: (_rule, value) =>
        String(value ?? '').trim().length > MAX_NAME_LENGTH
          ? new Error(t('scheduler.validation.nameMax', { n: MAX_NAME_LENGTH }))
          : true,
      trigger: ['input', 'blur'],
    },
  ],
  prompt: [
    { required: true, message: t('validation.required'), trigger: ['input', 'blur'] },
    {
      validator: (_rule, value) =>
        String(value ?? '').length > MAX_PROMPT_LENGTH
          ? new Error(t('scheduler.validation.promptMax', { n: MAX_PROMPT_LENGTH }))
          : true,
      trigger: ['input', 'blur'],
    },
  ],
  cron_expr: [
    {
      validator: (_rule, value) => {
        if (form.schedule_type !== 'cron') return true
        const v = String(value ?? '').trim()
        if (!v) return new Error(t('scheduler.validation.cronRequired'))
        if (v.length > MAX_CRON_LENGTH) return new Error(t('scheduler.validation.cronMax', { n: MAX_CRON_LENGTH }))
        return true
      },
      trigger: ['input', 'blur', 'change'],
    },
  ],
  interval_seconds: [
    {
      validator: (_rule, value) => {
        if (form.schedule_type !== 'interval') return true
        if (value == null) return new Error(t('scheduler.validation.intervalRequired'))
        if (Number(value) < MIN_INTERVAL_SECONDS) {
          return new Error(t('scheduler.validation.intervalMin', { n: MIN_INTERVAL_SECONDS }))
        }
        return true
      },
      trigger: ['input', 'blur', 'change'],
    },
  ],
  once_at: [
    {
      validator: (_rule, value) => {
        if (form.schedule_type !== 'once') return true
        if (value == null) return new Error(t('scheduler.validation.onceRequired'))
        if (Number(value) <= Date.now()) return new Error(t('scheduler.validation.onceFuture'))
        return true
      },
      trigger: ['input', 'blur', 'change'],
    },
  ],
}))

function openCreateModal() {
  createModal.show = true
}

function resetCreateForm() {
  Object.assign(form, defaultForm())
  formRef.value?.restoreValidation()
}

async function submitCreate() {
  try {
    await formRef.value?.validate()
  } catch {
    return
  }
  saving.value = true
  try {
    const payload = {
      name: form.name.trim(),
      prompt: form.prompt.trim(),
      schedule_type: form.schedule_type,
      enabled: form.enabled,
    } as Parameters<typeof createScheduledTask>[0]
    if (form.schedule_type === 'cron') {
      payload.cron_expr = form.cron_expr.trim()
      if (form.timezone) payload.timezone = form.timezone
    } else if (form.schedule_type === 'interval') {
      payload.interval_seconds = form.interval_seconds
    } else {
      // datetime 选择器给时间戳；转 ISO（带 Z）——后端按 UTC 存储
      payload.once_at = new Date(form.once_at as number).toISOString()
    }
    await createScheduledTask(payload)
    message.success(t('scheduler.createSuccess'))
    createModal.show = false
    reloadFromFirstPage()
  } catch (error: unknown) {
    // org fail-closed 403 / cron 语义 400 / 422 —— 后端原文如实呈现
    message.error(extractApiError(error, t('common.failed')))
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  loadTasks()
})
</script>
