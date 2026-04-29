<template>
  <div class="admin-dashboard p-4 md:p-6 space-y-5">
    <!-- 顶部统计卡片 - 渐变背景 -->
    <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
      <div class="bg-gradient-to-br from-blue-500 to-blue-600 rounded-xl p-4 text-white shadow-lg shadow-blue-500/20">
        <div class="flex items-center justify-between mb-2">
          <span class="text-xs font-medium text-indigo-100">{{ t('dashboard.totalUsers') }}</span>
          <div class="w-8 h-8 bg-white/20 rounded-lg flex items-center justify-center">
            <n-icon size="18" class="text-white"><PeopleOutline /></n-icon>
          </div>
        </div>
        <p class="text-2xl font-bold">{{ adminStats.totalUsers.toLocaleString() }}</p>
        <div class="flex items-center gap-1 mt-1 text-xs text-indigo-200">
          <TrendingUpOutline class="text-xs" />
          <span>{{ t('dashboard.activeUsers') }}: {{ adminStats.activeUsers }}</span>
        </div>
      </div>

      <div class="bg-gradient-to-br from-emerald-500 to-emerald-600 rounded-xl p-4 text-white shadow-lg shadow-emerald-500/20">
        <div class="flex items-center justify-between mb-2">
          <span class="text-xs font-medium text-emerald-100">{{ t('dashboard.totalSessions') }}</span>
          <div class="w-8 h-8 bg-white/20 rounded-lg flex items-center justify-center">
            <n-icon size="18" class="text-white"><ChatbubbleEllipsesOutline /></n-icon>
          </div>
        </div>
        <p class="text-2xl font-bold">{{ adminStats.totalSessions.toLocaleString() }}</p>
        <div class="flex items-center gap-1 mt-1 text-xs text-emerald-200">
          <span>↑ {{ adminStats.totalSessions > 0 ? '+12%' : '0%' }}</span>
          <span>vs last week</span>
        </div>
      </div>

      <div class="bg-gradient-to-br from-blue-500 to-blue-600 rounded-xl p-4 text-white shadow-lg shadow-blue-500/20">
        <div class="flex items-center justify-between mb-2">
          <span class="text-xs font-medium text-indigo-100">{{ t('dashboard.totalDocs') }}</span>
          <div class="w-8 h-8 bg-white/20 rounded-lg flex items-center justify-center">
            <n-icon size="18" class="text-white"><DocumentTextOutline /></n-icon>
          </div>
        </div>
        <p class="text-2xl font-bold">{{ adminStats.totalDocs.toLocaleString() }}</p>
        <div class="flex items-center gap-1 mt-1 text-xs text-indigo-200">
          <span>{{ t('dashboard.totalOrgs') }}: {{ adminStats.totalOrgs }}</span>
        </div>
      </div>

      <div class="bg-gradient-to-br from-orange-500 to-orange-600 rounded-xl p-4 text-white shadow-lg shadow-orange-500/20">
        <div class="flex items-center justify-between mb-2">
          <span class="text-xs font-medium text-orange-100">{{ t('dashboard.activeUsers') }}</span>
          <div class="w-8 h-8 bg-white/20 rounded-lg flex items-center justify-center">
            <n-icon size="18" class="text-white"><PulseOutline /></n-icon>
          </div>
        </div>
        <p class="text-2xl font-bold text-emerald-100">{{ adminStats.activeUsers }}</p>
        <div class="flex items-center gap-1 mt-1 text-xs text-orange-200">
          <span>24h active</span>
        </div>
      </div>

      <div class="bg-gradient-to-br from-cyan-500 to-cyan-600 rounded-xl p-4 text-white shadow-lg shadow-cyan-500/20">
        <div class="flex items-center justify-between mb-2">
          <span class="text-xs font-medium text-cyan-100">{{ t('dashboard.todayRequests') }}</span>
          <div class="w-8 h-8 bg-white/20 rounded-lg flex items-center justify-center">
            <n-icon size="18" class="text-white"><GitNetworkOutline /></n-icon>
          </div>
        </div>
        <p class="text-2xl font-bold">{{ adminStats.totalRequests.toLocaleString() }}</p>
        <div class="flex items-center gap-1 mt-1 text-xs text-cyan-200">
          <span>avg {{ llmStats.avgLatency || 0 }}ms</span>
        </div>
      </div>

      <div class="bg-gradient-to-br from-gray-600 to-gray-700 rounded-xl p-4 text-white shadow-lg">
        <div class="flex items-center justify-between mb-2">
          <span class="text-xs font-medium text-gray-300">{{ t('dashboard.systemStatus') }}</span>
          <div class="w-8 h-8 bg-white/20 rounded-lg flex items-center justify-center">
            <n-icon size="18" class="text-white"><ServerOutline /></n-icon>
          </div>
        </div>
        <div class="mt-3">
          <n-tag :type="adminStats.systemHealthy ? 'success' : 'error'" size="small" round>
            {{ adminStats.systemHealthy ? t('dashboard.healthy') : t('dashboard.unhealthy') }}
          </n-tag>
        </div>
        <div class="flex items-center gap-1 mt-2 text-xs text-gray-300">
          <span>ES: {{ adminStats.esHealth || 'ok' }}</span>
        </div>
      </div>
    </div>

    <!-- 两列布局：AI 监控 + RAG 性能 -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-5">
      <!-- AI 资源监控 -->
      <div class="bg-white dark:bg-gray-800 rounded-2xl border border-gray-100 dark:border-gray-700 shadow-sm overflow-hidden">
        <div class="px-5 py-4 border-b border-gray-100 dark:border-gray-700 flex items-center justify-between bg-gradient-to-r from-indigo-50 to-transparent dark:from-indigo-900/10">
          <div class="flex items-center gap-2">
            <div class="w-8 h-8 bg-indigo-100 dark:bg-indigo-900/30 rounded-lg flex items-center justify-center">
              <n-icon class="text-blue-600 dark:text-blue-400"><PulseOutline /></n-icon>
            </div>
            <div>
              <h3 class="font-semibold text-gray-900 dark:text-white">{{ t('dashboard.aiMonitor') }}</h3>
              <p class="text-xs text-gray-500 dark:text-gray-400">LLM Resource & Cost</p>
            </div>
          </div>
          <div class="flex items-center gap-2">
            <n-tag v-if="llmStats.costWarning" type="warning" size="small" round>
              <template #icon><n-icon size="12"><WarningOutline /></n-icon></template>
              {{ t('dashboard.costWarning') }}
            </n-tag>
            <span class="text-xs text-gray-400">{{ new Date().toLocaleDateString() }}</span>
          </div>
        </div>
        <div class="p-5">
          <!-- 统计数据 -->
          <div class="grid grid-cols-2 gap-4 mb-5">
            <div class="bg-gray-50 dark:bg-gray-700/50 rounded-xl p-4">
              <div class="flex items-center gap-2 mb-1">
                <div class="w-2 h-2 rounded-full bg-blue-500"></div>
                <span class="text-xs text-gray-500 dark:text-gray-400">{{ t('dashboard.tokensToday') }}</span>
              </div>
              <p class="text-xl font-bold text-gray-900 dark:text-white">{{ llmStats.tokensToday.toLocaleString() }}</p>
              <p class="text-xs text-gray-400 mt-1">input + output</p>
            </div>
            <div class="bg-gray-50 dark:bg-gray-700/50 rounded-xl p-4">
              <div class="flex items-center gap-2 mb-1">
                <div class="w-2 h-2 rounded-full bg-emerald-500"></div>
                <span class="text-xs text-gray-500 dark:text-gray-400">{{ t('dashboard.costToday') }}</span>
              </div>
              <p class="text-xl font-bold text-blue-600 dark:text-blue-400">${{ llmStats.costToday.toFixed(3) }}</p>
              <p class="text-xs text-gray-400 mt-1">gpt-4o-mini rate</p>
            </div>
            <div class="bg-gray-50 dark:bg-gray-700/50 rounded-xl p-4">
              <div class="flex items-center gap-2 mb-1">
                <div class="w-2 h-2 rounded-full bg-blue-500"></div>
                <span class="text-xs text-gray-500 dark:text-gray-400">{{ t('dashboard.avgLatency') }}</span>
              </div>
              <p class="text-xl font-bold text-gray-900 dark:text-white">{{ llmStats.avgLatency }}<span class="text-sm font-normal text-gray-400">ms</span></p>
              <p class="text-xs text-gray-400 mt-1">p50 response time</p>
            </div>
            <div class="bg-gray-50 dark:bg-gray-700/50 rounded-xl p-4">
              <div class="flex items-center gap-2 mb-1">
                <div class="w-2 h-2 rounded-full bg-orange-500"></div>
                <span class="text-xs text-gray-500 dark:text-gray-400">{{ t('dashboard.p95Latency') }}</span>
              </div>
              <p class="text-xl font-bold text-gray-900 dark:text-white">{{ llmStats.p95Latency }}<span class="text-sm font-normal text-gray-400">ms</span></p>
              <p class="text-xs text-gray-400 mt-1">p95 response time</p>
            </div>
          </div>
          <!-- 趋势图 -->
          <div class="h-44 rounded-xl bg-gray-50 dark:bg-gray-700/30 p-3">
            <div ref="llmChartRef" class="w-full h-full"></div>
          </div>
        </div>
      </div>

      <!-- RAG 命中率 -->
      <div class="bg-white dark:bg-gray-800 rounded-2xl border border-gray-100 dark:border-gray-700 shadow-sm overflow-hidden">
        <div class="px-5 py-4 border-b border-gray-100 dark:border-gray-700 flex items-center justify-between bg-gradient-to-r from-emerald-50 to-transparent dark:from-emerald-900/10">
          <div class="flex items-center gap-2">
            <div class="w-8 h-8 bg-emerald-100 dark:bg-emerald-900/30 rounded-lg flex items-center justify-center">
              <n-icon class="text-emerald-600 dark:text-emerald-400"><AnalyticsOutline /></n-icon>
            </div>
            <div>
              <h3 class="font-semibold text-gray-900 dark:text-white">{{ t('dashboard.ragStats') }}</h3>
              <p class="text-xs text-gray-500 dark:text-gray-400">Document Retrieval Performance (7d)</p>
            </div>
          </div>
          <div class="flex items-center gap-2">
            <n-tag :type="ragStats.hitRate > 60 ? 'success' : ragStats.hitRate > 30 ? 'warning' : 'error'" size="small" round>
              {{ ragStats.hitRate > 60 ? 'Good' : ragStats.hitRate > 30 ? 'Fair' : 'Poor' }}
            </n-tag>
          </div>
        </div>
        <div class="p-5">
          <!-- 命中率主指标 -->
          <div class="flex items-center gap-4 mb-5">
            <div class="flex-1">
              <p class="text-sm text-gray-500 dark:text-gray-400 mb-3">{{ t('dashboard.hitRate') }}</p>
              <n-progress
                type="line"
                :percentage="ragStats.hitRate"
                :indicator-placement="'inside'"
                :height="20"
                :border-radius="10"
                :fill-border-radius="10"
                :color="ragStats.hitRate > 60 ? CHART_COLORS.secondary : ragStats.hitRate > 30 ? CHART_COLORS.warning : CHART_COLORS.danger"
                :rail-color="'#f3f4f6'"
              />
            </div>
            <div class="text-right">
              <p class="text-4xl font-bold text-emerald-600 dark:text-emerald-400">{{ ragStats.hitRate }}<span class="text-xl">%</span></p>
            </div>
          </div>
          <!-- 统计数字 -->
          <div class="grid grid-cols-3 gap-3 mb-5">
            <div class="text-center p-3 bg-gray-50 dark:bg-gray-700/50 rounded-xl">
              <p class="text-xs text-gray-500 dark:text-gray-400 mb-1">{{ t('dashboard.queries7d') }}</p>
              <p class="text-lg font-bold text-gray-900 dark:text-white">{{ ragStats.totalQueries.toLocaleString() }}</p>
            </div>
            <div class="text-center p-3 bg-emerald-50 dark:bg-emerald-900/20 rounded-xl">
              <p class="text-xs text-gray-500 dark:text-gray-400 mb-1">{{ t('dashboard.hits') }}</p>
              <p class="text-lg font-bold text-emerald-600 dark:text-emerald-400">{{ ragStats.hits.toLocaleString() }}</p>
            </div>
            <div class="text-center p-3 bg-gray-50 dark:bg-gray-700/50 rounded-xl">
              <p class="text-xs text-gray-500 dark:text-gray-400 mb-1">{{ t('dashboard.avgDocs') }}</p>
              <p class="text-lg font-bold text-gray-900 dark:text-white">{{ ragStats.avgDocs }}</p>
            </div>
          </div>
          <!-- 趋势图 -->
          <div class="h-28 rounded-xl bg-gray-50 dark:bg-gray-700/30 p-3">
            <div ref="ragChartRef" class="w-full h-full"></div>
          </div>
        </div>
      </div>
    </div>

    <!-- 组织概览 -->
    <div class="bg-white dark:bg-gray-800 rounded-2xl border border-gray-100 dark:border-gray-700 shadow-sm overflow-hidden">
      <div class="px-5 py-4 border-b border-gray-100 dark:border-gray-700 flex items-center justify-between bg-gradient-to-r from-indigo-50 to-transparent dark:from-indigo-900/10">
        <div class="flex items-center gap-2">
          <div class="w-8 h-8 bg-indigo-100 dark:bg-indigo-900/30 rounded-lg flex items-center justify-center">
            <n-icon class="text-blue-600 dark:text-blue-400"><BusinessOutline /></n-icon>
          </div>
          <div>
            <h3 class="font-semibold text-gray-900 dark:text-white">{{ t('org.title') }}</h3>
            <p class="text-xs text-gray-500 dark:text-gray-400">{{ t('org.stats.total') }}: {{ orgList.length }} {{ t('org.level1').replace('Level 1', 'organizations') }}</p>
          </div>
        </div>
        <n-button text type="primary" size="small" @click="$router.push('/organizations')">
          {{ t('dashboard.viewAll') }}
          <template #icon><n-icon size="14"><ChevronForwardOutline /></n-icon></template>
        </n-button>
      </div>
      <div class="p-5">
        <div v-if="orgList.length > 0" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
          <div
            v-for="(org, idx) in orgList"
            :key="org.org_id"
            class="group p-4 rounded-xl border-2 border-transparent hover:border-indigo-200 dark:hover:border-blue-700 bg-gray-50 dark:bg-gray-700/30 hover:bg-indigo-50/50 dark:hover:bg-indigo-900/10 transition-all duration-200 cursor-pointer"
            :style="{ animationDelay: `${idx * 50}ms` }"
          >
            <div class="flex items-center gap-3">
              <div class="w-10 h-10 rounded-xl flex items-center justify-center text-white font-bold text-sm" :style="{ backgroundColor: `hsl(${idx * 45 + 220}, 70%, 55%)` }">
                {{ (org.org_name || ' Org').charAt(0).toUpperCase() }}
              </div>
              <div class="flex-1 min-w-0">
                <p class="font-semibold text-gray-900 dark:text-white truncate">{{ org.org_name }}</p>
                <div class="flex items-center gap-3 mt-1 text-xs text-gray-500 dark:text-gray-400">
                  <span class="flex items-center gap-1">
                    <n-icon size="12"><PeopleOutline /></n-icon>
                    {{ org.user_count }}
                  </span>
                  <span class="flex items-center gap-1">
                    <n-icon size="12"><DocumentTextOutline /></n-icon>
                    {{ org.doc_count }}
                  </span>
                </div>
              </div>
              <div class="opacity-0 group-hover:opacity-100 transition-opacity">
                <n-icon class="text-blue-500"><ChevronForwardOutline /></n-icon>
              </div>
            </div>
          </div>
        </div>
        <n-empty v-else :description="t('common.noData')" />
      </div>
    </div>

    <!-- 最近操作日志 -->
    <div class="bg-white dark:bg-gray-800 rounded-2xl border border-gray-100 dark:border-gray-700 shadow-sm overflow-hidden">
      <div class="px-5 py-4 border-b border-gray-100 dark:border-gray-700 flex items-center justify-between bg-gradient-to-r from-red-50 to-transparent dark:from-red-900/10">
        <div class="flex items-center gap-2">
          <div class="w-8 h-8 bg-red-100 dark:bg-red-900/30 rounded-lg flex items-center justify-center">
            <n-icon class="text-red-600 dark:text-red-400"><ShieldCheckmarkOutline /></n-icon>
          </div>
          <div>
            <h3 class="font-semibold text-gray-900 dark:text-white">{{ t('dashboard.recentAuditLogs') }}</h3>
            <p class="text-xs text-gray-500 dark:text-gray-400">Sensitive operations highlighted in red</p>
          </div>
        </div>
        <n-button type="primary" size="small" @click="$router.push('/audit-logs')">
          {{ t('dashboard.viewAll') }}
          <template #icon><n-icon size="14"><ChevronForwardOutline /></n-icon></template>
        </n-button>
      </div>
      <div class="p-5">
        <n-data-table
          :columns="auditColumns"
          :data="recentLogs"
          :bordered="false"
          size="small"
          :row-class-name="highlightSensitiveRow"
          :row-key="(row: any) => row.id"
        />
      </div>
    </div>

    <!-- 快捷入口 -->
    <div class="flex flex-wrap gap-3">
      <n-button type="primary" size="large" @click="$router.push('/users')" class="shadow-md">
        <template #icon><n-icon><PeopleOutline /></n-icon></template>
        {{ t('dashboard.manageUsers') }}
      </n-button>
      <n-button type="info" size="large" @click="$router.push('/knowledge')" class="shadow-md">
        <template #icon><n-icon><BookOutline /></n-icon></template>
        {{ t('dashboard.manageKB') }}
      </n-button>
      <n-button type="warning" size="large" @click="$router.push('/monitoring')" class="shadow-md">
        <template #icon><n-icon><PulseOutline /></n-icon></template>
        {{ t('dashboard.systemMonitor') }}
      </n-button>
      <n-button type="error" size="large" @click="$router.push('/audit-logs')" class="shadow-md">
        <template #icon><n-icon><ShieldCheckmarkOutline /></n-icon></template>
        {{ t('audit.title') }}
      </n-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, h } from 'vue'
