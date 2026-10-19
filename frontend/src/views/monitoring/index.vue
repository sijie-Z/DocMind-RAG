<template>
  <div class="min-h-full bg-gradient-to-b from-slate-50/80 to-slate-100/60 dark:from-gray-950 dark:to-gray-900/80 p-6 space-y-6 transition-colors duration-300">
    <!-- 页面标题 -->
    <div class="flex flex-col md:flex-row md:items-end md:justify-between gap-4">
      <div>
        <h1 class="text-2xl md:text-3xl font-bold tracking-tight text-slate-800 dark:text-slate-100">
          {{ t('menu.monitoring') }}
        </h1>
        <p class="text-slate-500 dark:text-slate-400 mt-1 text-sm flex items-center gap-2">
          <span class="w-6 h-0.5 bg-blue-500 rounded-full"></span>
          实时监控系统运行状态
        </p>
      </div>
      <div class="flex items-center gap-3">
        <n-tag size="small" round :type="notificationStatusType" :bordered="false">
          {{ t('monitoring.notificationService') }}: {{ notificationStatusLabel }}
        </n-tag>
        <n-select
          v-model:value="timeRange"
          :options="timeRangeOptions"
          size="small"
          class="w-32"
          @update:value="onTimeRangeChange"
        />
        <n-button type="primary" round @click="refreshData" :loading="loading">
          <template #icon><n-icon><RefreshOutline /></n-icon></template>
          {{ t('monitoring.refresh') }}
        </n-button>
      </div>
    </div>

    <!-- Error state -->
    <div v-if="loadError" class="py-16">
      <n-result status="error" :title="t('monitoring.loadFailed')" :description="loadErrorMsg">
        <template #footer>
          <n-button type="primary" round @click="refreshData">{{ t('common.retry') }}</n-button>
        </template>
      </n-result>
    </div>

    <!-- Content (only show when no error) -->
    <template v-if="!loadError">
    <!-- 统计卡片 -->
    <div class="grid grid-cols-2 lg:grid-cols-4 gap-4">
      <!-- 系统状态 -->
      <div class="bg-white/90 dark:bg-gray-800/90 backdrop-blur rounded-2xl border border-slate-200/60 dark:border-gray-700/60 p-5 shadow-sm">
        <div class="flex items-center gap-3 mb-4">
          <div class="w-10 h-10 rounded-xl bg-blue-100 dark:bg-blue-900/40 flex items-center justify-center">
            <n-icon size="20" class="text-blue-600 dark:text-blue-400"><PulseOutline /></n-icon>
          </div>
          <span class="text-sm font-medium text-slate-600 dark:text-slate-400">{{ t('monitoring.systemStatus') }}</span>
        </div>
        <div class="space-y-3">
          <div class="flex justify-between items-center">
            <span class="text-xs text-slate-500 dark:text-slate-400">{{ t('monitoring.cpu') }}</span>
            <span class="text-lg font-bold text-slate-800 dark:text-slate-100">{{ systemMetrics.cpu_percent || 0 }}%</span>
          </div>
          <div class="h-1.5 bg-slate-100 dark:bg-slate-700 rounded-full overflow-hidden">
            <div class="h-full bg-blue-500 rounded-full transition-all duration-500" :style="{ width: `${systemMetrics.cpu_percent || 0}%` }"></div>
          </div>
          <div class="flex justify-between items-center">
            <span class="text-xs text-slate-500 dark:text-slate-400">{{ t('monitoring.memory') }}</span>
            <span class="text-lg font-bold text-slate-800 dark:text-slate-100">{{ systemMetrics.memory_percent || 0 }}%</span>
          </div>
          <div class="h-1.5 bg-slate-100 dark:bg-slate-700 rounded-full overflow-hidden">
            <div class="h-full bg-emerald-500 rounded-full transition-all duration-500" :style="{ width: `${systemMetrics.memory_percent || 0}%` }"></div>
          </div>
        </div>
      </div>

      <!-- 应用状态 -->
      <div class="bg-white/90 dark:bg-gray-800/90 backdrop-blur rounded-2xl border border-slate-200/60 dark:border-gray-700/60 p-5 shadow-sm">
        <div class="flex items-center gap-3 mb-4">
          <div class="w-10 h-10 rounded-xl bg-emerald-100 dark:bg-emerald-900/40 flex items-center justify-center">
            <n-icon size="20" class="text-emerald-600 dark:text-emerald-400"><GitNetworkOutline /></n-icon>
          </div>
          <span class="text-sm font-medium text-slate-600 dark:text-slate-400">{{ t('monitoring.appStatus') }}</span>
        </div>
        <div class="grid grid-cols-2 gap-3">
          <div>
            <p class="text-xl font-bold text-slate-800 dark:text-slate-100">{{ appMetrics.active_connections || 0 }}</p>
            <p class="text-xs text-slate-500 dark:text-slate-400">{{ t('monitoring.activeConnections') }}</p>
          </div>
          <div>
            <p class="text-xl font-bold text-slate-800 dark:text-slate-100">{{ appMetrics.request_count || 0 }}</p>
            <p class="text-xs text-slate-500 dark:text-slate-400">{{ t('monitoring.totalRequests') }}</p>
          </div>
          <div>
            <p class="text-xl font-bold text-red-600 dark:text-red-400">{{ appMetrics.error_count || 0 }}</p>
            <p class="text-xs text-slate-500 dark:text-slate-400">{{ t('monitoring.errors') }}</p>
          </div>
          <div>
            <p class="text-xl font-bold text-slate-800 dark:text-slate-100">{{ appMetrics.response_time || 0 }}<span class="text-xs text-slate-400">ms</span></p>
            <p class="text-xs text-slate-500 dark:text-slate-400">{{ t('monitoring.avgResponseTime') }}</p>
          </div>
        </div>
      </div>

      <!-- 知识库状态 -->
      <div class="bg-white/90 dark:bg-gray-800/90 backdrop-blur rounded-2xl border border-slate-200/60 dark:border-gray-700/60 p-5 shadow-sm">
        <div class="flex items-center gap-3 mb-4">
          <div class="w-10 h-10 rounded-xl bg-amber-100 dark:bg-amber-900/40 flex items-center justify-center">
            <n-icon size="20" class="text-amber-600 dark:text-amber-400"><BookOutline /></n-icon>
          </div>
          <span class="text-sm font-medium text-slate-600 dark:text-slate-400">{{ t('monitoring.kbStatus') }}</span>
        </div>
        <div class="grid grid-cols-2 gap-3">
          <div>
            <p class="text-xl font-bold text-slate-800 dark:text-slate-100">{{ kbMetrics.total_documents || 0 }}</p>
            <p class="text-xs text-slate-500 dark:text-slate-400">{{ t('monitoring.totalDocs') }}</p>
          </div>
          <div>
            <p class="text-xl font-bold text-slate-800 dark:text-slate-100">{{ kbMetrics.total_chunks || 0 }}</p>
            <p class="text-xs text-slate-500 dark:text-slate-400">{{ t('monitoring.totalChunks') }}</p>
          </div>
          <div>
            <p class="text-xl font-bold text-slate-800 dark:text-slate-100">{{ kbMetrics.index_size_mb || 0 }}<span class="text-xs text-slate-400">MB</span></p>
            <p class="text-xs text-slate-500 dark:text-slate-400">{{ t('monitoring.indexSize') }}</p>
          </div>
          <div>
            <p class="text-xl font-bold text-slate-800 dark:text-slate-100">{{ kbMetrics.search_requests || 0 }}</p>
            <p class="text-xs text-slate-500 dark:text-slate-400">{{ t('monitoring.searchRequests') }}</p>
          </div>
        </div>
      </div>

      <!-- 告警状态 -->
      <div class="bg-white/90 dark:bg-gray-800/90 backdrop-blur rounded-2xl border border-slate-200/60 dark:border-gray-700/60 p-5 shadow-sm">
        <div class="flex items-center gap-3 mb-4">
          <div class="w-10 h-10 rounded-xl flex items-center justify-center" :class="hasCriticalAlerts ? 'bg-red-100 dark:bg-red-900/40' : hasWarningAlerts ? 'bg-amber-100 dark:bg-amber-900/40' : 'bg-emerald-100 dark:bg-emerald-900/40'">
            <n-icon size="20" :class="hasCriticalAlerts ? 'text-red-600 dark:text-red-400' : hasWarningAlerts ? 'text-amber-600 dark:text-amber-400' : 'text-emerald-600 dark:text-emerald-400'"><WarningOutline /></n-icon>
          </div>
          <span class="text-sm font-medium text-slate-600 dark:text-slate-400">{{ t('monitoring.alertStatus') }}</span>
        </div>
        <div class="text-center py-2">
          <p class="text-3xl font-bold" :class="hasCriticalAlerts ? 'text-red-600 dark:text-red-400' : hasWarningAlerts ? 'text-amber-600 dark:text-amber-400' : 'text-emerald-600 dark:text-emerald-400'">{{ activeAlerts.length }}</p>
          <p class="text-xs text-slate-500 dark:text-slate-400 mt-1">{{ t('monitoring.activeAlerts') }}</p>
        </div>
        <div class="flex gap-2 justify-center mt-3">
          <n-tag v-if="hasCriticalAlerts" type="error" size="small" round>{{ t('monitoring.critical') }}</n-tag>
          <n-tag v-if="hasWarningAlerts" type="warning" size="small" round>{{ t('monitoring.warning') }}</n-tag>
          <n-tag v-if="!hasAlerts" type="success" size="small" round>{{ t('monitoring.normal') }}</n-tag>
        </div>
      </div>
    </div>

    <!-- 趋势图表 -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <div class="bg-white/90 dark:bg-gray-800/90 backdrop-blur rounded-2xl border border-slate-200/60 dark:border-gray-700/60 p-5 shadow-sm">
        <h3 class="text-sm font-semibold text-slate-700 dark:text-slate-300 mb-4">{{ t('monitoring.cpuTrend') }}</h3>
        <div ref="cpuChartRef" style="height: 250px" />
      </div>
      <div class="bg-white/90 dark:bg-gray-800/90 backdrop-blur rounded-2xl border border-slate-200/60 dark:border-gray-700/60 p-5 shadow-sm">
        <h3 class="text-sm font-semibold text-slate-700 dark:text-slate-300 mb-4">{{ t('monitoring.memoryTrend') }}</h3>
        <div ref="memoryChartRef" style="height: 250px" />
      </div>
      <div class="bg-white/90 dark:bg-gray-800/90 backdrop-blur rounded-2xl border border-slate-200/60 dark:border-gray-700/60 p-5 shadow-sm">
        <h3 class="text-sm font-semibold text-slate-700 dark:text-slate-300 mb-4">{{ t('monitoring.requestTrend') }}</h3>
        <div ref="requestChartRef" style="height: 250px" />
      </div>
      <div class="bg-white/90 dark:bg-gray-800/90 backdrop-blur rounded-2xl border border-slate-200/60 dark:border-gray-700/60 p-5 shadow-sm">
        <h3 class="text-sm font-semibold text-slate-700 dark:text-slate-300 mb-4">{{ t('monitoring.responseTrend') }}</h3>
        <div ref="responseTimeChartRef" style="height: 250px" />
      </div>
    </div>

    <!-- 告警列表 -->
    <div class="bg-white/90 dark:bg-gray-800/90 backdrop-blur rounded-2xl border border-slate-200/60 dark:border-gray-700/60 p-5 shadow-sm">
      <h3 class="text-sm font-semibold text-slate-700 dark:text-slate-300 mb-4">{{ t('monitoring.recentAlerts') }}</h3>
      <n-data-table
        :columns="alertColumns"
        :data="recentAlerts"
        :loading="loading"
        :pagination="{ pageSize: 5 }"
        size="small"
      />
    </div>

    <!-- 审计日志 -->
    <div class="bg-white/90 dark:bg-gray-800/90 backdrop-blur rounded-2xl border border-slate-200/60 dark:border-gray-700/60 p-5 shadow-sm">
      <h3 class="text-sm font-semibold text-slate-700 dark:text-slate-300 mb-4">{{ t('menu.auditLogs') }}</h3>
      <n-data-table
        remote
        :columns="auditColumns"
        :data="auditLogs"
        :loading="auditLoading"
        :pagination="auditPagination"
        :row-key="(row: AuditLogEntry) => row.id"
        @update:page="handleAuditPageChange"
        size="small"
      />
    </div>
    </template> <!-- end !loadError -->
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, onUnmounted, h } from 'vue'
import { useDedupedMessage } from '@/utils/message'
import { NTag } from 'naive-ui'
import { useI18n } from 'vue-i18n'
import { getAuditLogs, type AuditLogEntry } from '@/api/user'
import { RefreshOutline, PulseOutline, GitNetworkOutline, BookOutline, WarningOutline } from '@vicons/ionicons5'
import echarts from '@/utils/echarts'
import { getMonitoringDashboard, getMonitoringAlerts, getMetrics, getNotificationStatus, type TrendPoint, type NotificationServiceStatus } from '@/api/monitoring'
import { apiUnwrap } from '@/utils/apiUnwrap'
import { getErrorMessage } from '@/utils/apiError'
import { filterPointsByTimeRange } from './timeRange'

