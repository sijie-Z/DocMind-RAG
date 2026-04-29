<template>
  <div
    class="vue-flow__node-memory bg-white dark:bg-gray-800 rounded-lg border-2 border-violet-400 dark:border-violet-500 p-3 min-w-[150px] transition-all"
    :class="{ 'ring-2 ring-violet-400 ring-opacity-50 animate-pulse': executing, 'ring-2 ring-violet-300': selected }"
  >
    <div class="flex items-center gap-2 mb-2">
      <div class="w-8 h-8 rounded-lg bg-violet-100 dark:bg-violet-900/30 flex items-center justify-center">
        <n-icon size="16" class="text-violet-500"><BookOutline /></n-icon>
      </div>
      <span class="font-medium text-gray-800 dark:text-gray-100">记忆节点</span>
      <n-spin v-if="executing" size="small" class="ml-auto" />
    </div>
    <div class="text-xs text-gray-500 dark:text-gray-400 truncate">
      {{ data.memoryType === 'short_term' ? '短期记忆' : data.memoryType === 'long_term' ? '长期记忆' : '记忆管理' }}
    </div>
    <div class="text-xs text-gray-400 mt-1">
      {{ data.action === 'store' ? '存储' : '检索' }}
    </div>
  </div>
</template>

<script setup lang="ts">
import { NIcon, NSpin } from 'naive-ui'
import { BookOutline } from '@vicons/ionicons5'

defineProps<{ data: Record<string, any>; selected?: boolean; executing?: boolean }>()
</script>