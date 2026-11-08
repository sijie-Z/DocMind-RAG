<template>
  <div class="admin-dashboard p-4 md:p-6 space-y-5">
    <div class="mb-1">
      <h1 class="text-2xl font-semibold tracking-tight text-slate-900 dark:text-white md:text-[28px]">
        {{ t('dashboard.adminTitle') }}
      </h1>
      <p class="mt-1 flex items-center gap-2 text-sm text-slate-500 dark:text-slate-400">
        <span class="h-0.5 w-6 rounded-full bg-blue-600"></span>
        {{ t('dashboard.adminSubtitle') }}
      </p>
    </div>
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
      <span class="text-sm text-slate-600 dark:text-slate-300">{{ t('dashboard.loadFailed') }}</span>
      <span class="text-xs text-slate-500 dark:text-slate-400">{{ t('dashboard.errorHint') }}</span>
      <n-button size="small" @click="loadSummary">{{ t('dashboard.retry') }}</n-button>
    </div>
    <div
      v-else-if="summaryDegraded && !summaryEverReady"
      class="flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-amber-300 bg-amber-50/50 py-10 dark:border-amber-800/60 dark:bg-amber-900/10"
      data-testid="kpi-degraded"
    >
      <span class="text-sm text-amber-800 dark:text-amber-200">{{ t('dashboard.dataUnavailable') }}</span>
      <n-button size="small" @click="loadSummary">{{ t('dashboard.retry') }}</n-button>
    </div>
    <div v-else class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
      <div
        v-if="summaryDegraded"
        class="col-span-full rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-800/60 dark:bg-amber-900/10 dark:text-amber-200"
        data-testid="kpi-stale"
      >
        {{ t('dashboard.statsStale') }}
      </div>
      <div class="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900">
        <div class="flex items-center justify-between mb-2">
          <span class="text-xs font-medium text-slate-500 dark:text-slate-400">{{ t('dashboard.totalUsers') }}</span>
          <div class="w-8 h-8 bg-slate-100 dark:bg-slate-800 flex items-center justify-center">
            <n-icon size="18" class="text-slate-600 dark:text-slate-300"><PeopleOutline /></n-icon>
          </div>
        </div>
        <p class="text-2xl font-semibold tabular-nums text-slate-900 dark:text-white">{{ adminStats.totalUsers.toLocaleString() }}</p>
        <div class="flex items-center gap-1 mt-1 text-xs text-slate-500 dark:text-slate-400">
        </div>
      </div>

      <div class="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900">
        <div class="flex items-center justify-between mb-2">
          <span class="text-xs font-medium text-slate-500 dark:text-slate-400">{{ t('dashboard.totalSessions') }}</span>
          <div class="w-8 h-8 bg-slate-100 dark:bg-slate-800 flex items-center justify-center">
            <n-icon size="18" class="text-slate-600 dark:text-slate-300"><ChatbubbleEllipsesOutline /></n-icon>
          </div>
        </div>
        <p class="text-2xl font-semibold tabular-nums text-slate-900 dark:text-white">{{ adminStats.totalSessions.toLocaleString() }}</p>
        <div class="flex items-center gap-1 mt-1 text-xs text-slate-500 dark:text-slate-400">
          <!-- W2-T7：此前是写死的「↑ +12%（对比上周）」（§八实机实锤假趋势）；
               改为后端 sessions_this_week/last_week 真环比，无基线（上周 0/缺）如实说 -->
          <template v-if="sessionTrend !== null">
            <span :class="sessionTrend >= 0 ? 'text-emerald-700 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'">
              {{ sessionTrend >= 0 ? '↑' : '↓' }} {{ sessionTrend >= 0 ? '+' : '' }}{{ sessionTrend }}%
            </span>
            <span>{{ t('dashboard.compareLastWeek') }}</span>
          </template>
          <span v-else>{{ t('dashboard.lastWeekNoData') }}</span>
        </div>
      </div>

      <div class="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900">
        <div class="flex items-center justify-between mb-2">
          <span class="text-xs font-medium text-slate-500 dark:text-slate-400">{{ t('dashboard.totalDocs') }}</span>
          <div class="w-8 h-8 bg-slate-100 dark:bg-slate-800 flex items-center justify-center">
            <n-icon size="18" class="text-slate-600 dark:text-slate-300"><DocumentTextOutline /></n-icon>
          </div>
        </div>
        <p class="text-2xl font-semibold tabular-nums text-slate-900 dark:text-white">{{ adminStats.totalDocs.toLocaleString() }}</p>
        <div class="flex items-center gap-1 mt-1 text-xs text-slate-500 dark:text-slate-400">
          <span>{{ t('dashboard.totalOrgs') }}: {{ adminStats.totalOrgs }}</span>
        </div>
      </div>

      <div class="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900">
        <div class="flex items-center justify-between mb-2">
          <span class="text-xs font-medium text-slate-500 dark:text-slate-400">{{ t('dashboard.activeUsers') }}</span>
          <div class="w-8 h-8 bg-slate-100 dark:bg-slate-800 flex items-center justify-center">
            <n-icon size="18" class="text-slate-600 dark:text-slate-300"><PulseOutline /></n-icon>
          </div>
        </div>
        <p class="text-2xl font-semibold tabular-nums text-slate-900 dark:text-white">{{ adminStats.activeUsers }}</p>
        <div class="flex items-center gap-1 mt-1 text-xs text-slate-500 dark:text-slate-400">
          <span>{{ t('dashboard.active24h') }}</span>
        </div>
      </div>

      <div class="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900">
        <div class="flex items-center justify-between mb-2">
          <span class="text-xs font-medium text-slate-500 dark:text-slate-400">{{ t('dashboard.todayRequests') }}</span>
          <div class="w-8 h-8 bg-slate-100 dark:bg-slate-800 flex items-center justify-center">
            <n-icon size="18" class="text-slate-600 dark:text-slate-300"><GitNetworkOutline /></n-icon>
          </div>
        </div>
        <p class="text-2xl font-semibold tabular-nums text-slate-900 dark:text-white" data-testid="kpi-llm-value">
          <n-skeleton v-if="llmState === 'loading'" text style="width: 64px" />
          <template v-else>{{ llmCardValue }}</template>
        </p>
        <div class="flex items-center gap-1 mt-1 text-xs text-slate-500 dark:text-slate-400" data-testid="kpi-llm-sub">
          <n-skeleton v-if="llmState === 'loading'" text style="width: 80px" />
          <span v-else>{{ llmCardSub }}</span>
        </div>
      </div>

      <div class="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900">
        <div class="flex items-center justify-between mb-2">
          <span class="text-xs font-medium text-slate-500 dark:text-slate-400">{{ t('dashboard.systemStatus') }}</span>
          <div class="w-8 h-8 bg-slate-100 dark:bg-slate-800 flex items-center justify-center">
            <n-icon size="18" class="text-slate-600 dark:text-slate-300"><ServerOutline /></n-icon>
          </div>
        </div>
        <div class="mt-3">
          <n-skeleton v-if="healthState === 'loading'" text style="width: 48px" />
          <n-tag v-else :type="healthTone" size="small" round>
            {{ healthLabel }}
          </n-tag>
        </div>
        <div class="flex items-center gap-1 mt-2 text-xs text-slate-500 dark:text-slate-400">
          <span>{{ t('dashboard.esLabel') }}: {{ healthState === 'ready' ? esHealthText : '—' }}</span>
          <n-button v-if="healthState === 'error'" text size="tiny" class="text-slate-600 dark:text-slate-300" @click="loadHealth">
            {{ t('dashboard.retry') }}
          </n-button>
        </div>
      </div>
    </div>

    <!-- 两列布局：AI 监控 + RAG 性能 -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-5">
      <!-- AI 资源监控 -->
      <div class="bg-white dark:bg-gray-800 rounded-2xl border border-gray-100 dark:border-gray-700 shadow-sm overflow-hidden">
        <div class="px-5 py-4 border-b border-gray-100 dark:border-gray-700 flex items-center justify-between">
          <div class="flex items-center gap-2">
            <div class="w-8 h-8 bg-slate-100 dark:bg-slate-900/30 rounded-lg flex items-center justify-center">
              <n-icon class="text-blue-600 dark:text-blue-400"><PulseOutline /></n-icon>
            </div>
            <div>
              <h3 class="font-semibold text-gray-900 dark:text-white">{{ t('dashboard.aiMonitor') }}</h3>
              <p class="text-xs text-slate-500 dark:text-slate-400">{{ t('dashboard.llmSubtitle') }}</p>
            </div>
          </div>
          <div class="flex items-center gap-2">
            <n-tag v-if="llmStats.costWarning" type="warning" size="small" round>
              <template #icon><n-icon size="12"><WarningOutline /></n-icon></template>
              {{ t('dashboard.costWarning') }}
            </n-tag>
            <span class="text-xs text-slate-500 dark:text-slate-400">{{ todayLabel }}</span>
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
            <span class="text-sm text-slate-600 dark:text-slate-300">{{ t('dashboard.loadFailed') }}</span>
            <span class="text-xs text-slate-500 dark:text-slate-400">{{ t('dashboard.errorHint') }}</span>
            <n-button size="small" @click="loadLLM">{{ t('dashboard.retry') }}</n-button>
          </div>
        </div>
        <div v-else-if="llmDegraded && !llmEverReady" class="p-5" data-testid="llm-degraded">
          <div class="flex flex-col items-center justify-center gap-3 py-10">
            <span class="text-sm text-slate-600 dark:text-slate-300">{{ t('dashboard.dataUnavailable') }}</span>
            <n-button size="small" @click="loadLLM">{{ t('dashboard.retry') }}</n-button>
          </div>
        </div>
        <div v-else class="p-5">
          <div
            v-if="llmDegraded"
            class="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-800/60 dark:bg-amber-900/10 dark:text-amber-200"
            data-testid="llm-stale"
          >
            {{ t('dashboard.statsStale') }}
          </div>
          <!-- 统计数据 -->
          <div class="grid grid-cols-2 gap-4 mb-5">
            <div class="bg-gray-50 dark:bg-gray-700/50 rounded-xl p-4">
              <div class="flex items-center gap-2 mb-1">
                <div class="w-2 h-2 rounded-full bg-blue-500"></div>
                <span class="text-xs text-gray-500 dark:text-gray-400">{{ t('dashboard.tokensToday') }}</span>
              </div>
              <p class="text-xl font-bold text-gray-900 dark:text-white">{{ llmStats.tokensToday.toLocaleString() }}</p>
              <p class="text-xs text-slate-500 dark:text-slate-400 mt-1">{{ t('dashboard.inputOutput') }}</p>
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
              <p class="text-xs text-slate-500 dark:text-slate-400 mt-1">{{ t('dashboard.deepseekRate') }}</p>
            </div>
            <div class="bg-gray-50 dark:bg-gray-700/50 rounded-xl p-4">
              <div class="flex items-center gap-2 mb-1">
                <div class="w-2 h-2 rounded-full bg-blue-500"></div>
                <span class="text-xs text-gray-500 dark:text-gray-400">{{ t('dashboard.avgLatency') }}</span>
              </div>
              <p class="text-xl font-bold text-gray-900 dark:text-white">{{ llmStats.avgLatency }}<span class="text-sm font-normal text-gray-400">ms</span></p>
              <p class="text-xs text-slate-500 dark:text-slate-400 mt-1">{{ t('dashboard.httpLatencyHint') }}</p>
            </div>
            <div class="bg-gray-50 dark:bg-gray-700/50 rounded-xl p-4">
              <div class="flex items-center gap-2 mb-1">
                <div class="w-2 h-2 rounded-full bg-orange-500"></div>
                <span class="text-xs text-gray-500 dark:text-gray-400">{{ t('dashboard.p95Latency') }}</span>
              </div>
              <p class="text-xl font-bold text-gray-900 dark:text-white">{{ llmStats.p95Latency }}<span class="text-sm font-normal text-gray-400">ms</span></p>
              <p class="text-xs text-slate-500 dark:text-slate-400 mt-1">{{ t('dashboard.httpP95Hint') }}</p>
            </div>
          </div>
          <!-- 趋势图：后端没有按日 token 真源时返回空数组，不画假坐标轴 -->
          <div v-if="llmStats.trend.length" class="h-44 rounded-xl bg-gray-50 dark:bg-gray-700/30 p-3">
            <div ref="llmChartRef" class="w-full h-full"></div>
          </div>
          <div v-else class="flex h-44 items-center justify-center rounded-xl border border-dashed border-slate-200 dark:border-slate-700">
            <span class="text-sm text-slate-600 dark:text-slate-300">{{ t('dashboard.llmNoDailyTrend') }}</span>
          </div>
        </div>
      </div>

      <!-- RAG 命中率 -->
      <div class="bg-white dark:bg-gray-800 rounded-2xl border border-gray-100 dark:border-gray-700 shadow-sm overflow-hidden flex flex-col lg:self-start" data-testid="rag-card">
        <div class="px-5 py-4 border-b border-gray-100 dark:border-gray-700 flex items-center justify-between">
          <div class="flex items-center gap-2">
            <div class="w-8 h-8 bg-emerald-100 dark:bg-emerald-900/30 rounded-lg flex items-center justify-center">
              <n-icon class="text-emerald-600 dark:text-emerald-400"><AnalyticsOutline /></n-icon>
            </div>
            <div>
              <h3 class="font-semibold text-gray-900 dark:text-white">{{ t('dashboard.ragStats') }}</h3>
              <p class="text-xs text-slate-500 dark:text-slate-400">{{ t('dashboard.ragSubtitle24h') }}</p>
            </div>
          </div>
          <div class="flex items-center gap-2">
            <n-tag v-if="ragHasData" :type="ragQualityTag" size="small" round>
              {{ ragQualityLabel }}
            </n-tag>
            <n-tag v-else type="default" size="small" round>{{ t('dashboard.ragNoData') }}</n-tag>
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
            <span class="text-sm text-slate-600 dark:text-slate-300">{{ t('dashboard.loadFailed') }}</span>
            <span class="text-xs text-slate-500 dark:text-slate-400">{{ t('dashboard.errorHint') }}</span>
            <n-button size="small" data-testid="rag-retry" @click="loadRAG">{{ t('dashboard.retry') }}</n-button>
          </div>
        </div>
        <div v-else-if="ragDegraded && !ragEverReady" class="p-5" data-testid="rag-degraded">
          <div class="flex flex-col items-center justify-center gap-3 py-10">
            <span class="text-sm text-slate-600 dark:text-slate-300">{{ t('dashboard.dataUnavailable') }}</span>
            <n-button size="small" data-testid="rag-retry" @click="loadRAG">{{ t('dashboard.retry') }}</n-button>
          </div>
        </div>
        <div v-else class="p-5 flex-1 flex flex-col">
          <div
            v-if="ragDegraded"
            class="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-800/60 dark:bg-amber-900/10 dark:text-amber-200"
          >
            {{ t('dashboard.statsStale') }}
          </div>
          <!-- 命中率主指标 -->
          <div v-if="ragHasData" class="flex items-center gap-4 mb-5">
            <div class="flex-1">
              <p class="text-sm text-slate-500 dark:text-slate-400 mb-3">{{ t('dashboard.hitRate') }}</p>
              <n-progress
                type="line"
                :percentage="ragStats.hitRate"
                :indicator-placement="'inside'"
                :height="20"
                :border-radius="10"
                :fill-border-radius="10"
                :color="ragStats.hitRate > 60 ? CHART_COLORS.secondary : ragStats.hitRate > 30 ? CHART_COLORS.warning : CHART_COLORS.danger"
                :rail-color="isDark ? '#1f2937' : '#f3f4f6'"
              />
            </div>
            <div class="text-right">
              <p class="text-4xl font-semibold tabular-nums" :class="hitRateClass">{{ ragStats.hitRate }}<span class="text-xl">%</span></p>
            </div>
          </div>
          <div v-else class="mb-5 rounded-xl border border-dashed border-slate-200 px-4 py-8 text-center dark:border-slate-700">
            <p class="text-sm text-slate-600 dark:text-slate-300">{{ t('dashboard.ragNoDataHint') }}</p>
          </div>
          <!-- 统计数字 -->
          <div class="grid grid-cols-3 gap-3 mb-5">
            <div class="text-center p-3 bg-gray-50 dark:bg-gray-700/50 rounded-xl">
              <p class="text-xs text-gray-500 dark:text-gray-400 mb-1">{{ t('dashboard.queries7d') }}</p>
              <p class="text-lg font-bold text-gray-900 dark:text-white">{{ ragStats.totalQueries.toLocaleString() }}</p>
            </div>
            <div class="text-center p-3 rounded-xl" :class="ragStats.hits > 0 ? 'bg-emerald-50 dark:bg-emerald-900/20' : 'bg-gray-50 dark:bg-gray-700/50'">
              <p class="text-xs text-slate-500 dark:text-slate-400 mb-1">{{ t('dashboard.hits') }}</p>
              <p class="text-lg font-bold tabular-nums" :class="ragStats.hits > 0 ? 'text-emerald-700 dark:text-emerald-400' : 'text-slate-900 dark:text-white'">{{ ragStats.hits.toLocaleString() }}</p>
            </div>
            <div class="text-center p-3 bg-gray-50 dark:bg-gray-700/50 rounded-xl">
              <p class="text-xs text-gray-500 dark:text-gray-400 mb-1">{{ t('dashboard.avgDocs') }}</p>
              <p
                class="text-lg font-bold text-gray-900 dark:text-white"
                :title="ragStats.totalQueries > 0 ? undefined : t('dashboard.avgDocsNoData')"
              >{{ ragStats.totalQueries > 0 ? ragStats.avgDocs.toLocaleString(undefined, { maximumFractionDigits: 2 }) : '—' }}</p>
            </div>
          </div>
          <!-- 趋势图（flex-1：与左卡等高时填充剩余空间，消除卡底空白） -->
          <div v-if="ragHasData" class="h-44 rounded-xl bg-gray-50 dark:bg-gray-700/30 p-3" data-testid="rag-chart">
            <div ref="ragChartRef" class="w-full h-full"></div>
          </div>

        </div>
      </div>
    </div>

    <!-- 组织概览 -->
    <div class="bg-white dark:bg-gray-800 rounded-2xl border border-gray-100 dark:border-gray-700 shadow-sm overflow-hidden">
      <div class="px-5 py-4 border-b border-gray-100 dark:border-gray-700 flex items-center justify-between">
        <div class="flex items-center gap-2">
          <div class="w-8 h-8 bg-slate-100 dark:bg-slate-900/30 rounded-lg flex items-center justify-center">
            <n-icon class="text-blue-600 dark:text-blue-400"><BusinessOutline /></n-icon>
          </div>
          <div>
            <h3 class="font-semibold text-gray-900 dark:text-white">{{ t('org.title') }}</h3>
            <p class="text-xs text-gray-500 dark:text-gray-400">{{ t('dashboard.totalOrgs') }}: {{ orgState === 'ready' ? orgList.length : '—' }}</p>
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
          <span class="text-sm text-slate-600 dark:text-slate-300">
            {{ orgDegraded ? t('dashboard.dataUnavailable') : t('dashboard.loadFailed') }}
          </span>
          <n-button size="small" @click="loadOrg">{{ t('dashboard.retry') }}</n-button>
        </div>
        <template v-else>
        <div v-if="orgList.length > 0" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
          <div
            v-for="(org, idx) in orgList"
            :key="org.org_id"
            class="group p-4 rounded-xl border-2 border-transparent hover:border-slate-200 dark:hover:border-blue-700 bg-gray-50 dark:bg-gray-700/30 hover:bg-slate-50/50 dark:hover:bg-slate-900/10 transition-all duration-200 cursor-pointer focus-visible:ring-2 focus-visible:ring-blue-500"
            :style="{ animationDelay: `${idx * 50}ms` }"
            role="link"
            tabindex="0"
            @click="$router.push('/organizations')"
            @keydown.enter.prevent="$router.push('/organizations')"
            @keydown.space.prevent="$router.push('/organizations')"
          >
            <div class="flex items-center gap-3">
              <div class="w-10 h-10 rounded-xl flex items-center justify-center text-white font-bold text-sm" :style="{ backgroundColor: ORG_AVATAR_COLORS[idx % ORG_AVATAR_COLORS.length] }">
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
      <div class="px-5 py-4 border-b border-gray-100 dark:border-gray-700 flex items-center justify-between">
        <div class="flex items-center gap-2">
          <div class="w-8 h-8 bg-red-100 dark:bg-red-900/30 rounded-lg flex items-center justify-center">
            <n-icon class="text-red-600 dark:text-red-400"><ShieldCheckmarkOutline /></n-icon>
          </div>
          <div>
            <h3 class="font-semibold text-gray-900 dark:text-white">{{ t('dashboard.recentAuditLogs') }}</h3>
            <p class="text-xs text-slate-500 dark:text-slate-400">{{ t('dashboard.auditSubtitle') }}</p>
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
          :scroll-x="720"
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
      <n-button type="primary" size="large" @click="$router.push('/users')" >
        <template #icon><n-icon><PeopleOutline /></n-icon></template>
        {{ t('dashboard.manageUsers') }}
      </n-button>
      <n-button secondary size="large" @click="$router.push('/knowledge')">
        <template #icon><n-icon><BookOutline /></n-icon></template>
        {{ t('dashboard.manageKB') }}
      </n-button>
      <n-button secondary size="large" @click="$router.push('/monitoring')">
        <template #icon><n-icon><PulseOutline /></n-icon></template>
        {{ t('dashboard.systemMonitor') }}
      </n-button>
      <n-button secondary size="large" @click="$router.push('/audit-logs')">
        <template #icon><n-icon><ShieldCheckmarkOutline /></n-icon></template>
        {{ t('menu.auditLogs') }}
      </n-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, h, nextTick, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useIsDark } from '@/composables/useIsDark'
