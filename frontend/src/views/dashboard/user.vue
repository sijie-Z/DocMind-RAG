<template>
  <div class="user-dashboard p-4 md:p-6 space-y-5">
    <!-- 欢迎区域 - 渐变背景 -->
    <div class="bg-gradient-to-r from-blue-600 via-blue-600 to-blue-700 rounded-2xl p-6 text-white shadow-xl shadow-blue-500/20 relative overflow-hidden">
      <div class="absolute inset-0 bg-[url('data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iNjAiIGhlaWdodD0iNjAiIHZpZXdCb3g9IjAgMCA2MCA2MCIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48ZyBmaWxsPSJub25lIiBmaWxsLXJ1bGU9ImV2ZW5vZGQiPjxnIGZpbGw9IiNmZmYiIGZpbGwtb3BhY2l0eT0iMC4xIj48Y2lyY2xlIGN4PSIzMCIgY3k9IjMwIiByPSIyIi8+PC9nPjwvZz48L3N2Zz4=')] opacity-50"></div>
      <div class="relative z-10">
        <h1 class="text-2xl md:text-3xl font-bold">{{ t('dashboard.welcome') }}, {{ userInfo?.nickname || userInfo?.username }}!</h1>
        <p class="text-slate-100 mt-1 text-sm md:text-base">{{ t('dashboard.welcomeDesc') }}</p>
      </div>
    </div>

    <!-- 统计卡片 - 4列 -->
    <div class="grid grid-cols-2 lg:grid-cols-4 gap-4">
      <div class="group bg-white dark:bg-gray-800 rounded-2xl p-5 border border-gray-100 dark:border-gray-700 shadow-sm hover:shadow-md hover:border-slate-200 dark:hover:border-blue-700 transition-all duration-200 cursor-pointer">
        <div class="flex items-center justify-between mb-3">
          <div class="w-10 h-10 bg-slate-100 dark:bg-slate-900/40 rounded-xl flex items-center justify-center group-hover:scale-110 transition-transform">
            <n-icon size="22" class="text-blue-600 dark:text-blue-400"><ChatbubbleEllipsesOutline /></n-icon>
          </div>
          <span class="text-xs text-gray-400 group-hover:text-blue-500 transition-colors">对话</span>
        </div>
        <p class="text-3xl font-bold text-gray-900 dark:text-white">{{ stats.conversation_count.toLocaleString() }}</p>
        <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">{{ t('dashboard.conversations') }}</p>
      </div>

      <div class="group bg-white dark:bg-gray-800 rounded-2xl p-5 border border-gray-100 dark:border-gray-700 shadow-sm hover:shadow-md hover:border-emerald-200 dark:hover:border-emerald-700 transition-all duration-200 cursor-pointer">
        <div class="flex items-center justify-between mb-3">
          <div class="w-10 h-10 bg-emerald-100 dark:bg-emerald-900/40 rounded-xl flex items-center justify-center group-hover:scale-110 transition-transform">
            <n-icon size="22" class="text-emerald-600 dark:text-emerald-400"><TextOutline /></n-icon>
          </div>
          <span class="text-xs text-gray-400 group-hover:text-emerald-500 transition-colors">消息</span>
        </div>
        <p class="text-3xl font-bold text-gray-900 dark:text-white">{{ stats.message_count.toLocaleString() }}</p>
        <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">{{ t('dashboard.messages') }}</p>
      </div>

      <div class="group bg-white dark:bg-gray-800 rounded-2xl p-5 border border-gray-100 dark:border-gray-700 shadow-sm hover:shadow-md hover:border-orange-200 dark:hover:border-orange-700 transition-all duration-200 cursor-pointer">
        <div class="flex items-center justify-between mb-3">
          <div class="w-10 h-10 bg-orange-100 dark:bg-orange-900/40 rounded-xl flex items-center justify-center group-hover:scale-110 transition-transform">
            <n-icon size="22" class="text-orange-600 dark:text-orange-400"><DocumentOutline /></n-icon>
          </div>
          <span class="text-xs text-gray-400 group-hover:text-orange-500 transition-colors">文档</span>
        </div>
        <p class="text-3xl font-bold text-gray-900 dark:text-white">{{ stats.file_count.toLocaleString() }}</p>
        <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">{{ t('dashboard.files') }}</p>
      </div>

      <div class="group bg-white dark:bg-gray-800 rounded-2xl p-5 border border-gray-100 dark:border-gray-700 shadow-sm hover:shadow-md hover:border-rose-200 dark:hover:border-rose-700 transition-all duration-200 cursor-pointer">
        <div class="flex items-center justify-between mb-3">
          <div class="w-10 h-10 bg-rose-100 dark:bg-rose-900/40 rounded-xl flex items-center justify-center group-hover:scale-110 transition-transform">
            <n-icon size="22" class="text-rose-600 dark:text-rose-400"><ServerOutline /></n-icon>
          </div>
          <span class="text-xs text-gray-400 group-hover:text-rose-500 transition-colors">存储</span>
        </div>
        <p class="text-3xl font-bold text-gray-900 dark:text-white">{{ formatSize(stats.storage_used) }}</p>
        <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">{{ t('dashboard.storage') }}</p>
      </div>
    </div>

    <!-- RAG 命中率模块 -->
    <div class="bg-white dark:bg-gray-800 rounded-2xl border border-gray-100 dark:border-gray-700 shadow-sm overflow-hidden">
      <div class="px-5 py-4 border-b border-gray-100 dark:border-gray-700 flex items-center justify-between bg-gradient-to-r from-emerald-50 to-transparent dark:from-emerald-900/10">
        <div class="flex items-center gap-2">
          <div class="w-8 h-8 bg-emerald-100 dark:bg-emerald-900/30 rounded-lg flex items-center justify-center">
            <n-icon class="text-emerald-600 dark:text-emerald-400"><AnalyticsOutline /></n-icon>
          </div>
          <div>
            <h3 class="font-semibold text-gray-900 dark:text-white">{{ t('dashboard.ragStats') }}</h3>
            <p class="text-xs text-gray-500 dark:text-gray-400">近 7 天知识检索质量</p>
          </div>
        </div>
        <div class="flex items-center gap-2">
          <n-tag :type="ragStats.hitRate > 60 ? 'success' : ragStats.hitRate > 30 ? 'warning' : 'error'" size="small" round>
            {{ ragStats.hitRate > 60 ? '良好' : ragStats.hitRate > 30 ? '一般' : '需关注' }}
          </n-tag>
        </div>
      </div>
      <div class="p-5">
        <div class="flex items-center gap-5 mb-4">
          <div class="flex-1">
            <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">{{ t('dashboard.hitRate') }}</p>
            <n-progress
              type="line"
              :percentage="ragStats.hitRate"
              :indicator-placement="'inside'"
              :height="18"
              :border-radius="9"
              :fill-border-radius="9"
              :color="ragStats.hitRate > 60 ? CHART_COLORS.secondary : ragStats.hitRate > 30 ? CHART_COLORS.warning : CHART_COLORS.danger"
              :rail-color="'#f3f4f6'"
            />
          </div>
          <div class="text-right min-w-[80px]">
            <p class="text-3xl font-bold text-emerald-600 dark:text-emerald-400">{{ ragStats.hitRate }}<span class="text-lg">%</span></p>
          </div>
        </div>
        <div class="grid grid-cols-3 gap-3">
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
      </div>
    </div>

    <!-- 两列布局：最近会话 + 活动趋势 -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-5">
      <!-- 最近会话 -->
      <div class="bg-white dark:bg-gray-800 rounded-2xl border border-gray-100 dark:border-gray-700 shadow-sm overflow-hidden">
        <div class="px-5 py-4 border-b border-gray-100 dark:border-gray-700 flex items-center justify-between">
          <div class="flex items-center gap-2">
            <div class="w-8 h-8 bg-slate-100 dark:bg-slate-900/30 rounded-lg flex items-center justify-center">
              <n-icon class="text-blue-600 dark:text-blue-400"><TimeOutline /></n-icon>
            </div>
            <div>
              <h3 class="font-semibold text-gray-900 dark:text-white">{{ t('dashboard.recentConversations') }}</h3>
              <p class="text-xs text-gray-500 dark:text-gray-400">最近 {{ recentConversations.length }} 个会话</p>
            </div>
          </div>
          <n-button text type="primary" size="small" @click="$router.push('/conversations')">
            {{ t('dashboard.viewAll') }}
            <template #icon><n-icon size="14"><ChevronForwardOutline /></n-icon></template>
          </n-button>
        </div>
        <div class="p-4">
          <div v-if="recentConversations.length === 0" class="text-center py-10">
            <div class="w-16 h-16 bg-gray-100 dark:bg-gray-700 rounded-full flex items-center justify-center mx-auto mb-3">
              <n-icon size="32" class="text-gray-400"><ChatbubbleEllipsesOutline /></n-icon>
            </div>
            <p class="text-gray-400 dark:text-gray-500">{{ t('dashboard.noConversations') }}</p>
            <n-button type="primary" size="small" class="mt-3" @click="$router.push('/chat')">
              {{ t('dashboard.newChat') }}
            </n-button>
          </div>
          <div v-else class="space-y-2">
            <div
              v-for="conv in recentConversations"
              :key="conv.id"
              class="flex items-center justify-between p-3 rounded-xl hover:bg-gray-50 dark:hover:bg-gray-700/50 cursor-pointer transition-all duration-150 group"
              @click="$router.push(`/chat?conversation=${conv.id}`)"
            >
              <div class="flex-1 min-w-0 flex items-center gap-3">
                <div class="w-8 h-8 bg-slate-100 dark:bg-slate-900/30 rounded-lg flex items-center justify-center flex-shrink-0 group-hover:bg-slate-200 dark:group-hover:bg-slate-800 transition-colors">
                  <n-icon size="16" class="text-blue-600 dark:text-blue-400"><ChatbubbleEllipsesOutline /></n-icon>
                </div>
                <div class="min-w-0">
                  <p class="font-medium text-gray-900 dark:text-white truncate">{{ conv.title || t('dashboard.untitled') }}</p>
                  <p class="text-sm text-gray-500 dark:text-gray-400 truncate">{{ conv.last_message || '' }}</p>
                </div>
              </div>
              <span class="text-xs text-gray-400 ml-2 flex-shrink-0">{{ formatDate(conv.updated_at) }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 活动趋势 -->
      <div class="bg-white dark:bg-gray-800 rounded-2xl border border-gray-100 dark:border-gray-700 shadow-sm overflow-hidden">
        <div class="px-5 py-4 border-b border-gray-100 dark:border-gray-700">
          <div class="flex items-center gap-2">
            <div class="w-8 h-8 bg-slate-100 dark:bg-slate-900/30 rounded-lg flex items-center justify-center">
              <n-icon class="text-blue-600 dark:text-blue-400"><TrendingUpOutline /></n-icon>
            </div>
            <div>
              <h3 class="font-semibold text-gray-900 dark:text-white">{{ t('dashboard.activityTrend') }}</h3>
              <p class="text-xs text-gray-500 dark:text-gray-400">7天活动趋势图</p>
            </div>
          </div>
        </div>
        <div class="p-5 h-64">
          <div ref="chartRef" class="w-full h-full"></div>
        </div>
      </div>
    </div>

    <!-- 新手引导 (空状态时显示) -->
    <div v-if="stats.file_count === 0" class="bg-gradient-to-br from-blue-50 to-slate-50 dark:from-blue-900/20 dark:to-slate-900/20 rounded-2xl border border-blue-200 dark:border-blue-800/50 p-6 relative overflow-hidden">
      <div class="absolute top-0 right-0 w-32 h-32 bg-blue-200/30 dark:bg-blue-700/10 rounded-full -translate-y-1/2 translate-x-1/2"></div>
      <div class="relative z-10">
        <h3 class="text-lg font-bold text-blue-900 dark:text-blue-100 mb-2 flex items-center gap-2">
          <n-icon class="text-blue-500"><RocketOutline /></n-icon>
          开始使用 DocMind
        </h3>
        <p class="text-sm text-blue-700 dark:text-blue-300 mb-5">只需 3 步，即可体验 AI 知识库的强大功能</p>
        <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-5">
          <div class="flex items-start gap-3 bg-white/60 dark:bg-gray-800/60 rounded-xl p-4">
            <div class="w-8 h-8 rounded-full bg-blue-500 text-white flex items-center justify-center text-sm font-bold flex-shrink-0">1</div>
            <div>
              <div class="font-semibold text-sm text-gray-900 dark:text-white">上传文档</div>
              <div class="text-xs text-gray-500 dark:text-gray-400 mt-1">支持 PDF、Word、TXT 等格式，系统自动解析并建立索引</div>
            </div>
          </div>
          <div class="flex items-start gap-3 bg-white/60 dark:bg-gray-800/60 rounded-xl p-4">
            <div class="w-8 h-8 rounded-full bg-blue-500 text-white flex items-center justify-center text-sm font-bold flex-shrink-0">2</div>
            <div>
              <div class="font-semibold text-sm text-gray-900 dark:text-white">智能搜索</div>
              <div class="text-xs text-gray-500 dark:text-gray-400 mt-1">混合检索（关键词 + 语义），自动引用来源文档</div>
            </div>
          </div>
          <div class="flex items-start gap-3 bg-white/60 dark:bg-gray-800/60 rounded-xl p-4">
            <div class="w-8 h-8 rounded-full bg-blue-500 text-white flex items-center justify-center text-sm font-bold flex-shrink-0">3</div>
            <div>
              <div class="font-semibold text-sm text-gray-900 dark:text-white">AI 对话</div>
              <div class="text-xs text-gray-500 dark:text-gray-400 mt-1">基于文档内容回答，附带 [n] 引用标注，支持多轮对话</div>
            </div>
          </div>
        </div>
        <div class="flex flex-wrap gap-3">
          <n-button type="primary" @click="$router.push('/knowledge')" class="shadow-lg shadow-blue-500/25">
            <template #icon><n-icon><CloudUploadOutline /></n-icon></template>
            上传第一个文档
          </n-button>
          <n-button secondary @click="loadDemoData" :loading="demoLoading">
            <template #icon><n-icon><SparklesOutline /></n-icon></template>
            加载示例数据
          </n-button>
        </div>
      </div>
    </div>

    <!-- 快捷操作 -->
    <div class="bg-white dark:bg-gray-800 rounded-2xl border border-gray-100 dark:border-gray-700 shadow-sm p-5">
      <h3 class="font-semibold text-gray-900 dark:text-white mb-4 flex items-center gap-2">
        <n-icon class="text-blue-500"><FlashOutline /></n-icon>
        {{ t('dashboard.quickActions') }}
      </h3>
      <div class="flex flex-wrap gap-3">
        <n-button type="primary" size="large" class="shadow-md hover:shadow-lg transition-shadow" @click="$router.push('/chat')">
          <template #icon><n-icon><ChatbubbleEllipsesOutline /></n-icon></template>
          {{ t('dashboard.newChat') }}
        </n-button>
        <n-button type="info" size="large" class="shadow-md hover:shadow-lg transition-shadow" @click="$router.push('/knowledge')">
          <template #icon><n-icon><CloudUploadOutline /></n-icon></template>
          {{ t('dashboard.uploadDoc') }}
        </n-button>
        <n-button type="success" size="large" class="shadow-md hover:shadow-lg transition-shadow" @click="$router.push('/search')">
          <template #icon><n-icon><SearchOutline /></n-icon></template>
          {{ t('dashboard.searchKB') }}
        </n-button>
        <n-button type="warning" size="large" class="shadow-md hover:shadow-lg transition-shadow" @click="$router.push('/agent')">
          <template #icon><n-icon><HardwareChipOutline /></n-icon></template>
          Agent 模式
        </n-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useMessage } from 'naive-ui'
