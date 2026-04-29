<template>
  <div
    class="vue-flow__node-transform bg-white dark:bg-gray-800 rounded-lg border-2 border-teal-400 dark:border-teal-500 p-3 min-w-[150px] transition-all"
    :class="{ 'ring-2 ring-teal-400 ring-opacity-50 animate-pulse': executing, 'ring-2 ring-teal-300': selected }"
  >
    <div class="flex items-center gap-2 mb-2">
      <div class="w-8 h-8 rounded-lg bg-teal-100 dark:bg-teal-900/30 flex items-center justify-center">
        <n-icon size="16" class="text-teal-500"><SwapHorizontalOutline /></n-icon>
      </div>
      <span class="font-medium text-gray-800 dark:text-gray-100">数据转换</span>
      <n-spin v-if="executing" size="small" class="ml-auto" />
    </div>
    <div class="text-xs text-gray-500 dark:text-gray-400 truncate">
      {{ data.transformType || 'JSON提取' }}
    </div>
  </div>
</template>

<script setup lang="ts">
import { NIcon, NSpin } from 'naive-ui'
import { SwapHorizontalOutline } from '@vicons/ionicons5'

defineProps<{ data: Record<string, any>; selected?: boolean; executing?: boolean }>()
</script>