import { NTag } from 'naive-ui'
import { getAuditLogs, type AuditLogEntry } from '@/api/user'
import { getLLMStats, getRAGStats, getAdminSummary, getOrgSummary, getMonitoringHealth, type OrgSummary } from '@/api/monitoring'
import { FIRST_SCREEN_REQUEST_CONFIG } from '@/utils/request'
import { formatDate } from '@/utils/format'
import { CHART_COLORS, CHART_THEME, DARK_CHART_THEME } from '@/utils/chartTheme'
import {
  PulseOutline, AnalyticsOutline, PeopleOutline, BookOutline, BusinessOutline,
  DocumentTextOutline, ServerOutline, ChatbubbleEllipsesOutline, GitNetworkOutline,
  ChevronForwardOutline, ShieldCheckmarkOutline, WarningOutline
} from '@vicons/ionicons5'
import echarts from '@/utils/echarts'
import type { EChartsType } from 'echarts/core'

const { t, locale } = useI18n()
const isDark = useIsDark()

/**
 * 分块加载状态：'loading' 骨架 / 'ready' 内容 / 'error' 区块错误态 + 重试。
 * 每个区块只依赖自己那路请求 —— 慢接口不会把整屏锁在 spinner 上。
 */
type BlockState = 'loading' | 'ready' | 'error'
const llmCardValue = computed(() => {
  if (llmState.value !== 'ready' || (llmDegraded.value && !llmEverReady.value)) return '—'
  return llmStats.value.requestCountToday.toLocaleString()
})
const llmCardSub = computed(() => {
  if (llmState.value !== 'ready' || (llmDegraded.value && !llmEverReady.value)) return '—'
  return t('dashboard.avgLatencyValue', { ms: llmStats.value.avgLatency || 0 })
})
const hitRateClass = computed(() => {
  if (!ragHasData.value) return 'text-slate-900 dark:text-white'
  const rate = ragStats.value.hitRate
  return rate > 60
    ? 'text-emerald-700 dark:text-emerald-400'
    : rate > 30
      ? 'text-amber-700 dark:text-amber-400'
      : 'text-red-600 dark:text-red-400'
})
const summaryState = ref<BlockState>('loading')
const summaryDegraded = ref(false)
const summaryEverReady = ref(false)
const llmDegraded = ref(false)
const llmEverReady = ref(false)
const ragDegraded = ref(false)
const ragEverReady = ref(false)
const orgDegraded = ref(false)
const orgEverReady = ref(false)
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

