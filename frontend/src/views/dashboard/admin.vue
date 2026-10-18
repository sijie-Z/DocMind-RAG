<template>
  <div class="admin-dashboard p-4 md:p-6 space-y-5">
    <!-- 分块加载：KPI 卡 / 图表 / 表格各自骨架；哪块数据先到就先替换哪块，互不阻塞 -->
    <!-- 顶部统计卡片 - 渐变背景（摘要数据未到 → 整排骨架） -->
    <div v-if="summaryState === 'loading'" class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3" data-testid="kpi-skeleton">
      <div v-for="i in 6" :key="i" class="bg-gray-100 dark:bg-gray-800 rounded-xl p-4 min-h-[108px]">
        <n-skeleton text style="width: 55%" />
        <n-skeleton text style="width: 75%; margin-top: 16px" />
        <n-skeleton text style="width: 45%; margin-top: 8px" />
      </div>
    </div>
    <div
      v-else-if="summaryState === 'error'"
      class="flex flex-col items-center justify-center gap-3 py-10 bg-white dark:bg-gray-800 rounded-xl border border-gray-100 dark:border-gray-700"
      data-testid="kpi-error"
    >
      <span class="text-sm text-gray-500 dark:text-gray-400">{{ t('dashboard.loadFailed') }}</span>
      <n-button size="small" @click="loadSummary">{{ t('dashboard.retry') }}</n-button>
    </div>
    <div v-else class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
      <div class="bg-gradient-to-br from-blue-500 to-blue-600 rounded-xl p-4 text-white shadow-lg shadow-blue-500/20">
        <div class="flex items-center justify-between mb-2">
          <span class="text-xs font-medium text-slate-100">{{ t('dashboard.totalUsers') }}</span>
          <div class="w-8 h-8 bg-white/20 rounded-lg flex items-center justify-center">
            <n-icon size="18" class="text-white"><PeopleOutline /></n-icon>
          </div>
        </div>
        <p class="text-2xl font-bold">{{ adminStats.totalUsers.toLocaleString() }}</p>
        <div class="flex items-center gap-1 mt-1 text-xs text-slate-200">
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
          <!-- W2-T7：此前是写死的「↑ +12%（对比上周）」（§八实机实锤假趋势）；
               改为后端 sessions_this_week/last_week 真环比，无基线（上周 0/缺）如实说 -->
          <template v-if="sessionTrend !== null">
            <span>{{ sessionTrend >= 0 ? '↑' : '↓' }} {{ sessionTrend >= 0 ? '+' : '' }}{{ sessionTrend }}%</span>
            <span>对比上周</span>
          </template>
          <span v-else>{{ t('dashboard.lastWeekNoData') }}</span>
        </div>
      </div>

      <div class="bg-gradient-to-br from-blue-500 to-blue-600 rounded-xl p-4 text-white shadow-lg shadow-blue-500/20">
        <div class="flex items-center justify-between mb-2">
          <span class="text-xs font-medium text-slate-100">{{ t('dashboard.totalDocs') }}</span>
          <div class="w-8 h-8 bg-white/20 rounded-lg flex items-center justify-center">
            <n-icon size="18" class="text-white"><DocumentTextOutline /></n-icon>
          </div>
        </div>
        <p class="text-2xl font-bold">{{ adminStats.totalDocs.toLocaleString() }}</p>
        <div class="flex items-center gap-1 mt-1 text-xs text-slate-200">
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
          <span>24小时活跃</span>
        </div>
      </div>

      <div class="bg-gradient-to-br from-cyan-500 to-cyan-600 rounded-xl p-4 text-white shadow-lg shadow-cyan-500/20">
        <div class="flex items-center justify-between mb-2">
          <span class="text-xs font-medium text-cyan-100">{{ t('dashboard.todayRequests') }}</span>
          <div class="w-8 h-8 bg-white/20 rounded-lg flex items-center justify-center">
            <n-icon size="18" class="text-white"><GitNetworkOutline /></n-icon>
          </div>
        </div>
        <p class="text-2xl font-bold">
          <n-skeleton v-if="llmState === 'loading'" text style="width: 64px" />
          <template v-else>{{ llmStats.requestCountToday.toLocaleString() }}</template>
        </p>
        <div class="flex items-center gap-1 mt-1 text-xs text-cyan-200">
          <n-skeleton v-if="llmState === 'loading'" text style="width: 80px" />
          <span v-else>平均 {{ llmStats.avgLatency || 0 }}ms</span>
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
          <n-skeleton v-if="healthState === 'loading'" text style="width: 48px" />
          <n-tag v-else :type="healthState === 'ready' ? (systemHealthy ? 'success' : 'error') : 'default'" size="small" round>
            {{ healthState === 'ready' ? (systemHealthy ? t('dashboard.healthy') : t('dashboard.unhealthy')) : '—' }}
          </n-tag>
        </div>
        <div class="flex items-center gap-1 mt-2 text-xs text-gray-300">
          <span>{{ t('dashboard.esLabel') }}: {{ healthState === 'ready' ? esHealthText : '—' }}</span>
          <n-button v-if="healthState === 'error'" text size="tiny" class="text-gray-200" @click="loadHealth">
            {{ t('dashboard.retry') }}
          </n-button>
        </div>
      </div>
    </div>

    <!-- 两列布局：AI 监控 + RAG 性能 -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-5">
      <!-- AI 资源监控 -->
      <div class="bg-white dark:bg-gray-800 rounded-2xl border border-gray-100 dark:border-gray-700 shadow-sm overflow-hidden">
        <div class="px-5 py-4 border-b border-gray-100 dark:border-gray-700 flex items-center justify-between bg-gradient-to-r from-slate-50 to-transparent dark:from-slate-900/10">
          <div class="flex items-center gap-2">
            <div class="w-8 h-8 bg-slate-100 dark:bg-slate-900/30 rounded-lg flex items-center justify-center">
              <n-icon class="text-blue-600 dark:text-blue-400"><PulseOutline /></n-icon>
            </div>
            <div>
              <h3 class="font-semibold text-gray-900 dark:text-white">{{ t('dashboard.aiMonitor') }}</h3>
              <p class="text-xs text-gray-500 dark:text-gray-400">LLM 资源与成本</p>
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
        <div v-if="llmState === 'loading'" class="p-5" data-testid="llm-skeleton">
          <div class="grid grid-cols-2 gap-4 mb-5">
            <div v-for="i in 4" :key="i" class="bg-gray-50 dark:bg-gray-700/50 rounded-xl p-4">
              <n-skeleton text style="width: 55%" size="small" />
              <n-skeleton text style="width: 70%; margin-top: 10px" />
            </div>
          </div>
          <n-skeleton height="176px" />
        </div>
        <div v-else-if="llmState === 'error'" class="p-5" data-testid="llm-error">
          <div class="flex flex-col items-center justify-center gap-3 py-10">
            <span class="text-sm text-gray-500 dark:text-gray-400">{{ t('dashboard.loadFailed') }}</span>
            <n-button size="small" @click="loadLLM">{{ t('dashboard.retry') }}</n-button>
          </div>
        </div>
        <div v-else class="p-5">
          <!-- 统计数据 -->
          <div class="grid grid-cols-2 gap-4 mb-5">
            <div class="bg-gray-50 dark:bg-gray-700/50 rounded-xl p-4">
              <div class="flex items-center gap-2 mb-1">
                <div class="w-2 h-2 rounded-full bg-blue-500"></div>
                <span class="text-xs text-gray-500 dark:text-gray-400">{{ t('dashboard.tokensToday') }}</span>
              </div>
              <p class="text-xl font-bold text-gray-900 dark:text-white">{{ llmStats.tokensToday.toLocaleString() }}</p>
              <p class="text-xs text-gray-400 mt-1">输入 + 输出</p>
            </div>
            <div class="bg-gray-50 dark:bg-gray-700/50 rounded-xl p-4">
              <div class="flex items-center gap-2 mb-1">
                <div class="w-2 h-2 rounded-full bg-emerald-500"></div>
                <span class="text-xs text-gray-500 dark:text-gray-400">{{ t('dashboard.costToday') }}</span>
              </div>
              <p class="text-xl font-bold text-blue-600 dark:text-blue-400">${{ llmStats.costToday.toFixed(3) }}</p>
              <!-- W2-T7：实际推理栈是 DeepSeek（settings.DEEPSEEK_MODEL），后端成本
                   已按 token_usage 真源费率（deepseek $0.0005/$0.002 per 1K）计算；
                   旧标签「GPT-4o-mini 费率」与实际供应商不符 -->
              <p class="text-xs text-gray-400 mt-1">DeepSeek 费率</p>
            </div>
            <div class="bg-gray-50 dark:bg-gray-700/50 rounded-xl p-4">
              <div class="flex items-center gap-2 mb-1">
                <div class="w-2 h-2 rounded-full bg-blue-500"></div>
                <span class="text-xs text-gray-500 dark:text-gray-400">{{ t('dashboard.avgLatency') }}</span>
              </div>
              <p class="text-xl font-bold text-gray-900 dark:text-white">{{ llmStats.avgLatency }}<span class="text-sm font-normal text-gray-400">ms</span></p>
              <p class="text-xs text-gray-400 mt-1">p50 响应时间</p>
            </div>
            <div class="bg-gray-50 dark:bg-gray-700/50 rounded-xl p-4">
              <div class="flex items-center gap-2 mb-1">
                <div class="w-2 h-2 rounded-full bg-orange-500"></div>
                <span class="text-xs text-gray-500 dark:text-gray-400">{{ t('dashboard.p95Latency') }}</span>
              </div>
              <p class="text-xl font-bold text-gray-900 dark:text-white">{{ llmStats.p95Latency }}<span class="text-sm font-normal text-gray-400">ms</span></p>
              <p class="text-xs text-gray-400 mt-1">p95 响应时间</p>
            </div>
          </div>
          <!-- 趋势图 -->
          <div class="h-44 rounded-xl bg-gray-50 dark:bg-gray-700/30 p-3">
            <div ref="llmChartRef" class="w-full h-full"></div>
          </div>
        </div>
      </div>

      <!-- RAG 命中率 -->
      <div class="bg-white dark:bg-gray-800 rounded-2xl border border-gray-100 dark:border-gray-700 shadow-sm overflow-hidden flex flex-col" data-testid="rag-card">
        <div class="px-5 py-4 border-b border-gray-100 dark:border-gray-700 flex items-center justify-between bg-gradient-to-r from-emerald-50 to-transparent dark:from-emerald-900/10">
          <div class="flex items-center gap-2">
            <div class="w-8 h-8 bg-emerald-100 dark:bg-emerald-900/30 rounded-lg flex items-center justify-center">
              <n-icon class="text-emerald-600 dark:text-emerald-400"><AnalyticsOutline /></n-icon>
            </div>
            <div>
              <h3 class="font-semibold text-gray-900 dark:text-white">{{ t('dashboard.ragStats') }}</h3>
              <p class="text-xs text-gray-500 dark:text-gray-400">近 24 小时文档检索效果（3 小时桶）</p>
            </div>
          </div>
          <div class="flex items-center gap-2">
            <n-tag :type="ragStats.hitRate > 60 ? 'success' : ragStats.hitRate > 30 ? 'warning' : 'error'" size="small" round>
              {{ ragStats.hitRate > 60 ? '良好' : ragStats.hitRate > 30 ? '一般' : '较差' }}
            </n-tag>
          </div>
        </div>
        <div v-if="ragState === 'loading'" class="p-5" data-testid="rag-skeleton">
          <div class="flex items-center gap-4 mb-5">
            <div class="flex-1">
              <n-skeleton text style="width: 30%" size="small" />
              <n-skeleton height="20px" style="margin-top: 12px" />
            </div>
            <n-skeleton text style="width: 64px" />
          </div>
          <div class="grid grid-cols-3 gap-3 mb-5">
            <div v-for="i in 3" :key="i" class="p-3 bg-gray-50 dark:bg-gray-700/50 rounded-xl">
              <n-skeleton text style="width: 70%" size="small" />
              <n-skeleton text style="width: 50%; margin-top: 8px" />
            </div>
          </div>
          <n-skeleton height="112px" />
        </div>
        <div v-else-if="ragState === 'error'" class="p-5" data-testid="rag-error">
          <div class="flex flex-col items-center justify-center gap-3 py-10">
            <span class="text-sm text-gray-500 dark:text-gray-400">{{ t('dashboard.loadFailed') }}</span>
            <n-button size="small" data-testid="rag-retry" @click="loadRAG">{{ t('dashboard.retry') }}</n-button>
          </div>
        </div>
        <div v-else class="p-5 flex-1 flex flex-col">
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
          <!-- 趋势图（flex-1：与左卡等高时填充剩余空间，消除卡底空白） -->
          <div class="flex-1 min-h-28 rounded-xl bg-gray-50 dark:bg-gray-700/30 p-3" data-testid="rag-chart">
            <div ref="ragChartRef" class="w-full h-full"></div>
          </div>
        </div>
      </div>
    </div>

    <!-- 组织概览 -->
    <div class="bg-white dark:bg-gray-800 rounded-2xl border border-gray-100 dark:border-gray-700 shadow-sm overflow-hidden">
      <div class="px-5 py-4 border-b border-gray-100 dark:border-gray-700 flex items-center justify-between bg-gradient-to-r from-slate-50 to-transparent dark:from-slate-900/10">
        <div class="flex items-center gap-2">
          <div class="w-8 h-8 bg-slate-100 dark:bg-slate-900/30 rounded-lg flex items-center justify-center">
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
        <div v-if="orgState === 'loading'" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3" data-testid="org-skeleton">
          <div v-for="i in 4" :key="i" class="p-4 rounded-xl bg-gray-50 dark:bg-gray-700/30">
            <div class="flex items-center gap-3">
              <n-skeleton circle width="40px" height="40px" />
              <div class="flex-1 min-w-0">
                <n-skeleton text style="width: 70%" />
                <n-skeleton text style="width: 45%; margin-top: 6px" size="small" />
              </div>
            </div>
          </div>
        </div>
        <div v-else-if="orgState === 'error'" class="flex flex-col items-center justify-center gap-3 py-10" data-testid="org-error">
          <span class="text-sm text-gray-500 dark:text-gray-400">{{ t('dashboard.loadFailed') }}</span>
          <n-button size="small" @click="loadOrg">{{ t('dashboard.retry') }}</n-button>
        </div>
        <template v-else>
        <div v-if="orgList.length > 0" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
          <div
            v-for="(org, idx) in orgList"
            :key="org.org_id"
            class="group p-4 rounded-xl border-2 border-transparent hover:border-slate-200 dark:hover:border-blue-700 bg-gray-50 dark:bg-gray-700/30 hover:bg-slate-50/50 dark:hover:bg-slate-900/10 transition-all duration-200 cursor-pointer"
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
        </template>
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
            <p class="text-xs text-gray-500 dark:text-gray-400">敏感操作以红色标注</p>
          </div>
        </div>
        <n-button type="primary" size="small" @click="$router.push('/audit-logs')">
          {{ t('dashboard.viewAll') }}
          <template #icon><n-icon size="14"><ChevronForwardOutline /></n-icon></template>
        </n-button>
      </div>
      <div class="p-5">
        <div v-if="auditState === 'loading'" class="space-y-3" data-testid="audit-skeleton">
          <n-skeleton v-for="i in 6" :key="i" text />
        </div>
        <div v-else-if="auditState === 'error'" class="flex flex-col items-center justify-center gap-3 py-10" data-testid="audit-error">
          <span class="text-sm text-gray-500 dark:text-gray-400">{{ t('dashboard.loadFailed') }}</span>
          <n-button size="small" @click="loadAudit">{{ t('dashboard.retry') }}</n-button>
        </div>
        <n-data-table
          v-else
          :columns="auditColumns"
          :data="recentLogs"
          :bordered="false"
          size="small"
          :row-class-name="highlightSensitiveRow"
          :row-key="(row: AuditLogEntry) => row.id"
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
        {{ t('menu.auditLogs') }}
      </n-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, h, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import { NTag } from 'naive-ui'