// E14/D1：审计日志错误由本页 toast 呈现 —— 带 X-Silent-Error 关闭响应拦截器
// 的全局 toast，避免同一失败两次弹提示。
const AUDIT_SILENT_CONFIG = { headers: { 'X-Silent-Error': '1' } } as const

interface SystemMetrics {
  cpu_percent: number
  memory_percent: number
  disk_usage: Record<string, number>
  network_io: Record<string, number>
  process_count: number
  load_average: number[]
}

interface AppMetrics {
  active_connections: number
  request_count: number
  error_count: number
  response_time: number
  api_endpoints: Record<string, unknown>
  database_connections: number
  redis_connections: number
  elasticsearch_health: string
}

interface KnowledgeBaseMetrics {
  total_documents: number
  total_chunks: number
  index_size_mb: number
  search_requests: number
  search_response_time: number
  document_types: Record<string, number>
  user_activity: Record<string, number>
}

interface Alert {
  type: string
  level: 'warning' | 'critical'
  message: string
  value: number
  threshold: number
  timestamp: number
}

const message = useDedupedMessage()
const { t } = useI18n()
const loading = ref(false)
const loadError = ref(false)
const loadErrorMsg = ref('')
const timeRange = ref('1h')
const refreshInterval = ref<NodeJS.Timeout | null>(null)

