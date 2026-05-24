<template>
  <aside
    class="flex-shrink-0 bg-white/80 dark:bg-gray-900/80 backdrop-blur-xl border-r border-gray-200/50 dark:border-gray-800/50 transition-all duration-300 flex flex-col z-20"
    :class="[
      sidebarOpen ? 'w-72 translate-x-0' : 'w-0 -translate-x-full overflow-hidden opacity-0 md:opacity-100 md:w-0 md:translate-x-0'
    ]"
  >
    <!-- Header -->
    <div class="p-4 border-b border-gray-100/50 dark:border-gray-800/50 flex items-center justify-between bg-white/40 dark:bg-gray-900/40 backdrop-blur-md sticky top-0 z-10">
      <n-button
        block
        round
        class="flex-1 mr-2 bg-slate-600 hover:bg-slate-700 text-white border-none shadow-lg shadow-slate-500/20 transition-all duration-300"
        @click="$emit('newConversation')"
      >
        <template #icon><n-icon><AddOutline /></n-icon></template>
        {{ t('chat.newChat') }}
      </n-button>
      <n-tooltip trigger="hover">
        <template #trigger>
          <n-button quaternary circle size="small" @click="$emit('refresh')" :loading="isListLoading" class="hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors">
            <template #icon><n-icon><RefreshOutline /></n-icon></template>
          </n-button>
        </template>
        {{ t('common.refresh') }}
      </n-tooltip>
    </div>

    <!-- Conversation list -->
    <div class="flex-1 overflow-y-auto p-3 space-y-2 scrollbar-thin">
      <!-- Loading skeleton -->
      <div v-if="isListLoading && !convLoadError" class="space-y-3 px-2">
        <div v-for="i in 5" :key="i" class="flex items-center gap-3 p-3 rounded-xl bg-gray-50/50 dark:bg-gray-800/30">
          <n-skeleton circle size="small" />
          <div class="flex-1 space-y-2">
            <n-skeleton text style="width: 80%" />
            <n-skeleton text style="width: 40%" size="small" />
          </div>
        </div>
      </div>

      <!-- Error state -->
      <div v-else-if="convLoadError" class="text-center py-8 flex flex-col items-center gap-4">
        <n-result status="error" :title="t('chat.loadFailed')" :description="t('chat.loadFailedDesc')" size="small">
          <template #footer>
            <n-button size="small" type="primary" @click="$emit('refresh')">{{ t('chat.retry') }}</n-button>
          </template>
        </n-result>
      </div>

      <!-- Empty state -->
      <div v-else-if="conversations.length === 0" class="text-center text-gray-400 dark:text-gray-500 text-sm py-8 flex flex-col items-center">
        <n-icon size="48" class="mb-2 opacity-20"><ChatboxOutline /></n-icon>
        {{ t('chat.noHistory') }}
      </div>

      <div
        v-for="conv in conversations"
        :key="conv.id"
        class="group relative flex items-center p-3 rounded-xl cursor-pointer transition-all duration-300 border border-transparent"
        :class="[
          currentConversationId === conv.id
            ? 'bg-slate-50 dark:bg-slate-900/20 border-slate-100/50 dark:border-slate-800/30 text-slate-900 dark:text-slate-100 shadow-sm'
            : 'hover:bg-gray-50/80 dark:hover:bg-gray-800/40 text-gray-700 dark:text-gray-300 hover:scale-[1.02]'
        ]"
        @click="$emit('selectConversation', conv)"
      >
        <div
          v-if="currentConversationId === conv.id"
          class="absolute left-0 top-1/2 -translate-y-1/2 w-1 h-6 bg-slate-500 rounded-r-full animate-pulse shadow-[0_0_10px_rgba(99,102,241,0.5)]"
        ></div>

        <div class="flex-shrink-0 mr-3 text-lg opacity-80 transition-transform duration-300 group-hover:scale-110">
          <n-icon v-if="currentConversationId === conv.id" class="text-slate-600 dark:text-slate-400"><ChatboxEllipsesOutline /></n-icon>
          <n-icon v-else class="text-gray-400 group-hover:text-slate-500"><ChatboxOutline /></n-icon>
        </div>

        <div class="flex-1 min-w-0">
          <h3 class="text-sm font-medium truncate leading-tight mb-1.5 transition-colors duration-200">
            {{ conv.title || t('chat.defaultTitle') }}
          </h3>
          <p class="text-xs opacity-60 truncate flex justify-between items-center font-light">
            <span>{{ formatDate(conv.updated_at || conv.created_at) }}</span>
          </p>
        </div>

        <div
          class="absolute right-2 opacity-0 group-hover:opacity-100 transition-all duration-200 bg-gradient-to-l from-white via-white to-transparent dark:from-gray-800 dark:via-gray-800 pl-4 py-1"
          v-if="currentConversationId !== conv.id"
        >
          <n-popconfirm @positive-click.stop="$emit('deleteConversation', conv.id)">
            <template #trigger>
              <n-button size="tiny" quaternary circle class="text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors">
                <template #icon><n-icon><TrashOutline /></n-icon></template>
              </n-button>
            </template>
            {{ t('chat.confirmDelete') }}
          </n-popconfirm>
        </div>
      </div>
    </div>

    <div class="p-4 border-t border-gray-100 dark:border-gray-800 text-xs text-center text-gray-400 dark:text-gray-600">
      {{ t('chat.version') }}
    </div>
  </aside>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { Conversation } from '@/api/conversation'
import { format } from 'date-fns'
import {
  AddOutline, RefreshOutline, ChatboxOutline, ChatboxEllipsesOutline, TrashOutline
} from '@vicons/ionicons5'

const { t } = useI18n()

defineProps<{
  sidebarOpen: boolean
  conversations: Conversation[]
  isListLoading: boolean
  convLoadError: boolean
  currentConversationId: string | number | undefined
}>()

defineEmits<{
  newConversation: []
  refresh: []
  selectConversation: [conv: Conversation]
  deleteConversation: [id: string]
}>()

const formatDate = (dateStr: string) => {
  if (!dateStr) return ''
  try { return format(new Date(dateStr), 'MM-dd HH:mm') } catch { return dateStr }
}
</script>