import { getAuditLogs, type AuditLogEntry } from '@/api/user'
import { getLLMStats, getRAGStats, getAdminSummary, getOrgSummary, getMonitoringHealth, type OrgSummary } from '@/api/monitoring'
import { FIRST_SCREEN_REQUEST_CONFIG } from '@/utils/request'
import { formatDate } from '@/utils/format'
import { CHART_COLORS } from '@/utils/chartTheme'
import {
  PulseOutline, AnalyticsOutline, PeopleOutline, BookOutline, BusinessOutline,
  DocumentTextOutline, ServerOutline, ChatbubbleEllipsesOutline, GitNetworkOutline,
  ChevronForwardOutline, TrendingUpOutline, ShieldCheckmarkOutline, WarningOutline
} from '@vicons/ionicons5'
import echarts from '@/utils/echarts'
import type { EChartsType } from 'echarts/core'

const { t } = useI18n()

/**
 * 分块加载状态：'loading' 骨架 / 'ready' 内容 / 'error' 区块错误态 + 重试。
 * 每个区块只依赖自己那路请求 —— 慢接口不会把整屏锁在 spinner 上。
 */
type BlockState = 'loading' | 'ready' | 'error'
const summaryState = ref<BlockState>('loading')
const healthState = ref<BlockState>('loading')
const llmState = ref<BlockState>('loading')
const ragState = ref<BlockState>('loading')
const orgState = ref<BlockState>('loading')
const auditState = ref<BlockState>('loading')