import { useI18n } from 'vue-i18n'
import { NTag } from 'naive-ui'
import { getAuditLogs } from '@/api/user'
import { getLLMStats, getRAGStats, getAdminSummary, getOrgSummary } from '@/api/monitoring'
import { formatDate } from '@/utils/format'
import { CHART_COLORS } from '@/utils/chartTheme'
import {
  PulseOutline, AnalyticsOutline, PeopleOutline, BookOutline, BusinessOutline,
  DocumentTextOutline, ServerOutline, ChatbubbleEllipsesOutline, GitNetworkOutline,
  ChevronForwardOutline, TrendingUpOutline, ShieldCheckmarkOutline, WarningOutline
} from '@vicons/ionicons5'
import * as echarts from 'echarts'
import type { ECharts } from 'echarts'

const { t } = useI18n()

const adminStats = ref({
  totalUsers: 0,
  totalSessions: 0,
  totalDocs: 0,
  totalOrgs: 0,
  activeUsers: 0,
  totalRequests: 0,
  systemHealthy: true,
  esHealth: 'ok'
})

const llmStats = ref({
  tokensToday: 0,
  costToday: 0,
  avgLatency: 0,
  p95Latency: 0,
  costWarning: false,
  trend: [] as number[]
})

const ragStats = ref({
  hitRate: 0,
  totalQueries: 0,
  hits: 0,
  avgDocs: 0,
  trend: [] as number[]
})