const timeRangeOptions = computed(() => [
  { label: t('monitoring.timeRange.hour1'), value: '1h' },
  { label: t('monitoring.timeRange.hour6'), value: '6h' },
  { label: t('monitoring.timeRange.hour12'), value: '12h' },
  { label: t('monitoring.timeRange.day1'), value: '24h' },
  { label: t('monitoring.timeRange.day7'), value: '7d' }
])

const systemMetrics = reactive<SystemMetrics>({
  cpu_percent: 0,
  memory_percent: 0,
  disk_usage: {},
  network_io: {},
  process_count: 0,
  load_average: []
})

const appMetrics = reactive<AppMetrics>({
  active_connections: 0,
  request_count: 0,
  error_count: 0,
  response_time: 0,
  api_endpoints: {},
  database_connections: 0,
  redis_connections: 0,
  elasticsearch_health: 'unknown'
})

const kbMetrics = reactive<KnowledgeBaseMetrics>({
  total_documents: 0,
  total_chunks: 0,
  index_size_mb: 0,
  search_requests: 0,
  search_response_time: 0,
  document_types: {},
  user_activity: {}
})

const recentAlerts = ref<Alert[]>([])
// 后端 alerts 的 timestamp 是秒级 int(time.time())（monitoring.py:175-184；
// 表格列同样按秒处理 *1000），此前直接与毫秒 Date.now() 比较 → 恒 false →
// 「活动告警」卡恒 0 且恒「正常」，与下方表格 active 行同屏矛盾。统一按毫秒换算。
const activeAlerts = computed(() => recentAlerts.value.filter(alert => alert.timestamp * 1000 > Date.now() - 3600000))
const hasAlerts = computed(() => activeAlerts.value.length > 0)
const hasCriticalAlerts = computed(() => activeAlerts.value.some(alert => alert.level === 'critical'))
const hasWarningAlerts = computed(() => activeAlerts.value.some(alert => alert.level === 'warning'))

