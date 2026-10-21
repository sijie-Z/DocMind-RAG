<template>
  <div v-if="debugData" class="border-t border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/50">
    <div class="px-4 py-2 flex items-center justify-between text-xs text-gray-500 border-b border-gray-200 dark:border-gray-700">
      <span class="font-medium">🔍 检索调试面板</span>
      <div class="flex items-center gap-2">
        <span class="text-gray-400">{{ debugData.elapsed_ms ? `${debugData.elapsed_ms}ms` : '' }}</span>
        <span v-if="debugData.cache_hit" class="px-1.5 py-0.5 rounded bg-yellow-100 dark:bg-yellow-900/30 text-yellow-600 dark:text-yellow-400">
          缓存命中({{ debugData.cache_type }})
        </span>
        <button class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300" @click="expanded = !expanded">
          {{ expanded ? '收起' : '展开' }}
        </button>
        <button class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300" @click="$emit('close')">✕</button>
      </div>
    </div>

    <div v-if="expanded" class="p-3 space-y-3 text-xs font-mono max-h-96 overflow-y-auto">
      <!-- Cache hit -->
      <div v-if="debugData.cache_hit" class="p-2 rounded bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800/40">
        ⚡ 缓存命中（{{ debugData.cache_type }}），跳过检索
      </div>

      <!-- 顶层附加项：final_context 由 chat_service 挂顶层（chat_service.py:305-313）、
           rerank_result 由 pipeline 挂顶层（rag/pipeline.py:262-272）。stages 恒真值，
           旧结构把这两个顶层键的渲染塞进「无 stages」分支 → 永不显示（B39/B40） -->
      <DebugStage :debug-data="debugData" />

      <!-- 子查询明细：strategy/note 与 keyword/vector/rrf 都挂在各 stage 里
           （rag/retriever.py:696 写 strategy、:787 写 note），并非顶层键（B37/B38） -->
      <template v-if="stageEntries.length">
        <div v-for="entry in stageEntries" :key="entry[0]" class="space-y-2">
          <div class="font-semibold text-gray-700 dark:text-gray-300 bg-gray-100 dark:bg-gray-800 px-2 py-1 rounded">
            子查询: {{ entry[0] }}
          </div>
          <DebugStage :debug-data="entry[1]" />
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import type { RetrievalDebugData } from '@/types/chat'
import DebugStage from './DebugStage.vue'

const props = defineProps<{
  debugData: RetrievalDebugData | null
}>()

defineEmits<{
  close: []
}>()

const expanded = ref(true)

// stages 条目（子查询 → stage），空对象时按无子查询渲染
const stageEntries = computed(() => Object.entries(props.debugData?.stages ?? {}))
</script>
