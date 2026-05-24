<template>
  <div
    ref="chatContainer"
    class="flex-1 overflow-y-auto px-4 pt-20 pb-40 scroll-smooth relative"
    @scroll="$emit('scroll')"
  >
    <div class="max-w-3xl mx-auto space-y-6">
      <!-- Welcome screen -->
      <div v-if="messages.length === 0" class="flex flex-col items-center justify-center mt-16 md:mt-24 text-center animate-fade-in">
        <div class="relative mb-10">
          <div class="w-28 h-28 bg-gradient-to-br from-slate-500 via-blue-500 to-blue-600 rounded-3xl shadow-2xl shadow-slate-500/30 flex items-center justify-center transform hover:scale-105 transition-transform duration-300">
            <n-icon size="52" class="text-white drop-shadow">
              <SparklesOutline />
            </n-icon>
          </div>
          <div class="absolute -inset-2 bg-slate-400/20 rounded-[1.5rem] blur-xl -z-10"></div>
        </div>
        <h2 class="text-3xl md:text-4xl font-extrabold tracking-tight text-slate-900 dark:text-white mb-3">
          {{ t('chat.welcome') }}
        </h2>
        <p class="text-base md:text-lg text-slate-500 dark:text-slate-400 max-w-md mb-10 font-light leading-relaxed">
          {{ t('chat.welcomeDesc') }}
        </p>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 w-full max-w-2xl">
          <button
            v-for="(suggestion, index) in suggestions"
            :key="index"
            class="p-4 md:p-5 bg-white/80 dark:bg-gray-800/80 backdrop-blur border border-slate-200/60 dark:border-gray-700/60 rounded-2xl text-left hover:border-slate-400/60 hover:shadow-lg hover:shadow-slate-500/10 transition-all duration-300 group relative overflow-hidden"
            @click="$emit('useSuggestion', suggestion)"
          >
            <div class="absolute top-0 right-0 p-3 opacity-0 group-hover:opacity-100 transition-all duration-300 transform translate-x-2 group-hover:translate-x-0">
              <n-icon class="text-slate-500" size="18"><ArrowForwardOutline /></n-icon>
            </div>
            <h3 class="font-semibold text-sm md:text-base text-slate-800 dark:text-slate-200 group-hover:text-slate-600 dark:group-hover:text-slate-400 mb-1.5 tracking-wide">
              {{ suggestion.title }}
            </h3>
            <p class="text-xs md:text-sm text-slate-500 dark:text-slate-400 line-clamp-2 leading-relaxed font-light">
              {{ suggestion.desc }}
            </p>
          </button>
        </div>
      </div>

      <!-- Message list -->
      <DynamicScroller
        :items="messages"
        :min-item-size="120"
        key-field="id"
        class="flex-1"
        v-slot="{ item: message, index, active }"
      >
        <DynamicScrollerItem
          :item="message"
          :active="active"
          :index="index"
          :data-index="index"
        >
          <div
            class="flex gap-4 animate-slide-in-bottom group mb-4"
            :class="{ 'flex-row-reverse': message.messageType === 'user' }"
          >
            <div
              class="flex-shrink-0 w-10 h-10 rounded-2xl flex items-center justify-center overflow-hidden shadow-lg mt-1 transform transition-all duration-300 group-hover:scale-110 group-hover:shadow-blue-500/20"
              :class="message.messageType === 'user' ? 'bg-gradient-to-br from-gray-800 to-black' : 'bg-white dark:bg-gray-800 border border-gray-100 dark:border-gray-700'"
            >
              <n-avatar
                v-if="message.messageType === 'user'"
                :src="userAvatar"
                :fallback-src="'/default-avatar.png'"
                size="small"
                class="bg-transparent"
              />
              <n-icon v-else size="20" class="text-slate-600 dark:text-slate-400"><SparklesOutline /></n-icon>
            </div>

            <div class="max-w-[85%] relative group/bubble">
              <div
                class="px-6 py-4 rounded-[1.5rem] shadow-sm text-[15px] leading-relaxed tracking-wide transition-all duration-300"
                :class="message.messageType === 'user'
                  ? 'bg-gradient-to-br from-slate-600 to-blue-600 text-white rounded-tr-md'
                  : 'bg-white dark:bg-gray-800 text-gray-800 dark:text-gray-200 rounded-tl-md border border-gray-100 dark:border-gray-700'"
              >
                <Markdown v-if="message.messageType === 'assistant'" :content="message.content" />
                <span
                  v-if="message.messageType === 'assistant' && isLoading && index === messages.length - 1"
                  class="inline-block w-0.5 h-4 bg-slate-500 dark:bg-slate-400 ml-0.5 align-middle animate-blink"
                />
                <div v-else class="whitespace-pre-wrap">{{ message.content }}</div>

                <div v-if="message.sources && message.sources.length > 0" class="mt-3 pt-3 border-t border-gray-100 dark:border-gray-700">
                  <div class="text-xs text-gray-500 dark:text-gray-400 mb-1.5 font-medium">{{ t('chat.sourcesLabel') }}</div>
                  <div class="flex flex-wrap gap-1.5">
                    <n-popover
                      v-for="(source, idx) in message.sources"
                      :key="idx"
                      trigger="hover"
                      placement="top"
                      :delay="200"
                      :duration="200"
                    >
                      <template #trigger>
                        <n-tag
                          size="tiny"
                          round
                          :bordered="false"
                          class="cursor-pointer bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 hover:bg-blue-100 dark:hover:bg-blue-900/50 transition-colors"
                        >
                          <template #icon>
                            <n-icon size="10" class="mr-0.5"><DocumentTextOutline /></n-icon>
                          </template>
                          [{{ idx + 1 }}] {{ source.filename || source.title || t('chat.sourceDoc') }}
                        </n-tag>
                      </template>
                      <div class="max-w-sm p-2">
                        <div class="text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                          {{ source.filename || t('chat.unknownDoc') }}
                        </div>
                        <div class="text-xs text-gray-500 dark:text-gray-400 leading-relaxed line-clamp-6">
                          {{ source.snippet || source.text || t('chat.noPreview') }}
                        </div>
                        <div v-if="source.score" class="mt-1.5 text-[10px] text-gray-400">
                          {{ t('chat.relevanceScore', { score: (source.score * 100).toFixed(0) }) }}
                        </div>
                      </div>
                    </n-popover>
                  </div>
                </div>
              </div>

              <div v-if="message.messageType === 'assistant'" class="flex items-center gap-1 mt-2 opacity-0 group-hover/bubble:opacity-100 transition-opacity duration-200">
                <n-tooltip trigger="hover">
                  <template #trigger>
                    <n-button size="tiny" quaternary circle
                      :class="message.feedback === 1 ? 'text-green-600 bg-green-50 dark:bg-green-900/30' : 'text-gray-400 hover:text-green-600'"
                      @click="$emit('feedback', message, 1)"
                    >
                      <template #icon><n-icon><ThumbsUpOutline /></n-icon></template>
                    </n-button>
                  </template>
                  {{ t('chat.useful') }}
                </n-tooltip>
                <n-tooltip trigger="hover">
                  <template #trigger>
                    <n-button size="tiny" quaternary circle
                      :class="message.feedback === -1 ? 'text-red-600 bg-red-50 dark:bg-red-900/30' : 'text-gray-400 hover:text-red-600'"
                      @click="$emit('feedback', message, -1)"
                    >
                      <template #icon><n-icon><ThumbsDownOutline /></n-icon></template>
                    </n-button>
                  </template>
                  {{ t('chat.notUseful') }}
                </n-tooltip>
                <n-button size="tiny" quaternary circle class="text-gray-400" @click="$emit('copy', message.content)">
                  <template #icon><n-icon><CopyOutline /></n-icon></template>
                </n-button>
                <n-tooltip v-if="index === messages.length - 1 && message.messageType === 'assistant'" trigger="hover">
                  <template #trigger>
                    <n-button size="tiny" quaternary circle class="text-gray-400 hover:text-blue-600"
                      @click="$emit('regenerate', message)">
                      <template #icon><n-icon><RefreshOutline /></n-icon></template>
                    </n-button>
                  </template>
                  {{ t('chat.regenerate') }}
                </n-tooltip>
              </div>
            </div>
          </div>
        </DynamicScrollerItem>
      </DynamicScroller>

      <!-- Loading indicator -->
      <div v-if="isLoading" class="flex gap-4 animate-pulse">
        <div class="w-10 h-10 bg-white dark:bg-gray-800 rounded-2xl border border-gray-200 dark:border-gray-700 flex items-center justify-center shadow-sm">
          <n-icon size="20" class="text-primary-600 dark:text-primary-400 animate-spin-slow"><SparklesOutline /></n-icon>
        </div>
        <div class="flex items-center h-10">
          <div class="flex space-x-1.5">
            <div class="w-2 h-2 bg-slate-400 rounded-full animate-bounce" style="animation-delay: 0ms"></div>
            <div class="w-2 h-2 bg-slate-400 rounded-full animate-bounce" style="animation-delay: 150ms"></div>
            <div class="w-2 h-2 bg-slate-400 rounded-full animate-bounce" style="animation-delay: 300ms"></div>
          </div>
        </div>
      </div>
    </div>

    <!-- Back to bottom button -->
    <transition name="fade">
      <div v-if="showBackToBottom" class="absolute bottom-32 left-1/2 -translate-x-1/2 z-20">
        <n-button
          round
          size="small"
          class="shadow-xl bg-white/80 dark:bg-gray-800/80 backdrop-blur-md border border-gray-200 dark:border-gray-700 text-primary-600 dark:text-primary-400 font-bold"
          @click="$emit('scrollToBottom', 'smooth', true)"
        >
          <template #icon><n-icon><ArrowDownOutline /></n-icon></template>
          {{ t('chat.backToBottom') }}
        </n-button>
      </div>
    </transition>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { SparklesOutline, ArrowForwardOutline, ArrowDownOutline, ThumbsUpOutline, ThumbsDownOutline, CopyOutline, DocumentTextOutline, RefreshOutline } from '@vicons/ionicons5'
import { NPopover } from 'naive-ui'
import Markdown from '@/components/common/Markdown.vue'
import type { ChatMessage } from '@/types/chat'

const { t } = useI18n()

defineProps<{
  messages: ChatMessage[]
  isLoading: boolean
  showBackToBottom: boolean
  userAvatar?: string
  suggestions: { title: string; desc: string }[]
}>()

defineEmits<{
  scroll: []
  scrollToBottom: [behavior: ScrollBehavior, force?: boolean]
  useSuggestion: [suggestion: { title: string; desc: string }]
  feedback: [msg: ChatMessage, feedback: number]
  copy: [text: string]
  regenerate: [msg: ChatMessage]
}>()
</script>

<style scoped>
@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}
.animate-blink {
  animation: blink 0.8s step-end infinite;
}
</style>