// T6：通知服务状态 —— 子请求失败只收敛为本地降级徽标（未知），不弹全局 toast。
const notificationStatus = ref<NotificationServiceStatus>({
  enabled_channels: [],
  status: 'unknown'
})

const notificationStatusLabel = computed(() => {
  if (notificationStatus.value.status === 'active') return t('monitoring.notificationActive')
  if (notificationStatus.value.status === 'inactive') return t('monitoring.notificationInactive')
  return t('monitoring.notificationUnknown')
})

const notificationStatusType = computed<'success' | 'default' | 'warning'>(() => {
  if (notificationStatus.value.status === 'active') return 'success'
  if (notificationStatus.value.status === 'inactive') return 'default'
  return 'warning'
})

// 审计日志相关
const auditLogs = ref<AuditLogEntry[]>([])
const auditLoading = ref(false)
const auditPagination = reactive({
  page: 1,
  pageSize: 10,
  itemCount: 0,
  showSizePicker: true,
  pageSizes: [10, 20, 50]
})

const auditColumns = [
  { title: '用户', key: 'username', width: 120 },
  {
    title: '动作',
    key: 'action',
    width: 150,
    render(row: AuditLogEntry) {
      const typeMap: Record<string, string> = {
        'login': 'success',
        'delete_file': 'error',
        'upload_file': 'info',
        'chat': 'warning'
      }
      return h(NTag, {
        type: (typeMap[row.action] || 'default') as 'success' | 'error' | 'info' | 'warning' | 'default',
        size: 'small',
        round: true
      }, { default: () => row.action })
    }
  },
  { title: '目标', key: 'target_type', width: 100 },
  { title: '详情', key: 'detail' },
  { title: 'IP', key: 'ip_address', width: 140 },
  {
    title: '时间',
    key: 'created_at',
    width: 180,
    render(row: AuditLogEntry) {
      return new Date(row.created_at).toLocaleString()
    }
  }
]