const adminStats = ref({
  totalUsers: 0,
  totalSessions: 0,
  totalDocs: 0,
  totalOrgs: 0,
  activeUsers: 0,
  // W2-T7：会话周环比真值（后端 admin-summary 新键；null=无基线/降级态）
  sessionsThisWeek: 0,
  sessionsLastWeek: null as number | null,
})
// 健康卡真实值（B6）：/monitoring/health 的 status 与 services.elasticsearch。
// 独立于 adminStats，避免两路请求互相覆盖。
const systemHealthy = ref(false)
const esHealth = ref('')

/**
 * J4：ES 健康值词条化 —— 后端值域 green/yellow/red/unknown/disconnected
 * 不再以英文状态词裸露在中英文界面；green/red 复用 healthy/unhealthy 口径，
 * yellow/unknown/disconnected 走独立词条。
 */
const ES_HEALTH_I18N_KEY: Record<string, string> = {
  green: 'dashboard.healthy',
  yellow: 'dashboard.esHealthYellow',
  red: 'dashboard.unhealthy',
  unknown: 'dashboard.esHealthUnknown',
  disconnected: 'dashboard.esHealthDisconnected',
}

const esHealthText = computed(() => {
  // E6 分块加载后健康值拆为独立 esHealth ref（healthState 管加载门控），
  // 此处只负责值域 → 词条映射；空值由渲染侧与这里共同兜底为 '—'。
  const raw = esHealth.value
  if (!raw) return '—'
  return t(ES_HEALTH_I18N_KEY[raw] ?? 'dashboard.esHealthUnknown')
})

