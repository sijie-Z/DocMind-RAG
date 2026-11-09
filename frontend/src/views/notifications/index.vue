<template>
  <div class="min-h-screen bg-gray-50 dark:bg-gray-950 px-4 sm:px-6 lg:px-8 py-6">
    <div class="max-w-6xl mx-auto space-y-6">
      <!-- T9 定位三件套：面包屑（DocMind / 消息通知） -->
      <PageBreadcrumb />

      <div class="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
        <div>
          <h1 class="text-2xl font-bold text-gray-900 dark:text-white">{{ t('menu.notifications') }}</h1>
          <p class="text-sm text-gray-500 dark:text-gray-400">{{ t('notifications.subtitle') }}</p>
        </div>
        <div class="flex items-center gap-3">
          <n-button type="primary" ghost @click="handleRefresh">{{ t('common.refresh') }}</n-button>
          <n-button type="info" ghost @click="toggleUnreadOnly">{{ filters.unread_only ? t('notifications.showAll') : t('notifications.unreadOnly') }}</n-button>
          <n-button type="warning" ghost :disabled="summary.unread_count === 0" @click="handleMarkAllRead">{{ t('notifications.markAllRead') }}</n-button>
          <n-button type="error" ghost :disabled="selectedIds.length === 0" @click="handleBatchDelete">{{ t('common.batchDelete') }}</n-button>
        </div>
      </div>

      <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
        <n-card class="rounded-2xl" size="small">
          <div class="text-sm text-gray-500">{{ t('notifications.total') }}</div>
          <div class="text-2xl font-bold text-gray-900 dark:text-white">{{ summaryError ? '—' : summary.total }}</div>
        </n-card>
        <n-card class="rounded-2xl" size="small">
          <div class="text-sm text-gray-500">{{ t('notifications.unread') }}</div>
          <div class="text-2xl font-bold text-orange-500">{{ summaryError ? '—' : summary.unread_count }}</div>
        </n-card>
        <n-card class="rounded-2xl" size="small">
          <div class="text-sm text-gray-500">{{ t('notifications.typeDistribution') }}</div>
          <div class="text-sm text-gray-700 dark:text-gray-300 mt-2">
            <span v-if="summaryError">—</span>
            <span v-for="(val, key) in summary.by_type" v-else :key="key" class="inline-block mr-3">
              {{ typeLabel(key) }}: {{ val }}
            </span>
          </div>
        </n-card>
      </div>

      <n-card class="rounded-2xl" content-style="padding: 0;">
        <div class="p-4 border-b border-gray-200 dark:border-gray-800 flex flex-col md:flex-row gap-3">
          <n-input v-model:value="filters.q" :placeholder="t('notifications.searchPlaceholder')" clearable class="md:w-64" />
          <n-select v-model:value="filters.type" :options="typeOptions" :placeholder="t('notifications.typePlaceholder')" clearable class="md:w-40" />
          <n-select v-model:value="filters.unread_only" :options="readOptions" :placeholder="t('notifications.statusPlaceholder')" clearable class="md:w-40" />
          <n-button type="primary" @click="handleSearch">{{ t('notifications.filter') }}</n-button>
        </div>

        <n-spin :show="loading">
          <template #description>
            <span>{{ t('common.loading') }}</span>
          </template>

          <!-- Error state -->
          <div v-if="loadError" class="py-16 text-center">
            <n-result status="error" :title="t('notifications.loadFailed')" :description="loadErrorMsg">
              <template #footer>
                <n-button type="primary" @click="handleRefresh">{{ t('common.retry') }}</n-button>
              </template>
            </n-result>
          </div>

          <!-- Empty state -->
          <div v-else-if="!loadError && items.length === 0 && !loading" class="py-16 text-center">
            <n-empty :description="t('notifications.empty')">
              <template #extra>
                <n-button type="primary" ghost @click="handleRefresh">{{ t('common.refresh') }}</n-button>
              </template>
            </n-empty>
          </div>

          <n-data-table v-else
            :columns="columns"
            :data="items"
            :row-key="rowKey"
            :pagination="pagination"
            :loading="false"
            :row-class-name="rowClassName"
            :checked-row-keys="selectedIds"
            @update:checked-row-keys="handleCheckedRowKeys"
            @update:page="handlePageChange"
          />
        </n-spin>
      </n-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, h, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { NButton, } from 'naive-ui'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { formatDate } from '@/utils/format'
import { notificationTargetLocation } from '@/utils/notificationTarget'
import { useDedupedMessage } from '@/utils/message'
import { getErrorMessage } from '@/utils/apiError'
import PageBreadcrumb from '@/components/common/PageBreadcrumb.vue'
import { batchDeleteNotifications, deleteNotification, getNotificationSummary, getNotifications, markAllAsRead, markAsRead, type Notification, type NotificationSummaryResponse } from '@/api/notification'
import { notificationSocket, type RealtimeNotificationPayload } from '@/utils/notificationSocket'