// 审计日志请求序号：刷新与翻页可能重叠，旧响应不得覆盖新分页数据，
// 也不得在新请求成功后补弹旧失败 toast。
let auditSeq = 0

const fetchAuditLogs = async () => {
  const seq = ++auditSeq
  auditLoading.value = true
  try {
    const res = await getAuditLogs({
      skip: (auditPagination.page - 1) * auditPagination.pageSize,
      limit: auditPagination.pageSize
    }, AUDIT_SILENT_CONFIG)
    if (seq !== auditSeq) return
    const resData = res.data as unknown as Record<string, unknown>
    const innerData = resData?.data as Record<string, unknown> | undefined
    if (resData?.success && innerData) {
      auditLogs.value = innerData.items as AuditLogEntry[]
      auditPagination.itemCount = innerData.total as number
    } else if (resData?.items) {
      auditLogs.value = resData.items as AuditLogEntry[]
      auditPagination.itemCount = resData.total as number
    }
  } catch (err) {
    if (seq !== auditSeq) return
    // E14/D1：错误如实且只有这一条（silent 头已关闭全局 toast）
    message.error(getErrorMessage(err, t('monitoring.auditLoadFailed')))
  } finally {
    if (seq === auditSeq) auditLoading.value = false
  }
}

const handleAuditPageChange = (page: number) => {
  auditPagination.page = page
  fetchAuditLogs()
}

