<template>
  <div
    class="vue-flow__node-llm bg-white dark:bg-gray-800 rounded-lg border-2 p-3 min-w-[180px] transition-all"
    :class="[borderColor, { 'ring-2 ring-opacity-50 animate-pulse': executing, 'ring-2': selected }]"
    :style="{ '--tw-ring-color': ringColor }"
  >
    <div class="flex items-center gap-2 mb-2">
      <div class="w-8 h-8 rounded-lg flex items-center justify-center" :class="bgColor">
        <n-icon size="16" :class="textColor"><ChatbubblesOutline /></n-icon>
      </div>
      <span class="font-medium text-gray-800 dark:text-gray-100">{{ model }}</span>
      <n-spin v-if="executing" size="small" class="ml-auto" />
    </div>
    <div class="text-xs text-gray-500 dark:text-gray-400 line-clamp-2">{{ data.systemPrompt || 'AI 助手' }}</div>
    <div class="mt-2 text-xs text-gray-400">温度: {{ (data.temperature || 0.7).toFixed(1) }}</div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { NIcon, NSpin } from 'naive-ui'
import { ChatbubblesOutline } from '@vicons/ionicons5'

const props = defineProps<{
  data: Record<string, any>
  model: string
  color: 'green' | 'blue' | 'orange'
  selected?: boolean
  executing?: boolean
}>()

const colorMap = {
  green: { border: 'border-green-400 dark:border-green-500', bg: 'bg-green-100 dark:bg-green-900/30', text: 'text-green-500', ring: '#4ade80' },
  blue: { border: 'border-blue-400 dark:border-blue-500', bg: 'bg-blue-100 dark:bg-blue-900/30', text: 'text-blue-500', ring: '#60a5fa' },
  orange: { border: 'border-orange-400 dark:border-orange-500', bg: 'bg-orange-100 dark:bg-orange-900/30', text: 'text-orange-500', ring: '#fb923c' }
}

const borderColor = computed(() => colorMap[props.color].border)
const bgColor = computed(() => colorMap[props.color].bg)
const textColor = computed(() => colorMap[props.color].text)
const ringColor = computed(() => colorMap[props.color].ring)
</script>