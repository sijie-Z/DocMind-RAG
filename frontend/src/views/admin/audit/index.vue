<template>
  <div class="audit-page p-6">
    <div class="bg-white dark:bg-gray-800 rounded-2xl border border-gray-100 dark:border-gray-700 shadow-sm">
      <!-- 顶部工具栏 -->
      <div class="p-4 border-b border-gray-100 dark:border-gray-700 flex flex-wrap items-center justify-between gap-4">
        <h2 class="text-xl font-bold text-gray-900 dark:text-white">{{ t('audit.title') }}</h2>
        <div class="flex items-center gap-3">
          <n-input v-model:value="searchText" :placeholder="t('audit.searchPlaceholder')" clearable round class="w-48" />
          <n-select v-model:value="actionFilter" :options="actionOptions" :placeholder="t('audit.actionFilter')" clearable class="w-36" />
          <n-date-picker v-model:value="dateRange" type="daterange" clearable class="w-64" />
          <n-button type="primary" @click="loadLogs" :loading="loading">
            {{ t('audit.search') }}
          </n-button>
          <n-button @click="exportLogs">
            <template #icon><n-icon><DownloadOutline /></n-icon></template>
            {{ t('audit.export') }}
          </n-button>
        </div>
      </div>

      <!-- 表格 -->
      <div class="p-4">
        <n-data-table
          :columns="columns"
          :data="logs"
          :loading="loading"
          :pagination="pagination"
          :row-key="(row: any) => row.id"
          :bordered="false"
          @update:page="loadLogs"
          @update:page-size="loadLogs"
        />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, h } from 'vue'
import { useDedupedMessage } from '@/utils/message'
import { useI18n } from 'vue-i18n'
import { NTag, NButton, NIcon, } from 'naive-ui'
import { getAuditLogs } from '@/api/user'
import { downloadAuditLogs } from '@/api/audit'
import { formatDate } from '@/utils/format'
import { DownloadOutline } from '@vicons/ionicons5'

const { t } = useI18n()
const message = useDedupedMessage()

const searchText = ref('')
const actionFilter = ref<string | null>(null)
const dateRange = ref<[number, number] | null>(null)
const loading = ref(false)
const logs = ref<any[]>([])

const actionOptions = [
  { label: t('audit.actionTypes.login'), value: 'login' },
  { label: t('audit.actionTypes.upload'), value: 'upload_document' },
  { label: t('audit.actionTypes.delete'), value: 'delete' },
  { label: t('audit.actionTypes.roleChange'), value: 'update_role' },
  { label: t('audit.actionTypes.passwordReset'), value: 'reset_password' }
]

const SENSITIVE_ACTIONS = ['delete_user', 'delete_document', 'reset_password', 'update_role', 'delete_organization', 'upload']

const pagination = ref({
  page: 1,
  pageSize: 20,
  itemCount: 0,
  showSizePicker: true,
  pageSizes: [10, 20, 50]
})

const columns = [
  {
    title: t('audit.columns.time'),
    key: 'created_at',
    width: 170,
    render: (row: any) => formatDate(row.created_at)
  },
  {
    title: t('audit.columns.user'),
    key: 'user_id',
    width: 80
  },
  {
    title: t('audit.columns.action'),
    key: 'action',
    width: 150,
    render: (row: any) => {
      const isSensitive = SENSITIVE_ACTIONS.some(a => row.action?.includes(a))
      return h(NTag, {
        type: isSensitive ? 'error' : 'info',
        size: 'small',
        bordered: false
      }, { default: () => row.action || '-' })
    }
  },
  {
    title: t('audit.columns.target'),
    key: 'target_type',
    width: 100,
    render: (row: any) => row.target_type || '-'
  },
  {
    title: t('audit.columns.detail'),
    key: 'detail',
    ellipsis: { tooltip: true }
  },
  {
    title: 'IP',
    key: 'ip_address',
    width: 130,
    render: (row: any) => row.ip_address || '-'
  }
]

const loadLogs = async () => {
  loading.value = true
  try {
    const res = await getAuditLogs({
      skip: (pagination.value.page - 1) * pagination.value.pageSize,
      limit: pagination.value.pageSize,
      action: actionFilter.value || undefined,
      search: searchText.value || undefined,
      start_date: dateRange.value ? new Date(dateRange.value[0]).toISOString().slice(0, 10) : undefined,
      end_date: dateRange.value ? new Date(dateRange.value[1]).toISOString().slice(0, 10) : undefined
    })
    if ((res.data as any)?.data?.items) {
      logs.value = (res.data as any).data.items
      pagination.value.itemCount = (res.data as any).data.total || 0
    }
  } catch (e) {
    console.error('Failed to load audit logs:', e)
  } finally {
    loading.value = false
  }
}

const exportLogs = () => {
  const params: Record<string, any> = {}
  if (actionFilter.value) params.action = actionFilter.value
  if (dateRange.value) {
    params.start_date = new Date(dateRange.value[0]).toISOString().slice(0, 10)
    params.end_date = new Date(dateRange.value[1]).toISOString().slice(0, 10)
  }
  downloadAuditLogs(params)
  message.success(t('common.success'))
}

onMounted(loadLogs)
</script>