const alertColumns = computed(() => [
  {
    title: t('monitoring.alert.level'),
    key: 'level',
    width: 80,
    render(row: Alert) {
      const type = row.level === 'critical' ? 'error' : row.level === 'warning' ? 'warning' : 'info'
      const label = row.level === 'critical' ? t('monitoring.critical') : row.level === 'warning' ? t('monitoring.warning') : t('monitoring.info')
      return h(
        'div',
        { class: `text-${type}` },
        label
      )
    }
  },
  {
    title: t('monitoring.alert.message'),
    key: 'message'
  },
  {
    title: t('monitoring.alert.time'),
    key: 'timestamp',
    width: 180,
    render(row: Alert) {
      return new Date(row.timestamp * 1000).toLocaleString()
    }
  },
  {
    title: t('monitoring.alert.status'),
    key: 'status',
    width: 100
  }
])

const cpuChartRef = ref<HTMLElement>()
const memoryChartRef = ref<HTMLElement>()
const requestChartRef = ref<HTMLElement>()
const responseTimeChartRef = ref<HTMLElement>()
let cpuChart: echarts.ECharts | null = null
let memoryChart: echarts.ECharts | null = null
let requestChart: echarts.ECharts | null = null
let responseTimeChart: echarts.ECharts | null = null

// 图表 title.subtext 承载诚实空态文案（无数据时画布写明「暂无数据」而非白板）
const emptySubtext = () => t('monitoring.noData')
const subtextStyle = { color: '#9ca3af', fontSize: 12 }

// CPU/内存趋势：后端系统指标采样已落地（fix/system-metrics-sampling @ af42e32）
// ——dashboard.trends.system 与 /monitoring/metrics/system 现返回真实采样点
// {timestamp(秒), cpu_percent, memory_percent}（ENABLE_MONITORING 门控）。
// 由 loadData → updateSystemCharts 接线；采样未启用或暂无样本时为空数组，
// 保持「暂无数据」诚实空态（不画假线）。
const initCharts = () => {
  if (cpuChartRef.value) {
    cpuChart = echarts.init(cpuChartRef.value)
    cpuChart.setOption({
      title: { text: t('monitoring.cpu') + ' (%)', subtext: emptySubtext(), subtextStyle, left: 'center' },
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'time' },
      yAxis: { type: 'value', min: 0, max: 100 },
      series: [{
        name: t('monitoring.cpu'),
        type: 'line',
        data: [],
        smooth: true,
        lineStyle: { color: '#5470c6' }
      }]
    })
  }

  if (memoryChartRef.value) {
    memoryChart = echarts.init(memoryChartRef.value)
    memoryChart.setOption({
      title: { text: t('monitoring.memory') + ' (%)', subtext: emptySubtext(), subtextStyle, left: 'center' },
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'time' },
      yAxis: { type: 'value', min: 0, max: 100 },
      series: [{
        name: t('monitoring.memory'),
        type: 'line',
        data: [],
        smooth: true,
        lineStyle: { color: '#91cc75' }
      }]
    })
  }

  if (requestChartRef.value) {
    requestChart = echarts.init(requestChartRef.value)
    requestChart.setOption({
      title: { text: t('monitoring.requestTrend'), subtext: emptySubtext(), subtextStyle, left: 'center' },
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'time' },
      yAxis: { type: 'value' },
      series: [{
        name: t('monitoring.totalRequests'),
        type: 'line',
        data: [],
        smooth: true,
        lineStyle: { color: '#fac858' }
      }]
    })
  }

  if (responseTimeChartRef.value) {
    responseTimeChart = echarts.init(responseTimeChartRef.value)
    responseTimeChart.setOption({
      title: { text: t('monitoring.responseTrend') + ' (ms)', subtext: emptySubtext(), subtextStyle, left: 'center' },
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'time' },
      yAxis: { type: 'value' },
      series: [{
        name: t('monitoring.avgResponseTime'),
        type: 'line',
        data: [],
        smooth: true,
        lineStyle: { color: '#ee6666' }
      }]
    })
  }
}

