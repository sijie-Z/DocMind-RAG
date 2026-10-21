<template>
  <div class="border border-gray-200 dark:border-gray-700 rounded overflow-hidden">
    <button
      class="w-full flex items-center justify-between px-3 py-1.5 bg-gray-100 dark:bg-gray-800 text-xs font-semibold text-gray-700 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-750 transition-colors"
      @click="open = !open"
    >
      <div class="flex items-center gap-2">
        <span>{{ title }}</span>
        <slot name="header-extra" />
        <span class="text-gray-400 font-normal">({{ hits.length }})</span>
      </div>
      <span class="text-gray-400">{{ open ? '▲' : '▼' }}</span>
    </button>

    <div v-if="open" class="divide-y divide-gray-100 dark:divide-gray-800">
      <div
        v-for="(hit, i) in hits"
        :key="hit.id + i"
        class="px-3 py-2 hover:bg-gray-50 dark:hover:bg-gray-800/50 transition-colors"
      >
        <div class="flex items-center justify-between mb-0.5">
          <span class="text-xs font-medium text-gray-900 dark:text-gray-100 truncate max-w-[70%]">
            <span class="text-gray-400 mr-1">#{{ i + 1 }}</span>
            {{ hit.filename }}
          </span>
          <span
            class="text-xs font-mono tabular-nums"
            :class="scoreColorClass(displayedScore(hit), i)"
            data-testid="hit-score"
          >
            {{ scoreLabel }}: {{ displayedScore(hit) }}
          </span>
        </div>

        <div class="flex items-center gap-2 text-xs text-gray-400">
          <span v-if="hit.keyword_rank != null" class="px-1 rounded bg-blue-50 dark:bg-blue-900/20 text-blue-500">kw:#{{ hit.keyword_rank }}</span>
          <span v-if="hit.vector_rank != null" class="px-1 rounded bg-green-50 dark:bg-green-900/20 text-green-500">vec:#{{ hit.vector_rank }}</span>
          <span v-if="hit.keyword_score != null" class="text-gray-400">kw:{{ hit.keyword_score }}</span>
          <span v-if="hit.vector_score != null" class="text-gray-400">vec:{{ hit.vector_score }}</span>
          <span v-if="hit.rewrite_hits && hit.rewrite_hits > 1" class="text-orange-500">rewrite×{{ hit.rewrite_hits }}</span>
        </div>

        <p v-if="hit.snippet" class="text-xs text-gray-500 dark:text-gray-400 mt-1 leading-relaxed line-clamp-2">
          {{ hit.snippet }}
        </p>
      </div>

      <div v-if="hits.length === 0" class="px-3 py-2 text-xs text-gray-400 italic">
        无结果
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import type { DebugHit } from '@/types/chat'

const props = defineProps<{
  title: string
  hits: DebugHit[]
  scoreLabel: string
  /** issue #190：重排节展示 rerank_score（绝对分）；缺失时回退 rank/融合分。 */
  scoreKey?: 'score' | 'rank_score' | 'rerank_score'
  /**
   * issue #198 ②：分数着色口径。
   * - 'absolute'（默认）：按绝对分值分档——适用于 BM25/向量等量纲可比的分数；
   * - 'rrf'：按列表名次着色——RRF 分值域上限 2/(k+1) ≈ 0.033，与
   *   2.0/1.0/0.3 的绝对阈值毫不相干，修前所有 RRF 行都渲染成最低档灰色。
   */
  scoreMode?: 'absolute' | 'rrf'
}>()

const open = ref(true)

/** 按 score-key 取展示分：目标字段缺失（如降级/LLM 路径无 rerank_score）时回退。 */
const displayedScore = (hit: DebugHit): number => {
  const value = props.scoreKey ? hit[props.scoreKey] : undefined
  if (typeof value === 'number') return value
  return hit.score
}

/** RRF 名次分档：top3 绿 / 4-10 蓝 / 其余灰（与分值域无关，不会因量纲失真）。 */
const rrfRankColorClass = (index: number): string => {
  if (index < 3) return 'text-green-600 dark:text-green-400 font-bold'
  if (index < 10) return 'text-blue-600 dark:text-blue-400'
  return 'text-gray-400'
}

const scoreColorClass = (score: number, index: number): string => {
  if (props.scoreMode === 'rrf') return rrfRankColorClass(index)
  if (score >= 2.0) return 'text-green-600 dark:text-green-400 font-bold'
  if (score >= 1.0) return 'text-blue-600 dark:text-blue-400'
  if (score >= 0.3) return 'text-yellow-600 dark:text-yellow-400'
  return 'text-gray-400'
}
</script>
