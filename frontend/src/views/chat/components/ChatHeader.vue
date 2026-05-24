<template>
  <header class="absolute top-0 left-0 right-0 z-10 px-4 py-3 flex items-center justify-between bg-white/80 dark:bg-gray-900/80 backdrop-blur-md border-b border-gray-100 dark:border-gray-800 transition-colors duration-300">
    <div class="flex items-center gap-3">
      <n-button quaternary circle size="small" @click="$emit('toggleSidebar')" class="mr-1">
        <template #icon>
          <n-icon size="20" class="text-gray-600 dark:text-gray-300">
            <MenuOutline v-if="!sidebarOpen" />
            <CloseOutline v-else />
          </n-icon>
        </template>
      </n-button>

      <div class="flex items-center space-x-2">
        <div class="bg-slate-100 dark:bg-slate-900/30 p-1.5 rounded-lg text-slate-600 dark:text-slate-400">
          <n-icon size="18"><SparklesOutline /></n-icon>
        </div>
        <div class="hidden sm:block">
          <h1 class="text-sm font-bold text-gray-800 dark:text-white leading-tight">
            {{ title || t('chat.aiAssistant') }}
          </h1>
          <div v-if="isBoundMode" class="mt-1">
            <n-tag size="tiny" round :bordered="false" type="primary">
              <template #icon>
                <n-icon :component="AttachOutline" />
              </template>
              {{ t('chat.documentModeOnly') }}
              <n-button text size="tiny" @click="$emit('unbind')" class="ml-2 hover:text-white">{{ t('chat.unbindBtn') }}</n-button>
            </n-tag>
          </div>
        </div>
      </div>
    </div>

    <div class="flex items-center space-x-2">
      <n-tooltip trigger="hover">
        <template #trigger>
          <n-button quaternary circle size="small" @click="$emit('exportChat')" class="text-gray-500 dark:text-gray-400" :disabled="!hasConversation">
            <template #icon><n-icon><DownloadOutline /></n-icon></template>
          </n-button>
        </template>
        {{ t('chat.exportChat') }}
      </n-tooltip>
      <n-tooltip trigger="hover">
        <template #trigger>
          <n-button quaternary circle size="small" @click="$emit('clearChat')" class="text-gray-500 dark:text-gray-400" :disabled="!hasConversation">
            <template #icon><n-icon><TrashOutline /></n-icon></template>
          </n-button>
        </template>
        {{ t('chat.clearChat') }}
      </n-tooltip>
    </div>
  </header>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { MenuOutline, CloseOutline, SparklesOutline, TrashOutline, AttachOutline, DownloadOutline } from '@vicons/ionicons5'

const { t } = useI18n()

defineProps<{
  sidebarOpen: boolean
  title?: string
  hasConversation: boolean
  isBoundMode: boolean
}>()

defineEmits<{
  toggleSidebar: []
  clearChat: []
  unbind: []
  exportChat: []
}>()
</script>