const updateMetricsCharts = async (seq: number) => {
  try {
    // 趋势真实形状：{"data": [<快照>]}（monitoring.py:151-166；<2 条时 {"data": []}），
    // 元素键 timestamp(秒)/request_count/response_time 等（core/middleware.py:129-136）。
    // 旧前端读不存在的 resp.data.metrics → 三图恒不更新。
    const appMetricsResponse = await getMetrics('application', timeRange.value)
    // 竞态守卫：请求等待期间可能已有更新的 loadData 启动并完成 —— 迟到的
    // 图表数据不得覆盖最新结果（旧响应只允许在它仍是最新请求时写状态）。
    if (seq !== loadSeq) return
    // GET /monitoring/metrics/{type} 为裸 {data: [...]} 单层体 → apiUnwrap 归一为数组
    const points = apiUnwrap<TrendPoint[]>(appMetricsResponse) || []

    if (requestChart) {
      const requestData = points.map((item) => [item.timestamp * 1000, item.request_count])
      requestChart.setOption({
        title: { subtext: points.length ? '' : emptySubtext() },
        series: [{ data: requestData }]
      })
    }

    if (responseTimeChart) {
      const responseTimeData = points.map((item) => [item.timestamp * 1000, item.response_time])
      responseTimeChart.setOption({
        title: { subtext: points.length ? '' : emptySubtext() },
        series: [{ data: responseTimeData }]
      })
    }
  } catch (error) {
    // 更新指标图表失败：保持空态（subtext 已在初始化写明「暂无数据」）
  }
}

/**
 * 系统指标趋势（CPU/内存）接线：数据源 dashboard.trends.system —— 采样点
 * {timestamp(秒), cpu_percent, memory_percent}（af42e32 实现报告逐字核对；
 * 与 /metrics/system 同一信封但本图走 dashboard 既有响应，零额外请求）。
 * timestamp×1000 转毫秒与前端时间轴既有约定一致；空数组 → 保持
 * 「暂无数据」诚实空态（不画假线）。
 */
const updateSystemCharts = (
  systemTrend: Array<{ timestamp: number; cpu_percent: number; memory_percent: number }> | undefined
) => {
  // T8：按时间范围选择器裁剪（dashboard.trends.system 不经 /metrics，后端无法代劳）；
  // 空数组 → 保持「暂无数据」诚实空态（不画假线）。
  const points = filterPointsByTimeRange(Array.isArray(systemTrend) ? systemTrend : [], timeRange.value)

  if (cpuChart) {
    const cpuData = points.map((item) => [item.timestamp * 1000, item.cpu_percent])
    cpuChart.setOption({
      title: { subtext: points.length ? '' : emptySubtext() },
      series: [{ data: cpuData }]
    })
  }

  if (memoryChart) {
    const memoryData = points.map((item) => [item.timestamp * 1000, item.memory_percent])
    memoryChart.setOption({
      title: { subtext: points.length ? '' : emptySubtext() },
      series: [{ data: memoryData }]
    })
  }
}

// 请求序号（E14/D1）：自动刷新（30s）、手动刷新、切时间范围可能重叠——
// 只认最后一次请求的状态，旧响应（无论成功或失败）不得覆盖最新结果/错误态。
let loadSeq = 0