import { useUserStore } from '@/stores/user'
import { getUserStats } from '@/api/user'
import { getConversations, type Conversation } from '@/api/conversation'
import { getRAGStats } from '@/api/monitoring'
import { seedDemoData } from '@/api/demo'
import { formatFileSize, formatDate as formatDateUtil } from '@/utils/format'
import { CHART_COLORS } from '@/utils/chartTheme'
import {
  ChatbubbleEllipsesOutline, TextOutline, DocumentOutline,
  ServerOutline, CloudUploadOutline, SearchOutline, TimeOutline,
  ChevronForwardOutline, AnalyticsOutline, TrendingUpOutline, FlashOutline,
  HardwareChipOutline, RocketOutline, SparklesOutline
} from '@vicons/ionicons5'
import * as echarts from 'echarts'
import type { ECharts } from 'echarts'

const { t } = useI18n()
const message = useMessage()
const userStore = useUserStore()
const userInfo = computed(() => userStore.userInfo)

const stats = ref({
  conversation_count: 0,
  message_count: 0,
  file_count: 0,
  knowledge_count: 0,
  storage_used: 0,
  activity_trend: [0, 0, 0, 0, 0, 0, 0]
})

const ragStats = ref({
  hitRate: 0,
  totalQueries: 0,
  hits: 0,
  avgDocs: 0
})