const router = useRouter()
const { t } = useI18n()
const message = useDedupedMessage()
const latestRealtimeId = ref<number | null>(null)

const summary = reactive<NotificationSummaryResponse>({
  total: 0,
  unread_count: 0,
  by_type: {}
})

const items = ref<Notification[]>([])
const selectedIds = ref<number[]>([])
const loading = ref(false)
const loadError = ref(false)
const loadErrorMsg = ref('')
const summaryError = ref(false)
const recentlyReadIds = ref<number[]>([])

const filters = reactive({
  q: '',
  type: undefined as string | undefined,
  unread_only: undefined as boolean | undefined
})

const pagination = reactive({
  page: 1,
  pageSize: 10,
  itemCount: 0
})

// 选项与列定义随语言切换（computed 依赖 t，locale 变化实时重算）
const typeOptions = computed(() => [
  { label: t('notifications.types.system'), value: 'system' },
  { label: t('notifications.types.security'), value: 'security' },
  { label: t('notifications.types.account'), value: 'account' },
  { label: t('notifications.types.document'), value: 'document' },
  { label: t('notifications.types.chat'), value: 'chat' },
  // Agent 任务终态通知（完成/失败/取消；点击经 target_route="Agent" 跳回放）
  { label: t('notifications.types.agent'), value: 'agent' },
  // 后端实际写入的其余类型（knowledge/organizations/scheduler/files 端点）
  { label: t('notifications.types.knowledge'), value: 'knowledge' },
  { label: t('notifications.types.organization'), value: 'organization' },
  { label: t('notifications.types.scheduler'), value: 'scheduler' },
  { label: t('notifications.types.file'), value: 'file' }
])

const readOptions = computed(() => [
  { label: t('notifications.unread'), value: true },
  { label: t('notifications.all'), value: undefined }
])

// 汇总「类型分布」里的后端类型码 → 本地化名称（未知类型原样显示）
const typeLabel = (key: string) => {
  const map: Record<string, string> = {
    system: t('notifications.types.system'),
    security: t('notifications.types.security'),
    account: t('notifications.types.account'),
    document: t('notifications.types.document'),
    chat: t('notifications.types.chat'),
    agent: t('notifications.types.agent'),
    knowledge: t('notifications.types.knowledge'),
    organization: t('notifications.types.organization'),
    scheduler: t('notifications.types.scheduler'),
    file: t('notifications.types.file'),
  }
  return Object.prototype.hasOwnProperty.call(map, key) ? map[key] : key
}

const rowKey = (row: Notification) => row.id

const rowClassName = (row: Notification) => {
  if (latestRealtimeId.value && row.id === latestRealtimeId.value) return 'notification-realtime-highlight'
  if (recentlyReadIds.value.includes(row.id)) return 'notification-read-highlight'
  return ''
}

const jumpToNotificationTarget = async (row: Notification) => {
  await router.push(notificationTargetLocation(row))
}

const columns = computed(() => [
  {
    type: 'selection',
    multiple: true,
    disabled: (row: Notification) => !row
  },
  {
    title: t('notifications.columns.title'),
    key: 'title',
    render: (row: Notification) => h('button', { class: 'text-left font-medium text-blue-600 hover:underline', onClick: () => jumpToNotificationTarget(row) }, row.title)
  },
  {
    title: t('notifications.columns.content'),
    key: 'content',
    render: (row: Notification) => h('div', { class: 'text-xs text-gray-500 line-clamp-2' }, row.content)
  },
  {
    title: t('notifications.columns.type'),
    key: 'type',
    render: (row: Notification) => typeLabel(row.type)
  },
  {
    title: t('notifications.columns.time'),
    key: 'created_at',
    render: (row: Notification) => formatDate(row.created_at)
  },
  {
    title: t('notifications.columns.status'),
    key: 'is_read',
    render: (row: Notification) => row.is_read ? t('notifications.read') : t('notifications.unread')
  },
  {
    title: t('common.actions'),
    key: 'actions',
    render: (row: Notification) => h('div', { class: 'flex gap-2' }, [
      h(NButton, { size: 'tiny', onClick: async () => handleMarkRead(row) }, { default: () => t('notifications.markRead') }),
      h(NButton, { size: 'tiny', type: 'error', ghost: true, onClick: async () => handleDelete(row) }, { default: () => t('common.delete') })
    ])
  }
])

const fetchSummary = async () => {
  try {
    // E14/D1：页面自呈现错误（统计未知用占位符），请求静默
    const res = await getNotificationSummary({ silent: true })
    if (res.data) {
      summary.total = res.data.total
      summary.unread_count = res.data.unread_count
      summary.by_type = res.data.by_type
      summaryError.value = false
    }
  } catch {
    // 汇总失败不阻塞列表；统计以占位符如实表示未知
    summaryError.value = true
  }
}

