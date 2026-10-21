<template>
  <div class="space-y-2">
    <!-- 策略徽标 + RRF 告警：只挂在子查询 stage 上（rag/retriever.py:696 写
        strategy、:787 写 note，随 stages[sq] 下发）；旧 UI 在顶层读恒空（B37/B38） -->
    <div v-if="debugData.strategy || debugData.note" class="flex flex-wrap items-center gap-2">
      <span
        v-if="debugData.strategy"
        class="px-1.5 py-0.5 rounded text-xs bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400"
      >
        {{ debugData.strategy }}
      </span>
      <span
        v-if="debugData.note"
        class="px-1.5 py-0.5 rounded text-xs bg-orange-50 dark:bg-orange-900/20 border border-orange-200 dark:border-orange-800/40 text-orange-600 dark:text-orange-400"
      >
        ⚠️ {{ debugData.note }}
      </span>
    </div>

    <!-- issue #189：重排路径/状态徽标 —— 降级/未启用/无候选必须显式可见，
         不再让「重排结果」标题在 noop 路径下冒充 Cross-Encoder 已生效 -->
    <div v-if="debugData.rerank" class="flex flex-wrap items-center gap-2" data-testid="rerank-status">
      <span class="px-1.5 py-0.5 rounded text-xs" :class="rerankStatusClass">
        {{ rerankStatusText }}
      </span>
      <span class="px-1.5 py-0.5 rounded text-xs bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400">
        {{ t('chat.rerankPath', { path: debugData.rerank.path }) }}
      </span>
      <span v-if="debugData.rerank.reason" class="text-xs text-gray-400">
        {{ debugData.rerank.reason }}
      </span>
    </div>

    <!-- Final Context (LLM 实际收到的上下文) -->
    <div v-if="debugData.final_context?.length" class="border border-gray-200 dark:border-gray-700 rounded overflow-hidden">
      <button
        class="w-full flex items-center justify-between px-3 py-1.5 bg-gray-100 dark:bg-gray-800 text-xs font-semibold text-gray-700 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-750 transition-colors"
        @click="openFinal = !openFinal"
      >
        <div class="flex items-center gap-2">
          <span>📄 最终 LLM 上下文</span>
          <span class="text-gray-400 font-normal">({{ debugData.final_context.length }})</span>
        </div>
        <span class="text-gray-400">{{ openFinal ? '▲' : '▼' }}</span>
      </button>
      <div v-if="openFinal" class="divide-y divide-gray-100 dark:divide-gray-800">
        <div
          v-for="item in debugData.final_context"
          :key="'ctx-' + item.rank"
          class="px-3 py-2 hover:bg-gray-50 dark:hover:bg-gray-800/50 transition-colors"
        >
          <div class="flex items-center justify-between mb-1">
            <div class="flex items-center gap-2">
              <span class="text-xs font-bold text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/20 px-1.5 py-0.5 rounded">
                #{{ item.rank }}
              </span>
              <span class="text-xs font-medium text-gray-900 dark:text-gray-100 truncate max-w-[60%]">
                {{ item.filename }}
              </span>
            </div>
            <span
              class="text-xs font-mono tabular-nums"
              :class="item.score >= 0.8 ? 'text-green-600' : item.score >= 0.5 ? 'text-blue-600' : 'text-yellow-600'"
            >
              score: {{ item.score }}
            </span>
          </div>
          <pre class="text-xs text-gray-600 dark:text-gray-400 leading-relaxed whitespace-pre-wrap font-sans bg-gray-50 dark:bg-gray-900/30 p-2 rounded mt-1 max-h-24 overflow-y-auto">{{ item.content }}</pre>
        </div>
      </div>
    </div>

    <!-- Keyword Top -->
    <DebugSection v-if="debugData.keyword_top?.length" title="🔤 BM25 关键词检索" :hits="debugData.keyword_top" :score-label="'score'" />

    <!-- Vector Top -->
    <DebugSection v-if="debugData.vector_top?.length" title="🧠 向量相似度检索" :hits="debugData.vector_top" :score-label="'score'" />

    <!-- RRF Result：分值域 ≈ 2/(k+1) ≤ 0.033，按名次着色（issue #198 ②） -->
    <DebugSection
      v-if="debugData.rrf_result?.length"
      title="🔀 RRF 融合结果"
      :hits="debugData.rrf_result"
      :score-label="'score'"
      score-mode="rrf"
    >
      <template #header-extra>
        <span class="text-gray-400">(k=60)</span>
      </template>
    </DebugSection>

    <!-- Rerank Result：展示 rerank_score 绝对分（缺失时回退排序分；#190） -->
    <DebugSection
      v-if="debugData.rerank_result?.length"
      :title="t('chat.rerankResultTitle')"
      :hits="debugData.rerank_result"
      :score-label="'rerank'"
      :score-key="'rerank_score'"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import type { RetrievalDebugData } from '@/types/chat'
import DebugSection from './DebugSection.vue'

const props = defineProps<{
  debugData: RetrievalDebugData
}>()

const { t } = useI18n()

const openFinal = ref(true)

// issue #189：状态文案/配色（ok 绿 / degraded 橙 / disabled|empty 灰）。
const rerankStatusText = computed(() => {
  const status = props.debugData.rerank?.status
  return status ? t(`chat.rerankStatus.${status}`) : ''
})

const rerankStatusClass = computed(() => {
  const status = props.debugData.rerank?.status
  if (status === 'ok') return 'bg-green-100 dark:bg-green-900/30 text-green-600 dark:text-green-400'
  if (status === 'degraded') {
    return 'bg-orange-50 dark:bg-orange-900/20 border border-orange-200 dark:border-orange-800/40 text-orange-600 dark:text-orange-400'
  }
  return 'bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400'
})
</script>