// W2-T7：真实环比 =（本周新增-上周新增）/上周新增；上周为 0/缺（无基线）→ null（渲染"上周无数据"），
// 不再回退任何写死百分比
const sessionTrend = computed<number | null>(() => {
  const cur = adminStats.value.sessionsThisWeek
  const last = adminStats.value.sessionsLastWeek
  if (last === null || last === undefined || last === 0) return null
  return Math.round(((cur - last) / last) * 100)
})

const llmStats = ref({
  tokensToday: 0,
  costToday: 0,
  avgLatency: 0,
  p95Latency: 0,
  costWarning: false,
  trend: [] as number[],
  // 「今日请求」真实口径（C1）：llm-stats.request_count_today
  requestCountToday: 0
})

const ragStats = ref({
  hitRate: 0,
  totalQueries: 0,
  hits: 0,
  avgDocs: 0,
  trend: [] as number[]
})

const orgList = ref<OrgSummary[]>([])
const recentLogs = ref<AuditLogEntry[]>([])

const llmChartRef = ref<HTMLElement | null>(null)
const ragChartRef = ref<HTMLElement | null>(null)
let llmChart: EChartsType | null = null
let ragChart: EChartsType | null = null

const SENSITIVE_ACTIONS = ['delete_user', 'delete_document', 'reset_password', 'update_role', 'delete_organization', 'api_key_revoked']