const fetchList = async () => {
  loading.value = true
  try {
    const res = await getNotifications({
      skip: (pagination.page - 1) * pagination.pageSize,
      limit: pagination.pageSize,
      unread_only: filters.unread_only,
      type: filters.type,
      q: filters.q
    }, { silent: true })
    if (res.data) {
      items.value = res.data.items
      pagination.itemCount = res.data.total
      loadError.value = false
      loadErrorMsg.value = ''
    }
  } catch (error: unknown) {
    // E14/D2：搜索/加载失败清空旧结果，展示错误态与重试
    loadError.value = true
    loadErrorMsg.value = getErrorMessage(error, t('notifications.loadFailed'))
    items.value = []
    pagination.itemCount = 0
    selectedIds.value = []
  } finally {
    loading.value = false
  }
}

const handleCheckedRowKeys = (keys: Array<string | number>) => {
  selectedIds.value = keys.map((k) => Number(k)).filter((k) => !Number.isNaN(k))
}

const handlePageChange = (page: number) => {
  pagination.page = page
  fetchList()
}

const handleSearch = () => {
  pagination.page = 1
  fetchList()
}

const toggleUnreadOnly = () => {
  filters.unread_only = filters.unread_only ? undefined : true
  handleSearch()
}

const handleRefresh = async () => {
  await fetchSummary()
  await fetchList()
}

const handleMarkRead = async (row: Notification) => {
  if (row.is_read) return
  try {
    await markAsRead(row.id)
    items.value = items.value.map((n) => n.id === row.id ? { ...n, is_read: true } : n)
    recentlyReadIds.value = [...new Set([row.id, ...recentlyReadIds.value])].slice(0, 30)
    await fetchSummary()
  } catch (error) {
    // Failed to mark as read
  }
}

const handleMarkAllRead = async () => {
  try {
    await markAllAsRead()
    await handleRefresh()
  } catch (error) {
    // Failed to mark all as read
  }
}

const handleDelete = async (row: Notification) => {
  try {
    await deleteNotification(row.id, { silent: true })
    selectedIds.value = selectedIds.value.filter((id) => id !== row.id)
    message.success(t('notifications.deleteSuccess'))
    await handleRefresh()
  } catch (error: unknown) {
    // E14/D3：失败必须可见且状态如实——不删行、不刷新，单一 toast 呈现后端语义
    message.error(t('notifications.withDetail', { message: t('notifications.deleteFailed'), detail: getErrorMessage(error, t('common.tryAgain')) }))
  }
}

const handleBatchDelete = async () => {
  if (!selectedIds.value.length) return
  const ids = [...selectedIds.value]
  try {
    await batchDeleteNotifications(ids, { silent: true })
    selectedIds.value = []
    message.success(t('notifications.deleteSuccess'))
    await handleRefresh()
  } catch (error: unknown) {
    // 批删失败：保留选中状态（不得假装已删），失败可见且只提示一次
    message.error(t('notifications.withDetail', { message: t('notifications.batchDeleteFailed'), detail: getErrorMessage(error, t('common.tryAgain')) }))
  }
}

const handleRealtime = async (payload: RealtimeNotificationPayload) => {
  latestRealtimeId.value = payload.id

  const normalized: Notification = {
    id: payload.id,
    title: payload.title,
    content: payload.content,
    type: payload.type,
    is_read: payload.is_read,
    target_route: payload.target_route,
    target_id: payload.target_id,
    created_at: payload.created_at
  }

  items.value = [normalized, ...items.value.filter((i) => i.id !== normalized.id)]
  summary.total += 1
  if (!normalized.is_read) summary.unread_count += 1

  window.setTimeout(() => {
    if (latestRealtimeId.value === payload.id) latestRealtimeId.value = null
  }, 3500)
}

onMounted(async () => {
  await handleRefresh()
  // Socket connection is handled by the layout component
  notificationSocket.onNotification(handleRealtime)
})

watch(
  () => filters.unread_only,
  () => {
    pagination.page = 1
  }
)

onUnmounted(() => {
  // 只移除本视图的处理器：裸调 offNotification() 会清空全部处理器——
  // 离开本页后铃铛/layout 的实时推送会静默失效（bug 修复）
  notificationSocket.offNotification(handleRealtime)
})
</script>

<style scoped>
:deep(.notification-realtime-highlight td) {
  animation: fadeHighlight 3s ease;
  background: rgba(59, 130, 246, 0.08);
}

@keyframes fadeHighlight {
  0% {
    background: rgba(59, 130, 246, 0.22);
  }
  100% {
    background: transparent;
  }
}

:deep(.notification-read-highlight td) {
  animation: readFade 1.6s ease;
  background: rgba(16, 185, 129, 0.08);
}

@keyframes readFade {
  0% {
    background: rgba(16, 185, 129, 0.2);
  }
  100% {
    background: transparent;
  }
}
</style>
