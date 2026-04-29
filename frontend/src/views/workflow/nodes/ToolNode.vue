<template>
  <div
    class="vue-flow__node-tool bg-white dark:bg-gray-800 rounded-lg border-2 border-amber-400 dark:border-amber-500 p-3 min-w-[150px] transition-all"
    :class="{ 'ring-2 ring-amber-400 ring-opacity-50 animate-pulse': executing, 'ring-2 ring-amber-300': selected }"
  >
    <div class="flex items-center gap-2 mb-2">
      <div class="w-8 h-8 rounded-lg bg-amber-100 dark:bg-amber-900/30 flex items-center justify-center">
        <n-icon size="16" class="text-amber-500"><component :is="iconComponent" /></n-icon>
      </div>
      <span class="font-medium text-gray-800 dark:text-gray-100">{{ name }}</span>
      <n-spin v-if="executing" size="small" class="ml-auto" />
    </div>
    <div class="text-xs text-gray-500 dark:text-gray-400">{{ name }}</div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { NIcon, NSpin } from 'naive-ui'
import { SearchOutline, MusicalNotesOutline } from '@vicons/ionicons5'

const iconMap: Record<string, any> = {
  SearchOutline,
  MusicalNotesOutline
}

const props = defineProps<{
  data: Record<string, any>
  name: string
  icon: string
  selected?: boolean
  executing?: boolean
}>()

const iconComponent = computed(() => iconMap[props.icon] || SearchOutline)
</script>