const loadData = async () => {
  const seq = ++loadSeq
  loading.value = true
  try {
    const [dashboardResponse, alertsResponse] = await Promise.all([
      getMonitoringDashboard(),
      getMonitoringAlerts()
    ])
    if (seq !== loadSeq) return

    const dashboardData = dashboardResponse.data
    const alertsData = alertsResponse.data

    // 更新系统指标
    if (dashboardData.current?.system) {
      Object.assign(systemMetrics, dashboardData.current.system)
    }

    // 更新应用指标
    if (dashboardData.current?.application) {
      Object.assign(appMetrics, dashboardData.current.application)
    }

    // 更新知识库指标
    if (dashboardData.current?.knowledge_base) {
      Object.assign(kbMetrics, dashboardData.current.knowledge_base)
    }

    // 更新告警
    recentAlerts.value = alertsData.alerts || []

    // 系统指标趋势（CPU/内存）：dashboard.trends.system 真实采样点
    // （fix/system-metrics-sampling @ af42e32；空数组 → 诚实空态）
    updateSystemCharts(dashboardData.trends?.system)

    // 更新时间范围相关的图表（请求/响应趋势：真实 {data: history[]}）
    await updateMetricsCharts(seq)

    // 获取通知服务状态（T6：失败仅更新本地降级状态，不弹全局 toast）
    try {
      // 单前缀正确路径：axios baseURL 已含 /api/v1（utils/request.ts:140），
      // 旧写法 /api/monitoring/... 实际请求 /api/v1/api/monitoring/... 恒 404
      // （A_sweep A4 实锤 404；真实路由 monitoring.py:442）
      const notificationResponse = await getNotificationStatus()
      // 竞态守卫：迟到响应不得覆盖最新请求的状态
      if (seq !== loadSeq) return
      notificationStatus.value = notificationResponse.data
    } catch {
      // 失败降级：徽标显示「未知」，可感知但不打扰（请求已带 X-Silent-Error）
      if (seq !== loadSeq) return
      notificationStatus.value = { enabled_channels: [], status: 'unknown' }
    }

    // E14/D1：成功路径复位错误态 —— 自动刷新（30s）/切时间范围再次成功时，
    // 内容必须恢复展示，不能因一次失败永久停留在错误页（直到手动重试）。
    // 仅最新请求可修改状态（旧请求迟到成功不得清掉新请求刚设置的错误态）。
    if (seq !== loadSeq) return
    loadError.value = false

  } catch (error: unknown) {
    if (seq !== loadSeq) return
    // E14/D1：错误如实（5xx/传输层回退 i18n，不上屏 axios 黑话）。
    // 内联 n-result（标题/描述/重试）已是唯一提示表面 —— 不再弹 toast；
    // 页面每 30s 自动刷新，弹 toast 还会造成周期性骚扰。
    loadError.value = true
    loadErrorMsg.value = getErrorMessage(error, t('monitoring.loadFailedDesc'))
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

const refreshData = () => {
  loadError.value = false
  loadData()
  fetchAuditLogs()
}

const onTimeRangeChange = () => {
  loadData()
}

onMounted(() => {
  initCharts()
  loadData()
  fetchAuditLogs()
  
  // 设置自动刷新
  refreshInterval.value = setInterval(() => {
    loadData()
  }, 30000) // 每30秒刷新一次
})

onUnmounted(() => {
  if (refreshInterval.value) {
    clearInterval(refreshInterval.value)
  }
  if (cpuChart) {
    cpuChart.dispose()
  }
  if (memoryChart) {
    memoryChart.dispose()
  }
  if (requestChart) {
    requestChart.dispose()
  }
  if (responseTimeChart) {
    responseTimeChart.dispose()
  }
})
</script>

<style scoped>
/* Minimal styles - using Tailwind classes in template */
</style>