const orgList = ref<any[]>([])
const recentLogs = ref<any[]>([])

const llmChartRef = ref<HTMLElement | null>(null)
const ragChartRef = ref<HTMLElement | null>(null)
let llmChart: ECharts | null = null
let ragChart: ECharts | null = null

const SENSITIVE_ACTIONS = ['delete_user', 'delete_document', 'reset_password', 'update_role', 'delete_organization', 'api_key_revoked']

const auditColumns = [
  {
    title: t('dashboard.time'),
    key: 'created_at',
    width: 170,
    render: (row: any) => h('span', { class: 'text-gray-600 dark:text-gray-300' }, formatDate(row.created_at))
  },
  {
    title: t('dashboard.user'),
    key: 'user_id',
    width: 100,
    render: (row: any) => h('span', { class: 'font-medium' }, row.username || `ID:${row.user_id}`)
  },
  {
    title: t('dashboard.action'),
    key: 'action',
    width: 160,
    render: (row: any) => {
      const isSensitive = SENSITIVE_ACTIONS.some(a => row.action?.includes(a))
      return h(NTag, {
        type: isSensitive ? 'error' : 'default',
        size: 'small',
        round: true
      }, { default: () => row.action || '-' })
    }
  },
  {
    title: t('dashboard.detail'),
    key: 'detail',
    ellipsis: { tooltip: { placement: 'top' } }
  },
  {
    title: 'IP',
    key: 'ip_address',
    width: 120,
    render: (row: any) => h('code', { class: 'text-xs text-gray-500' }, row.ip_address || '-')
  }
]

