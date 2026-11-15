<template>
  <div class="h-full overflow-y-auto p-6 lg:p-8 bg-gray-50 dark:bg-gray-950">
    <div class="max-w-4xl mx-auto space-y-6">
      <!-- T9 定位三件套：面包屑（DocMind / 系统关于） -->
      <PageBreadcrumb />

      <!-- Header -->
      <div class="flex items-center gap-4 mb-2">
        <div class="w-12 h-12 rounded-2xl bg-emerald-500 flex items-center justify-center text-white font-bold text-xl shadow-lg shadow-emerald-500/25">D</div>
        <div>
          <h1 class="text-2xl font-bold text-gray-900 dark:text-white">DocMind</h1>
          <p class="text-sm text-gray-500 dark:text-gray-400">{{ t('about.tagline') }}</p>
        </div>
        <div class="ml-auto">
          <n-tag v-if="appVersion" type="success" round size="small">v{{ appVersion }}</n-tag>
        </div>
      </div>

      <!-- System Info -->
      <div class="bg-white dark:bg-gray-800 rounded-2xl border border-gray-100 dark:border-gray-700 shadow-sm overflow-hidden">
        <div class="px-6 py-4 border-b border-gray-100 dark:border-gray-700">
          <h2 class="text-base font-semibold text-gray-900 dark:text-white">{{ t('about.systemInfo') }}</h2>
        </div>
        <div class="p-6">
          <n-descriptions :column="1" bordered label-placement="left" class="rounded-xl overflow-hidden">
            <n-descriptions-item :label="t('about.labels.product')">{{ t('about.values.product') }}</n-descriptions-item>
            <n-descriptions-item :label="t('about.labels.architecture')">{{ t('about.values.architecture') }}</n-descriptions-item>
            <n-descriptions-item :label="t('about.labels.backend')">{{ t('about.values.backend') }}</n-descriptions-item>
            <n-descriptions-item :label="t('about.labels.frontend')">{{ t('about.values.frontend') }}</n-descriptions-item>
            <n-descriptions-item :label="t('about.labels.aiEngine')">{{ t('about.values.aiEngine') }}</n-descriptions-item>
            <n-descriptions-item :label="t('about.labels.agentSystem')">{{ t('about.values.agentSystem') }}</n-descriptions-item>
            <n-descriptions-item :label="t('about.labels.security')">{{ t('about.values.security') }}</n-descriptions-item>
          </n-descriptions>
        </div>
      </div>

      <!-- Architecture Highlights -->
      <div class="bg-white dark:bg-gray-800 rounded-2xl border border-gray-100 dark:border-gray-700 shadow-sm overflow-hidden">
        <div class="px-6 py-4 border-b border-gray-100 dark:border-gray-700">
          <h2 class="text-base font-semibold text-gray-900 dark:text-white">{{ t('about.highlightsTitle') }}</h2>
        </div>
        <div class="p-6 grid grid-cols-1 md:grid-cols-2 gap-4">
          <div v-for="item in highlights" :key="item.title" class="p-4 rounded-xl bg-gray-50 dark:bg-gray-700/30">
            <h3 class="text-sm font-semibold text-gray-900 dark:text-white mb-2">{{ item.title }}</h3>
            <p class="text-xs text-gray-500 dark:text-gray-400 leading-relaxed">{{ item.desc }}</p>
          </div>
        </div>
      </div>

      <!-- Data Flow -->
      <div class="bg-white dark:bg-gray-800 rounded-2xl border border-gray-100 dark:border-gray-700 shadow-sm overflow-hidden">
        <div class="px-6 py-4 border-b border-gray-100 dark:border-gray-700">
          <h2 class="text-base font-semibold text-gray-900 dark:text-white">{{ t('about.dataFlowTitle') }}</h2>
        </div>
        <div class="p-6">
          <div class="flex flex-wrap items-center gap-2 text-sm">
            <span v-for="(step, i) in dataFlow" :key="i" class="flex items-center gap-2">
              <span class="px-3 py-1.5 rounded-lg bg-emerald-50 dark:bg-emerald-900/20 text-emerald-700 dark:text-emerald-300 font-medium text-xs">{{ step }}</span>
              <span v-if="i < dataFlow.length - 1" class="text-gray-300 dark:text-gray-600">&rarr;</span>
            </span>
          </div>
        </div>
      </div>

      <!-- Footer -->
      <div class="text-center py-4">
        <p class="text-xs text-gray-400 dark:text-gray-500">
          {{ t('about.footer') }}
        </p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { NDescriptions, NDescriptionsItem, NTag } from 'naive-ui'
import { useI18n } from 'vue-i18n'
import PageBreadcrumb from '@/components/common/PageBreadcrumb.vue'
import { useAppVersion } from '@/composables/useAppVersion'

// ISS-2 数字对账：版本号动态读取——后端 APP_VERSION 是唯一版本真源，
// 经 GET /health 会话级缓存读取（见 useAppVersion）；拿不到时空态不显示。
const appVersion = useAppVersion()

const { t } = useI18n()

// 词条驱动 + computed：切语言后亮点/数据流实时更新（ECharts 式一次性
// 求值的常量不会响应 locale）
const highlights = computed(() => [
  {
    title: t('about.highlights.hybridSearch.title'),
    desc: t('about.highlights.hybridSearch.desc'),
  },
  {
    title: t('about.highlights.agentArchitecture.title'),
    desc: t('about.highlights.agentArchitecture.desc'),
  },
  {
    title: t('about.highlights.semanticCache.title'),
    desc: t('about.highlights.semanticCache.desc'),
  },
  {
    title: t('about.highlights.circuitBreaker.title'),
    desc: t('about.highlights.circuitBreaker.desc'),
  },
  {
    title: t('about.highlights.multiTenantRbac.title'),
    desc: t('about.highlights.multiTenantRbac.desc'),
  },
  {
    title: t('about.highlights.realtimeStreaming.title'),
    desc: t('about.highlights.realtimeStreaming.desc'),
  },
])

const dataFlow = computed(() => [
  t('about.flow.upload'),
  'MinIO + DB',
  'Kafka',
  'Worker',
  t('about.flow.parseChunk'),
  t('about.flow.embed'),
  'Elasticsearch',
  t('about.flow.userQuestion'),
  t('about.flow.hybridSearch'),
  t('about.flow.rerank'),
  'DeepSeek LLM',
  t('about.flow.streamAnswer'),
])
</script>