const auditColumns = [
  {
    title: t('dashboard.time'),
    key: 'created_at',
    width: 170,
    render: (row: AuditLogEntry) => h('span', { class: 'text-gray-600 dark:text-gray-300' }, formatDate(row.created_at))
  },
  {
    title: t('dashboard.user'),
    key: 'user_id',
    width: 100,
    render: (row: AuditLogEntry) => h('span', { class: 'font-medium' }, row.username || `ID:${row.user_id}`)
  },
  {
    title: t('dashboard.action'),
    key: 'action',
    width: 160,
    render: (row: AuditLogEntry) => {
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
    render: (row: AuditLogEntry) => h('code', { class: 'text-xs text-gray-500' }, row.ip_address || '-')
  }
]

function highlightSensitiveRow(row: AuditLogEntry) {
  const isSensitive = SENSITIVE_ACTIONS.some(a => row.action?.includes(a))
  return isSensitive ? 'bg-red-50/70 dark:bg-red-900/10' : ''
}

/**
 * RAG 命中率趋势轴标签：后端 hit_rate_trend_7d 实为 8 个 3 小时桶覆盖最近
 * 24h（knowledge.py rag-stats 构造，range(8)×3h；事件仅保留 24h），第 k 点
 * 桶起点 = now - (N - k) 个 3h。旧实现套「周一~周日」7 个标签 → 第 8 点丢失/
 * 错贴（B5）。此处按真实桶生成 HH:00 标签（最小诚实）。
 */
const ragBucketLabels = (count: number): string[] => {
  const labels: string[] = []
  const now = Date.now()
  for (let k = 0; k < count; k++) {
    const start = new Date(now - (count - k) * 3 * 3600 * 1000)
    labels.push(`${String(start.getHours()).padStart(2, '0')}:00`)
  }
  return labels
}

const emptyChartSubtext = (data: number[]) => ({
  title: {
    text: '',
    subtext: data.length ? '' : '暂无数据',
    subtextStyle: { color: '#9ca3af', fontSize: 12 },
    left: 'center',
    top: 'middle'
  }
})

// LLM token 趋势：后端 token_trend_7d 为 7 个日桶 → 保留 7 天标签
const llmDays = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

/** LLM 面板数据到位后单独初始化（与 RAG 图互不等待） */
const initLlmChart = () => {
  if (!llmChartRef.value) return
  llmChart?.dispose()
  llmChart = echarts.init(llmChartRef.value)
  const llmTrend = llmStats.value.trend
  llmChart.setOption({
    ...emptyChartSubtext(llmTrend),
    tooltip: { trigger: 'axis', backgroundColor: 'rgba(255,255,255,0.95)', borderColor: '#e5e7eb', textStyle: { color: '#374151' } },
    grid: { left: '2%', right: '3%', bottom: '3%', top: '8%', containLabel: true },
    xAxis: { type: 'category', data: llmDays, axisLine: { lineStyle: { color: '#e5e7eb' } }, axisLabel: { color: '#6b7280', fontSize: 10 }, splitLine: { show: false } },
    yAxis: { type: 'value', axisLine: { show: false }, axisLabel: { color: '#6b7280', fontSize: 10 }, splitLine: { lineStyle: { color: '#f3f4f6' } } },
    series: [{
      // 无数据即为空序列（旧实现回退 [1200,1800,…] 伪造曲线——已删）
      data: llmTrend,
      type: 'line',
      smooth: 0.4,
      lineStyle: { width: 2, color: CHART_COLORS.sky },
      itemStyle: { color: CHART_COLORS.sky },
      areaStyle: { opacity: 0.15, color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
        { offset: 0, color: CHART_COLORS.sky },
        { offset: 1, color: 'rgba(168,85,247,0.0)' }
      ]) },
      symbol: 'circle',
      symbolSize: 5,
      showSymbol: false,
      emphasis: { focus: 'series', itemStyle: { symbolSize: 8 } }
    }]
  })
}