const recentConversations = ref<Conversation[]>([])
const chartRef = ref<HTMLElement | null>(null)
const demoLoading = ref(false)
let chart: ECharts | null = null

const formatSize = (bytes: number) => formatFileSize(bytes)
const formatDate = (date: string) => formatDateUtil(date)

const loadDemoData = async () => {
  demoLoading.value = true
  try {
    const res = await seedDemoData()
    if (res.data?.success) {
      message.success('示例数据加载成功！页面即将刷新')
      // 刷新页面数据
      setTimeout(() => window.location.reload(), 1000)
    } else {
      message.info(res.data?.message || '示例数据已存在')
    }
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } }
    message.error(err?.response?.data?.detail || '加载示例数据失败')
  } finally {
    demoLoading.value = false
  }
}

const initChart = () => {
  if (!chartRef.value) return
  chart = echarts.init(chartRef.value)
  updateChart()
}

const updateChart = () => {
  if (!chart) return
  const option = {
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(255,255,255,0.95)',
      borderColor: '#e5e7eb',
      borderWidth: 1,
      textStyle: { color: '#374151' }
    },
    grid: { left: '2%', right: '3%', bottom: '3%', top: '10%', containLabel: true },
    xAxis: {
      type: 'category',
      data: ['周一', '周二', '周三', '周四', '周五', '周六', '周日'],
      axisLine: { lineStyle: { color: '#e5e7eb' } },
      axisLabel: { color: '#6b7280', fontSize: 10 },
      splitLine: { show: false }
    },
    yAxis: {
      type: 'value',
      axisLine: { show: false },
      axisLabel: { color: '#6b7280', fontSize: 10 },
      splitLine: { lineStyle: { color: '#f3f4f6' } }
    },
    series: [{
      data: stats.value.activity_trend,
      type: 'bar',
      barWidth: '60%',
      itemStyle: {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: CHART_COLORS.primary },
          { offset: 1, color: CHART_COLORS.sky }
        ]),
        borderRadius: [4, 4, 0, 0]
      },
      emphasis: {
        itemStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: CHART_COLORS.primary },
            { offset: 1, color: CHART_COLORS.sky }
          ])
        }
      }
    }]
  }
  chart.setOption(option)
}