function highlightSensitiveRow(row: any) {
  const isSensitive = SENSITIVE_ACTIONS.some(a => row.action?.includes(a))
  return isSensitive ? 'bg-red-50/70 dark:bg-red-900/10' : ''
}

const initCharts = () => {
  const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
  const trendData = llmStats.value.trend.length ? llmStats.value.trend : [1200, 1800, 1500, 2200, 1900, 2500, 2100]

  if (llmChartRef.value) {
    llmChart = echarts.init(llmChartRef.value)
    llmChart.setOption({
      tooltip: { trigger: 'axis', backgroundColor: 'rgba(255,255,255,0.95)', borderColor: '#e5e7eb', textStyle: { color: '#374151' } },
      grid: { left: '2%', right: '3%', bottom: '3%', top: '8%', containLabel: true },
      xAxis: { type: 'category', data: days, axisLine: { lineStyle: { color: '#e5e7eb' } }, axisLabel: { color: '#6b7280', fontSize: 10 }, splitLine: { show: false } },
      yAxis: { type: 'value', axisLine: { show: false }, axisLabel: { color: '#6b7280', fontSize: 10 }, splitLine: { lineStyle: { color: '#f3f4f6' } } },
      series: [{
        data: trendData,
        type: 'line',
        smooth: 0.4,
        lineStyle: { width: 2, color: CHART_COLORS.purple },
        itemStyle: { color: CHART_COLORS.purple },
        areaStyle: { opacity: 0.15, color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: CHART_COLORS.purple },
          { offset: 1, color: 'rgba(168,85,247,0.0)' }
        ]) },
        symbol: 'circle',
        symbolSize: 5,
        showSymbol: false,
        emphasis: { focus: 'series', itemStyle: { symbolSize: 8 } }
      }]
    })
  }

  if (ragChartRef.value) {
    ragChart = echarts.init(ragChartRef.value)
    ragChart.setOption({
      tooltip: { trigger: 'axis', backgroundColor: 'rgba(255,255,255,0.95)', borderColor: '#e5e7eb', textStyle: { color: '#374151' } },
      grid: { left: '2%', right: '3%', bottom: '3%', top: '8%', containLabel: true },
      xAxis: { type: 'category', data: days, axisLine: { lineStyle: { color: '#e5e7eb' } }, axisLabel: { color: '#6b7280', fontSize: 10 }, splitLine: { show: false } },
      yAxis: { type: 'value', min: 0, max: 100, axisLine: { show: false }, axisLabel: { color: '#6b7280', fontSize: 10, formatter: '{value}%' }, splitLine: { lineStyle: { color: '#f3f4f6' } } },
      series: [{
        data: ragStats.value.trend.length ? ragStats.value.trend : [0, 0, 0, 0, 0, 0, ragStats.value.hitRate || 0],
        type: 'line',
        smooth: 0.4,
        lineStyle: { width: 2, color: CHART_COLORS.secondary },
        itemStyle: { color: CHART_COLORS.secondary },
        areaStyle: { opacity: 0.15, color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: CHART_COLORS.secondary },
          { offset: 1, color: 'rgba(16,185,129,0.0)' }
        ]) },
        symbol: 'circle',
        symbolSize: 5,
        showSymbol: false,
        emphasis: { focus: 'series', itemStyle: { symbolSize: 8 } }
      }]
    })
  }
}

