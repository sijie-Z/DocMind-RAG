<template>
  <footer class="absolute bottom-0 left-0 right-0 p-4 md:p-6 bg-gradient-to-t from-gray-50 via-gray-50 to-transparent dark:from-gray-950 dark:via-gray-950 transition-colors duration-300">
    <div class="max-w-3xl mx-auto relative">
      <!-- Stop generation button -->
      <div v-if="isLoading" class="absolute -top-12 left-1/2 -translate-x-1/2">
        <n-button
          round
          size="small"
          class="shadow-lg border-red-200 dark:border-red-900/30 text-red-500 bg-white/90 dark:bg-gray-800/90 backdrop-blur-sm"
          @click="$emit('stopGeneration')"
        >
          <template #icon><n-icon><StopCircleOutline /></n-icon></template>
          {{ t('chat.stopGenerating') || '停止生成' }}
        </n-button>
      </div>

      <div class="bg-white dark:bg-gray-800 rounded-2xl shadow-[0_8px_30px_rgb(0,0,0,0.08)] border border-gray-200 dark:border-gray-700 p-2 transition-all duration-300 focus-within:shadow-[0_8px_30px_rgb(0,0,0,0.12)] focus-within:border-primary-300 dark:focus-within:border-primary-700">
        <!-- Attached files -->
        <div v-if="attachedFiles.length > 0" class="flex flex-wrap gap-2 px-3 pt-2 items-center">
          <div v-for="(file, index) in attachedFiles" :key="index"
            class="flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-xs transition-all duration-300"
            :class="file.status === 'done'
              ? 'bg-green-50 border-green-200 text-green-600 dark:bg-green-900/30 dark:border-green-800/50 dark:text-green-400'
              : file.status === 'error'
               ? 'bg-red-50 border-red-200 text-red-600 dark:bg-red-900/30 dark:border-red-800/50 dark:text-red-400'
               : 'bg-gray-50 border-gray-200 text-gray-500 dark:bg-gray-800 dark:border-gray-700 animate-pulse'"
          >
            <n-icon size="14">
              <DocumentTextOutline v-if="file.name.endsWith('.pdf')" />
              <DocumentOutline v-else />
            </n-icon>
            <span class="truncate max-w-[120px]" :title="file.name">{{ file.name }}</span>

            <span v-if="file.status === 'uploading'" class="ml-1 text-[10px] text-gray-400 flex items-center gap-1">
              <n-icon size="10"><CloudUploadOutline /></n-icon>
              上传中...
            </span>
            <span v-else-if="file.status === 'parsing'" class="ml-1 text-[10px] text-amber-500 flex items-center gap-1">
              <n-spin :size="10" stroke="currentColor" />
              {{ file.statusDetail || '解析中...' }}
            </span>
            <span v-else-if="file.status === 'indexing'" class="ml-1 text-[10px] text-blue-500 flex items-center gap-1">
              <n-spin :size="10" stroke="currentColor" />
              {{ file.statusDetail || '索引中...' }}
            </span>
            <span v-else-if="file.status === 'done'" class="ml-1 text-[10px] text-green-500 flex items-center gap-1">
              <n-icon size="10"><CheckmarkCircleOutline /></n-icon>
              {{ file.statusDetail || '已完成' }}
            </span>
            <span v-else-if="file.status === 'error'" class="ml-1 text-[10px] flex items-center gap-1">
              <n-icon size="12" class="flex-shrink-0"><CloseCircleOutline /></n-icon>
              <span class="truncate max-w-[80px]" :title="file.errorMsg">{{ file.errorMsg || '解析失败' }}</span>
            </span>

            <button @click.stop="$emit('removeAttachment', index)" class="ml-1 hover:text-red-500 transition-colors p-0.5 rounded-full hover:bg-red-50 dark:hover:bg-red-900/20">
              <n-icon size="14"><CloseOutline /></n-icon>
            </button>
          </div>

          <div v-if="attachedFileIds.length > 0" class="ml-auto pr-2 flex items-center gap-2">
            <n-tooltip trigger="hover">
              <template #trigger>
                <div class="flex items-center gap-2 bg-amber-50 dark:bg-amber-900/20 px-3 py-1.5 rounded-lg border border-amber-200 dark:border-amber-800/50 hover:border-amber-400 dark:hover:border-amber-600 transition-colors">
                  <n-icon size="14" class="text-amber-600 dark:text-amber-400"><ShieldOutline /></n-icon>
                  <span class="text-[11px] font-bold text-amber-700 dark:text-amber-400 uppercase tracking-wider">仅附件模式</span>
                  <n-switch v-model:value="strictMode" size="small" :round="false" />
                </div>
              </template>
              <div class="text-xs leading-relaxed">
                <div class="font-bold mb-1">强约束模式</div>
                <div>开启后，AI 将 <span class="text-red-500 font-bold">完全基于</span> 你上传的文档内容回答，<span class="text-red-500 font-bold">不会</span> 使用全局知识库或其他外部知识。</div>
              </div>
            </n-tooltip>
          </div>
        </div>

        <!-- Input area -->
        <n-input
          v-model:value="inputMessage"
          type="textarea"
          :placeholder="t('chat.inputPlaceholder')"
          :autosize="{ minRows: 1, maxRows: 6 }"
          :bordered="false"
          class="text-base px-2 py-1 !bg-transparent text-gray-900 dark:text-gray-100 placeholder-gray-400 dark:placeholder-gray-500"
          @keydown.enter.prevent="$emit('send')"
          :disabled="isLoading"
        />

        <div class="flex justify-between items-center mt-2 px-2 pb-1">
          <div class="flex gap-2 items-center">
            <n-tooltip trigger="hover">
              <template #trigger>
                <n-button size="tiny" quaternary circle class="text-gray-400 hover:text-primary-600 dark:hover:text-primary-400" @click="$emit('triggerFileUpload')">
                  <template #icon><n-icon size="18"><AttachOutline /></n-icon></template>
                </n-button>
              </template>
              {{ t('chat.uploadFile') }}
            </n-tooltip>

            <span
              class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border"
              :class="connectionStatus === 'connected'
                ? 'bg-emerald-50 dark:bg-emerald-900/20 text-emerald-600 dark:text-emerald-400 border-emerald-200 dark:border-emerald-800/50'
                : (connectionStatus === 'connecting'
                ? 'bg-amber-50 dark:bg-amber-900/20 text-amber-600 dark:text-amber-400 border-amber-200 dark:border-amber-800/50'
                : 'bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 border-red-200 dark:border-red-800/50')"
            >
              <span
                class="w-2 h-2 rounded-full shrink-0"
                :class="connectionStatus === 'connected' ? 'bg-emerald-500 animate-pulse' : connectionStatus === 'connecting' ? 'bg-amber-500 animate-pulse' : 'bg-red-500'"
              ></span>
              {{ useSSE ? 'SSE' : '' }}{{ connectionStatusText }}
            </span>

            <n-popover trigger="hover" placement="top">
              <template #trigger>
                <n-button size="tiny" quaternary circle :class="useStream ? 'text-primary-600 dark:text-primary-400' : 'text-gray-400'">
                  <template #icon><n-icon size="18"><FlashOutline /></n-icon></template>
                </n-button>
              </template>
              <div class="flex items-center gap-2">
                <n-switch v-model:value="useStream" size="small" />
                <span class="text-xs">{{ t('chat.streamResponse') }}</span>
              </div>
            </n-popover>

            <n-popover trigger="hover" placement="top">
              <template #trigger>
                <n-button size="tiny" quaternary circle :class="privacyMode ? 'text-green-600 dark:text-green-400' : 'text-gray-400'">
                  <template #icon><n-icon size="18"><ShieldCheckmarkOutline /></n-icon></template>
                </n-button>
              </template>
              <div class="flex items-center gap-2">
                <n-switch v-model:value="privacyMode" size="small" />
                <span class="text-xs">隐私模式 (自动脱敏敏感信息)</span>
              </div>
            </n-popover>

            <n-popover trigger="hover" placement="top">
              <template #trigger>
                <n-button size="tiny" quaternary circle :class="useSSE ? 'text-amber-600 dark:text-amber-400' : 'text-gray-400'">
                  <template #icon><n-icon size="18"><RadioButtonOnOutline /></n-icon></template>
                </n-button>
              </template>
              <div class="flex items-center gap-2">
                <n-switch v-model:value="useSSE" size="small" />
                <span class="text-xs">SSE模式 (企业防火墙兼容)</span>
              </div>
            </n-popover>
          </div>

          <div class="flex items-center gap-3">
            <n-button
              type="primary"
              round
              size="small"
              :loading="isLoading"
              :disabled="!inputMessage.trim() || isLoading"
              @click="$emit('send')"
              class="!px-4"
            >
              <template #icon>
                <n-icon><SendOutline /></n-icon>
              </template>
              {{ t('chat.send') }}
            </n-button>
          </div>
        </div>
      </div>

      <div class="text-center mt-3 text-xs text-gray-400 dark:text-gray-500 hidden sm:block">
        {{ t('chat.disclaimer') }}
      </div>
    </div>
  </footer>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import {
  SendOutline, AttachOutline, FlashOutline, ShieldCheckmarkOutline, RadioButtonOnOutline,
  StopCircleOutline, DocumentTextOutline, DocumentOutline, CloseOutline,
  CloudUploadOutline, CheckmarkCircleOutline, CloseCircleOutline, ShieldOutline
} from '@vicons/ionicons5'
import type { AttachedFile } from '@/types/chat'

const { t } = useI18n()

const inputMessage = defineModel<string>('inputMessage', { default: '' })
const strictMode = defineModel<boolean>('strictMode', { default: false })
const privacyMode = defineModel<boolean>('privacyMode', { default: true })
const useSSE = defineModel<boolean>('useSSE', { default: false })
const useStream = defineModel<boolean>('useStream', { default: true })

defineProps<{
  isLoading: boolean
  attachedFiles: AttachedFile[]
  attachedFileIds: string[]
  connectionStatus: string
  connectionStatusText: string
}>()

defineEmits<{
  send: []
  stopGeneration: []
  triggerFileUpload: []
  removeAttachment: [index: number]
}>()
</script>