const healthTone = computed<'default' | 'success' | 'warning' | 'error'>(() => {
  if (healthState.value !== 'ready') return 'default'
  if (!systemHealthy.value || ['red', 'disconnected'].includes(esHealth.value)) return 'error'
  if (['yellow', 'unknown'].includes(esHealth.value)) return 'warning'
  return 'success'
})
const healthLabel = computed(() => {
  if (healthState.value !== 'ready') return '—'
  if (!systemHealthy.value || ['red', 'disconnected'].includes(esHealth.value)) {
    return t('dashboard.unhealthy')
  }
  if (['yellow', 'unknown'].includes(esHealth.value)) return t('dashboard.attention')
  return t('dashboard.healthy')
})
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

const ragHasData = computed(() => ragStats.value.totalQueries > 0)
const ragQualityLabel = computed(() =>
  ragStats.value.hitRate > 60
    ? t('dashboard.ragQualityGood')
    : ragStats.value.hitRate > 30
      ? t('dashboard.ragQualityMedium')
      : t('dashboard.ragQualityPoor')
)
const ragQualityTag = computed(() =>
  ragStats.value.hitRate > 60 ? 'success' : ragStats.value.hitRate > 30 ? 'warning' : 'error'
)
const todayLabel = computed(() =>
  new Intl.DateTimeFormat(locale.value, { dateStyle: 'medium' }).format(new Date())
)
const ragStats = ref({
  hitRate: 0,
  totalQueries: 0,
  hits: 0,
  avgDocs: 0,
  trend: [] as number[]
})

