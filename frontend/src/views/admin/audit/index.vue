<template>
  <div class="audit-page p-6">
    <div class="bg-white dark:bg-gray-800 rounded-2xl border border-gray-100 dark:border-gray-700 shadow-sm">
      <!-- 顶部工具栏 -->
      <div class="p-4 border-b border-gray-100 dark:border-gray-700 flex flex-wrap items-center justify-between gap-4">
        <h2 class="text-xl font-bold text-gray-900 dark:text-white">{{ t('menu.auditLogs') }}</h2>
        <div class="flex items-center gap-3">
          <n-input v-model:value="searchText" :placeholder="t('audit.searchPlaceholder')" clearable round class="w-48" />
          <n-select v-model:value="actionFilter" :options="actionOptions" :placeholder="t('audit.actionFilter')" clearable class="w-36" />
          <n-date-picker v-model:value="dateRange" type="daterange" clearable class="w-64" />
          <n-button type="primary" @click="handleSearch" :loading="loading">
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
          remote
          :columns="columns"
          :data="logs"
          :loading="loading"
          :pagination="pagination"
          :row-key="(row: AuditLogEntry) => row.id"
          :bordered="false"
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
import { getAuditLogs, type AuditLogEntry } from '@/api/user'
import { downloadAuditLogs, getAuditActions } from '@/api/audit'
import { formatDate } from '@/utils/format'
import { DownloadOutline } from '@vicons/ionicons5'

const { t } = useI18n()
const message = useDedupedMessage()

const searchText = ref('')
const actionFilter = ref<string | null>(null)
const dateRange = ref<[number, number] | null>(null)
const loading = ref(false)
const logs = ref<AuditLogEntry[]>([])

// T8：操作类型选项从 API 动态生成 —— 只列当前数据里真实存在的动作（带计数），
// 杜绝此前硬编码值与真实动作串不一致（如 'delete' vs 'delete_document'）导致的
// 「选中即恒空」。已知动作映射中文标签；未知动作回退原始串（诚实）。
const ACTION_LABEL_KEYS: Record<string, string> = {
  login: 'audit.actionTypes.login',
  upload: 'audit.actionTypes.upload',
  upload_document: 'audit.actionTypes.upload',
  delete_user: 'audit.actionTypes.delete',
  delete_document: 'audit.actionTypes.delete',
  delete_organization: 'audit.actionTypes.delete',
  update_role: 'audit.actionTypes.roleChange',
  reset_password: 'audit.actionTypes.passwordReset',
}

const actionOptions = ref<Array<{ label: string; value: string }>>([])

const loadAuditActions = async () => {
  try {
    const res = await getAuditActions()
    const rows = ((res.data as unknown as Record<string, unknown>)?.data as Array<{ action: string; count: number }>) || []
    actionOptions.value = rows.map((row) => {
      const key = ACTION_LABEL_KEYS[row.action]
      const base = key ? t(key) : row.action
      return { label: `${base} (${row.count})`, value: row.action }
    })
  } catch {
    // 选项加载失败：下拉留空（文本搜索不受影响），不阻塞页面
    actionOptions.value = []
  }
}

const SENSITIVE_ACTIONS = ['delete_user', 'delete_document', 'reset_password', 'update_role', 'delete_organization', 'upload']

const pagination = ref({
  page: 1,
  pageSize: 20,
  itemCount: 0,
  showSizePicker: true,
  pageSizes: [10, 20, 50],
  // T8：remote 表下页码/页大小变更必须回写后重新请求（旧版分页回调缺失，
  // naive-ui 受控分页点 next 零请求、表格清空——「翻页失效」根因）。
  onChange: (page: number) => {
    pagination.value.page = page
    loadLogs()
  },
  onUpdatePageSize: (pageSize: number) => {
    pagination.value.pageSize = pageSize
    pagination.value.page = 1
    loadLogs()
  }
})

const columns = [
  {
    title: t('audit.columns.time'),
    key: 'created_at',
    width: 170,
    render: (row: AuditLogEntry) => formatDate(row.created_at)
  },
  {
    title: t('audit.columns.user'),
    // 用户列绑定真实 username 字段（同 API 已返回，users.py:328）；此前绑
    // user_id 直出数字 1/3。username 缺失（如用户已删除）时回退 user_id。
    key: 'username',
    width: 100,
    render: (row: AuditLogEntry) => row.username || String(row.user_id ?? '-')
  },
  {
    title: t('audit.columns.action'),
    key: 'action',
    width: 150,
    render: (row: AuditLogEntry) => {
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
    render: (row: AuditLogEntry) => row.target_type || '-'
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
    render: (row: AuditLogEntry) => row.ip_address || '-'
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
    const resData = res.data as unknown as Record<string, unknown>
    const items = (resData?.data as Record<string, unknown>)?.items
    if (items) {
      logs.value = items as AuditLogEntry[]
      pagination.value.itemCount = ((resData?.data as Record<string, unknown>)?.total as number) || 0
    }
  } catch (e) {
    // Failed to load audit logs
  } finally {
    loading.value = false
  }
}

const exportLogs = () => {
  const params: Record<string, string> = {}
  if (actionFilter.value) params.action = actionFilter.value
  if (dateRange.value) {
    params.start_date = new Date(dateRange.value[0]).toISOString().slice(0, 10)
    params.end_date = new Date(dateRange.value[1]).toISOString().slice(0, 10)
  }
  downloadAuditLogs(params)
  message.success(t('common.success'))
}

/** T8：筛选条件变化后从第 1 页重新查询（避免停留在越界页码） */
const handleSearch = () => {
  pagination.value.page = 1
  loadLogs()
}

onMounted(() => {
  loadLogs()
  loadAuditActions()
})
</script>
