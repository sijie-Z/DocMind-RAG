<template>
  <div class="min-h-screen bg-gray-50 dark:bg-gray-950 px-4 sm:px-6 lg:px-8 py-6">
    <div class="max-w-6xl mx-auto space-y-6">
      <div class="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
        <div>
          <h1 class="text-2xl font-bold text-gray-900 dark:text-white">通知中心</h1>
          <p class="text-sm text-gray-500 dark:text-gray-400">集中查看、筛选与处理系统消息</p>
        </div>
        <div class="flex items-center gap-3">
          <n-button type="primary" ghost @click="handleRefresh">刷新</n-button>
          <n-button type="info" ghost @click="toggleUnreadOnly">{{ filters.unread_only ? '显示全部' : '只看未读' }}</n-button>
          <n-button type="warning" ghost :disabled="summary.unread_count === 0" @click="handleMarkAllRead">全部已读</n-button>
          <n-button type="error" ghost :disabled="selectedIds.length === 0" @click="handleBatchDelete">批量删除</n-button>
        </div>
      </div>

      <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
        <n-card class="rounded-2xl" size="small">
          <div class="text-sm text-gray-500">总通知</div>
          <div class="text-2xl font-bold text-gray-900 dark:text-white">{{ summary.total }}</div>
        </n-card>
        <n-card class="rounded-2xl" size="small">
          <div class="text-sm text-gray-500">未读</div>
          <div class="text-2xl font-bold text-orange-500">{{ summary.unread_count }}</div>
        </n-card>
        <n-card class="rounded-2xl" size="small">
          <div class="text-sm text-gray-500">类型分布</div>
          <div class="text-sm text-gray-700 dark:text-gray-300 mt-2">
            <span v-for="(val, key) in summary.by_type" :key="key" class="inline-block mr-3">
              {{ key }}: {{ val }}
            </span>
          </div>
        </n-card>
      </div>

      <n-card class="rounded-2xl" content-style="padding: 0;">
        <div class="p-4 border-b border-gray-200 dark:border-gray-800 flex flex-col md:flex-row gap-3">
          <n-input v-model:value="filters.q" placeholder="搜索标题或内容" clearable class="md:w-64" />
          <n-select v-model:value="filters.type" :options="typeOptions" placeholder="类型" clearable class="md:w-40" />
          <n-select v-model:value="filters.unread_only" :options="readOptions" placeholder="状态" clearable class="md:w-40" />
          <n-button type="primary" @click="handleSearch">筛选</n-button>
        </div>

        <n-data-table
          :columns="columns"
          :data="items"
          :row-key="rowKey"
          :pagination="pagination"
          :loading="loading"
          :row-class-name="rowClassName"
          :checked-row-keys="selectedIds"
          @update:checked-row-keys="handleCheckedRowKeys"
          @update:page="handlePageChange"
        />
      </n-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { h, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useDedupedMessage } from '@/utils/message'
import { NButton, } from 'naive-ui'
import { useRouter } from 'vue-router'
import { formatDate } from '@/utils/format'
import { batchDeleteNotifications, deleteNotification, getNotificationSummary, getNotifications, markAllAsRead, markAsRead, type Notification, type NotificationSummaryResponse } from '@/api/notification'
import { notificationSocket, type RealtimeNotificationPayload } from '@/utils/notificationSocket'
import { useNotificationStore } from '@/stores/notification'

const message = useDedupedMessage()
const router = useRouter()
const notificationStore = useNotificationStore()
const latestRealtimeId = ref<number | null>(null)

const summary = reactive<NotificationSummaryResponse>({
  total: 0,
  unread_count: 0,
  by_type: {}
})

const items = ref<Notification[]>([])
const selectedIds = ref<number[]>([])
const loading = ref(false)
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

const typeOptions = [
  { label: 'system', value: 'system' },
  { label: 'security', value: 'security' },
  { label: 'account', value: 'account' },
  { label: 'document', value: 'document' },
  { label: 'chat', value: 'chat' }
]

const readOptions = [
  { label: '未读', value: true },
  { label: '全部', value: undefined }
]

const rowKey = (row: Notification) => row.id

const rowClassName = (row: Notification) => {
  if (latestRealtimeId.value && row.id === latestRealtimeId.value) return 'notification-realtime-highlight'
  if (recentlyReadIds.value.includes(row.id)) return 'notification-read-highlight'
  return ''
}

const jumpToNotificationTarget = async (row: Notification) => {
  if (row.target_route) {
    await router.push({ name: row.target_route as any, query: row.target_id ? { id: row.target_id } : undefined })
    return
  }
  if (row.type === 'knowledge' || row.type === 'document') {
    await router.push({ name: 'Knowledge' })
  } else if (row.type === 'chat') {
    await router.push({ name: 'Chat' })
  } else if (row.type === 'account' || row.type === 'security') {
    await router.push({ name: 'Profile' })
  }
}

const columns = [
  {
    type: 'selection',
    multiple: true,
    disabled: (row: Notification) => !row
  },
  {
    title: '标题',
    key: 'title',
    render: (row: Notification) => h('button', { class: 'text-left font-medium text-blue-600 hover:underline', onClick: () => jumpToNotificationTarget(row) }, row.title)
  },
  {
    title: '内容',
    key: 'content',
    render: (row: Notification) => h('div', { class: 'text-xs text-gray-500 line-clamp-2' }, row.content)
  },
  {
    title: '类型',
    key: 'type'
  },
  {
    title: '时间',
    key: 'created_at',
    render: (row: Notification) => formatDate(row.created_at)
  },
  {
    title: '状态',
    key: 'is_read',
    render: (row: Notification) => row.is_read ? '已读' : '未读'
  },
  {
    title: '操作',
    key: 'actions',
    render: (row: Notification) => h('div', { class: 'flex gap-2' }, [
      h(NButton, { size: 'tiny', onClick: async () => handleMarkRead(row) }, { default: () => '标记已读' }),
      h(NButton, { size: 'tiny', type: 'error', ghost: true, onClick: async () => handleDelete(row) }, { default: () => '删除' })
    ])
  }
]

const fetchSummary = async () => {
  const res = await getNotificationSummary()
  if (res.data) {
    summary.total = res.data.total
    summary.unread_count = res.data.unread_count
    summary.by_type = res.data.by_type
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
    })
    if (res.data) {
      items.value = res.data.items
      pagination.itemCount = res.data.total
    }
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
    console.error('Failed to mark as read:', error)
  }
}

const handleMarkAllRead = async () => {
  try {
    await markAllAsRead()
    await handleRefresh()
  } catch (error) {
    console.error('Failed to mark all as read:', error)
  }
}

const handleDelete = async (row: Notification) => {
  try {
    await deleteNotification(row.id)
    await handleRefresh()
  } catch (error) {
    console.error('Failed to delete notification:', error)
  }
}

const handleBatchDelete = async () => {
  if (!selectedIds.value.length) return
  await batchDeleteNotifications(selectedIds.value)
  selectedIds.value = []
  await handleRefresh()
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
  notificationSocket.offNotification()
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