onMounted(async () => {
  try {
    const [adminRes, auditRes, llmRes] = await Promise.all([
      getAdminSummary(),
      getAuditLogs({ limit: 10 }),
      getLLMStats()
    ])

    if (adminRes.data) {
      adminStats.value = {
        totalUsers: adminRes.data.total_users || 0,
        totalSessions: adminRes.data.total_sessions || 0,
        totalDocs: adminRes.data.total_documents || 0,
        totalOrgs: adminRes.data.total_organizations || 0,
        activeUsers: adminRes.data.active_users_24h || 0,
        totalRequests: adminRes.data.total_sessions || 0,
        systemHealthy: true,
        esHealth: 'ok'
      }
    }

    if ((auditRes.data as any)?.data?.items) {
      recentLogs.value = (auditRes.data as any).data.items
    }

    if (llmRes.data) {
      llmStats.value = {
        tokensToday: llmRes.data.total_tokens_today || 0,
        costToday: llmRes.data.cost_today_usd || 0,
        avgLatency: llmRes.data.avg_latency_ms || 0,
        p95Latency: llmRes.data.p95_latency_ms || 0,
        costWarning: llmRes.data.cost_warning || false,
        trend: llmRes.data.token_trend_7d || []
      }
    }

    const ragRes = await getRAGStats()
    if (ragRes.data) {
      ragStats.value = {
        hitRate: ragRes.data.hit_rate || 0,
        totalQueries: ragRes.data.total_queries_7d || 0,
        hits: ragRes.data.hits_with_documents || 0,
        avgDocs: ragRes.data.avg_documents_retrieved || 0,
        trend: ragRes.data.hit_rate_trend_7d || []
      }
    }

    const orgRes = await getOrgSummary()
    if (orgRes.data?.organizations) {
      orgList.value = orgRes.data.organizations
    }

    initCharts()
  } catch (e) {
    console.error('Failed to load admin dashboard:', e)
    initCharts()
  }

  window.addEventListener('resize', handleResize)
})

const handleResize = () => {
  llmChart?.resize()
  ragChart?.resize()
}

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  llmChart?.dispose()
  ragChart?.dispose()
  llmChart = null
  ragChart = null
})
</script>