const handleResize = () => chart?.resize()

watch(() => stats.value.activity_trend, updateChart, { deep: true })

onMounted(async () => {
  try {
    const [statsRes, convRes, ragRes] = await Promise.all([
      getUserStats(),
      getConversations(),
      getRAGStats()
    ])

    if (statsRes.data) {
      stats.value = {
        conversation_count: statsRes.data.conversation_count || 0,
        message_count: statsRes.data.message_count || 0,
        file_count: statsRes.data.file_count || 0,
        knowledge_count: statsRes.data.knowledge_count || 0,
        storage_used: statsRes.data.storage_used || 0,
        activity_trend: statsRes.data.activity_trend || [0, 0, 0, 0, 0, 0, 0]
      }
    }

    const convData = convRes.data as unknown as Record<string, unknown>
    const convInner = convData?.data as Record<string, unknown> | undefined
    if (convInner?.data && Array.isArray(convInner.data)) {
      recentConversations.value = (convInner.data as Conversation[]).slice(0, 5)
    }

    if (ragRes.data) {
      ragStats.value = {
        hitRate: ragRes.data.hit_rate || 0,
        totalQueries: ragRes.data.total_queries_7d || 0,
        hits: ragRes.data.hits_with_documents || 0,
        avgDocs: ragRes.data.avg_documents_retrieved || 0
      }
    }

    initChart()
  } catch (e) {
    initChart()
  }

  window.addEventListener('resize', handleResize)
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  chart?.dispose()
  chart = null
})
</script>