/** RAG 面板数据到位后单独初始化（与 LLM 图互不等待） */
const initRagChart = () => {
  if (!ragChartRef.value) return
  ragChart?.dispose()
  ragChart = echarts.init(ragChartRef.value)
  const ragTrend = ragStats.value.trend
  ragChart.setOption({
    ...emptyChartSubtext(ragTrend),
    tooltip: { trigger: 'axis', backgroundColor: 'rgba(255,255,255,0.95)', borderColor: '#e5e7eb', textStyle: { color: '#374151' } },
    grid: { left: '2%', right: '3%', bottom: '3%', top: '8%', containLabel: true },
    xAxis: { type: 'category', data: ragBucketLabels(ragTrend.length), axisLine: { lineStyle: { color: '#e5e7eb' } }, axisLabel: { color: '#6b7280', fontSize: 10 }, splitLine: { show: false } },
    yAxis: { type: 'value', min: 0, max: 100, axisLine: { show: false }, axisLabel: { color: '#6b7280', fontSize: 10, formatter: '{value}%' }, splitLine: { lineStyle: { color: '#f3f4f6' } } },
    series: [{
      // 真实 8 桶数据；空即空（旧实现回退 [0,…,hitRate] 伪造末点——已删）
      data: ragTrend,
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

// ---- 分块加载器：六路请求并行发起，各自落位、各自骨架/错误态 ----

const loadSummary = async () => {
  summaryState.value = 'loading'
  try {
    const adminRes = await getAdminSummary()
    if (adminRes.data) {
      adminStats.value = {
        totalUsers: adminRes.data.total_users || 0,
        totalSessions: adminRes.data.total_sessions || 0,
        totalDocs: adminRes.data.total_documents || 0,
        totalOrgs: adminRes.data.total_organizations || 0,
        activeUsers: adminRes.data.active_users_24h || 0,
        // W2-T7：会话周环比真值（后端新键；degraded/缺省 → null → 页面渲染"上周无数据"）
        sessionsThisWeek: adminRes.data.sessions_this_week ?? 0,
        sessionsLastWeek: adminRes.data.sessions_last_week ?? null,
      }
    }
    summaryState.value = 'ready'
  } catch {
    summaryState.value = 'error'
  }
}

const loadHealth = async () => {
  healthState.value = 'loading'
  try {
    const healthRes = await getMonitoringHealth()
    // 健康卡真实口径（B6）：/monitoring/health 的 status（healthy/degraded）
    // 与 services.elasticsearch（green/yellow/red/unknown/disconnected）。
    if (!healthRes.data) throw new Error('empty health payload')
    systemHealthy.value = healthRes.data.status === 'healthy'
    esHealth.value = healthRes.data.services?.elasticsearch || 'unknown'
    healthState.value = 'ready'
  } catch {
    healthState.value = 'error'
  }
}

const loadLLM = async () => {
  llmState.value = 'loading'
  try {
    const llmRes = await getLLMStats()
    if (!llmRes.data) throw new Error('empty llm-stats payload')
    llmStats.value = {
      tokensToday: llmRes.data.total_tokens_today || 0,
      costToday: llmRes.data.cost_today_usd || 0,
      avgLatency: llmRes.data.avg_latency_ms || 0,
      p95Latency: llmRes.data.p95_latency_ms || 0,
      costWarning: llmRes.data.cost_warning || false,
      trend: llmRes.data.token_trend_7d || [],
      // 「今日请求」真实口径（C1）：llm-stats.request_count_today 是后端真实
      // 请求计数（rag_service 24h 滚动窗口检索数，字段名即今日请求）——
      // 替代此前 total_sessions 顶替的谎言口径。
      requestCountToday: llmRes.data.request_count_today || 0
    }
    llmState.value = 'ready'
    // 等本区块内容（含图表容器）渲染后再初始化，不依赖其它区块是否到齐
    await nextTick()
    initLlmChart()
  } catch {
    llmState.value = 'error'
  }
}

const loadRAG = async () => {
  ragState.value = 'loading'
  try {
    const ragRes = await getRAGStats()
    if (!ragRes.data) throw new Error('empty rag-stats payload')
    ragStats.value = {
      hitRate: ragRes.data.hit_rate || 0,
      totalQueries: ragRes.data.total_queries_7d || 0,
      hits: ragRes.data.hits_with_documents || 0,
      avgDocs: ragRes.data.avg_documents_retrieved || 0,
      trend: ragRes.data.hit_rate_trend_7d || []
    }
    ragState.value = 'ready'
    await nextTick()
    initRagChart()
  } catch {
    ragState.value = 'error'
  }
}

const loadOrg = async () => {
  orgState.value = 'loading'
  try {
    const orgRes = await getOrgSummary()
    if (orgRes.data?.organizations) {
      orgList.value = orgRes.data.organizations
    }
    orgState.value = 'ready'
  } catch {
    orgState.value = 'error'
  }
}

const loadAudit = async () => {
  auditState.value = 'loading'
  try {
    const auditRes = await getAuditLogs({ limit: 10 }, FIRST_SCREEN_REQUEST_CONFIG)
    const auditData = auditRes.data as unknown as Record<string, unknown>
    const auditInner = auditData?.data as Record<string, unknown> | undefined
    if (auditInner?.items) {
      recentLogs.value = auditInner.items as AuditLogEntry[]
    }
    auditState.value = 'ready'
  } catch {
    auditState.value = 'error'
  }
}

/**
 * 首屏加载：六路请求一次性并行发出（旧实现把 RAG/组织概览串行排在
 * Promise.all 之后 —— 连锁等待；现在谁先到谁先替换自己的区块）。
 */
const loadData = () => {
  void loadSummary()
  void loadHealth()
  void loadLLM()
  void loadRAG()
  void loadOrg()
  void loadAudit()
}

onMounted(() => {
  loadData()
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