const ORG_AVATAR_COLORS = ['#1d4ed8', '#0369a1', '#0f766e', '#15803d', '#b45309', '#475569']
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
    subtext: data.length ? '' : t('dashboard.noChartData'),
    subtextStyle: { color: '#9ca3af', fontSize: 12 },
    left: 'center',
    top: 'middle'
  }
})

// LLM token 趋势：后端 token_trend_7d 为 7 个日桶 → 保留 7 天标签。
// 星期标签走 common.weekdays 词条（周一为首；0=周日 与 Date/cron 口径一致）。
const WEEK_ORDER = [1, 2, 3, 4, 5, 6, 0]
const llmDays = computed(() => WEEK_ORDER.map((d) => t(`common.weekdays.${d}`)))

/** LLM 面板数据到位后单独初始化（与 RAG 图互不等待） */
const initLlmChart = () => {
  if (!llmChartRef.value) return
  llmChart?.dispose()
  llmChart = echarts.init(llmChartRef.value)
  const llmTrend = llmStats.value.trend
  const theme = isDark.value ? DARK_CHART_THEME : CHART_THEME
  llmChart.setOption({
    ...emptyChartSubtext(llmTrend),
    tooltip: theme.tooltip,
    grid: { ...theme.grid, left: '2%', right: '3%', bottom: '3%', top: '8%' },
    xAxis: { ...theme.xAxis, type: 'category', data: llmDays.value, splitLine: { show: false } },
    yAxis: { ...theme.yAxis, type: 'value' },
    series: [{
      // 无数据即为空序列（旧实现回退 [1200,1800,…] 伪造曲线——已删）
      data: llmTrend,
      type: 'line',
      smooth: 0.4,
      lineStyle: { width: 2, color: CHART_COLORS.sky },
      itemStyle: { color: CHART_COLORS.sky },
      areaStyle: { opacity: 0.15, color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
        { offset: 0, color: CHART_COLORS.sky },
        { offset: 1, color: 'rgba(37,99,235,0.0)' }
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
  const theme = isDark.value ? DARK_CHART_THEME : CHART_THEME
  ragChart.setOption({
    ...emptyChartSubtext(ragTrend),
    tooltip: theme.tooltip,
    grid: { ...theme.grid, left: '2%', right: '3%', bottom: '3%', top: '8%' },
    xAxis: { ...theme.xAxis, type: 'category', data: ragBucketLabels(ragTrend.length), splitLine: { show: false } },
    yAxis: { ...theme.yAxis, type: 'value', min: 0, max: 100, axisLabel: { ...theme.yAxis.axisLabel, formatter: '{value}%' } },
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
    if (!adminRes.data) throw new Error('empty admin-summary payload')
    const degraded = adminRes.data.degraded === true
    summaryDegraded.value = degraded
    if (degraded) {
      // 降级全零不得覆盖历史真实 KPI；首次降级由模板显示「暂不可用」。
      summaryState.value = 'ready'
      return
    }
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
    summaryEverReady.value = true
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
    const degraded = llmRes.data.degraded === true
    llmDegraded.value = degraded
    if (degraded) {
      llmState.value = 'ready'
      return
    }
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
    llmEverReady.value = true
    llmState.value = 'ready'
    // 等本区块内容（含图表容器）渲染后再初始化，不依赖其它区块是否到齐
    await nextTick()
    if (llmStats.value.trend.length) initLlmChart()
  } catch {
    llmState.value = 'error'
  }
}

const loadRAG = async () => {
  ragState.value = 'loading'
  try {
    const ragRes = await getRAGStats()
    if (!ragRes.data) throw new Error('empty rag-stats payload')
    const degraded = ragRes.data.degraded === true
    ragDegraded.value = degraded
    if (degraded) {
      ragState.value = 'ready'
      return
    }
    ragStats.value = {
      hitRate: ragRes.data.hit_rate || 0,
      totalQueries: ragRes.data.total_queries_7d || 0,
      hits: ragRes.data.hits_with_documents || 0,
      avgDocs: ragRes.data.avg_documents_retrieved || 0,
      trend: ragRes.data.hit_rate_trend_7d || []
    }
    ragEverReady.value = true
    ragState.value = 'ready'
    await nextTick()
    if (ragHasData.value) initRagChart()
  } catch {
    ragState.value = 'error'
  }
}

const loadOrg = async () => {
  orgState.value = 'loading'
  try {
    const orgRes = await getOrgSummary()
    if (orgRes.data?.degraded === true) {
      orgDegraded.value = true
      // 首次降级不渲染 0 个组织；曾成功过则保留旧列表并给 stale 提示。
      orgState.value = orgEverReady.value ? 'ready' : 'error'
      return
    }
    if (orgRes.data?.organizations) {
      orgList.value = orgRes.data.organizations
      orgEverReady.value = true
    }
    orgDegraded.value = false
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
    // 后端查询失败会返回 HTTP 200 + {success:false}；不能当空表静默成功。
    if (auditData?.success === false) throw new Error('audit query failed')
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

watch(locale, () => {
  if (llmState.value === 'ready' && llmStats.value.trend.length) initLlmChart()
  if (ragState.value === 'ready' && ragHasData.value) initRagChart()
})

watch(isDark, () => {
  if (llmState.value === 'ready' && llmStats.value.trend.length) initLlmChart()
  if (ragState.value === 'ready') initRagChart()
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
