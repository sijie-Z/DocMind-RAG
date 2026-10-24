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
          {{ t('chat.stopGenerating') }}
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
              {{ t('chat.fileUploading') }}
            </span>
            <span v-else-if="file.status === 'parsing'" class="ml-1 text-[10px] text-amber-500 flex items-center gap-1">
              <n-spin :size="10" stroke="currentColor" />
              {{ file.statusDetail || t('chat.fileParsing') }}
            </span>
            <span v-else-if="file.status === 'indexing'" class="ml-1 text-[10px] text-blue-500 flex items-center gap-1">
              <n-spin :size="10" stroke="currentColor" />
              {{ file.statusDetail || t('chat.fileIndexing') }}
            </span>
            <span v-else-if="file.status === 'done'" class="ml-1 text-[10px] text-green-500 flex items-center gap-1">
              <n-icon size="10"><CheckmarkCircleOutline /></n-icon>
              {{ file.statusDetail || t('chat.fileCompleted') }}
            </span>
            <span v-else-if="file.status === 'error'" class="ml-1 text-[10px] flex items-center gap-1">
              <n-icon size="12" class="flex-shrink-0"><CloseCircleOutline /></n-icon>
              <span class="truncate max-w-[80px]" :title="file.errorMsg">{{ file.errorMsg || t('chat.fileFailed') }}</span>
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
                  <span class="text-[11px] font-bold text-amber-700 dark:text-amber-400 uppercase tracking-wider">{{ t('chat.attachmentModeOnly') }}</span>
                  <n-switch v-model:value="strictMode" size="small" :round="false" />
                </div>
              </template>
              <div class="text-xs leading-relaxed">
                <div class="font-bold mb-1">{{ t('chat.strictModeTitle') }}</div>
                <div v-html="t('chat.strictModeDesc')"></div>
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
          @keydown.enter.prevent="onKeydown"
          :disabled="isLoading"
        />

        <!-- issue #475：普通 RAG 通道没有斜杠命令入口（后端仅在 Agent 分支拦截）。
             非 Agent 模式下如实提示，避免用户以为 /help 会被执行。 -->
        <div
          v-if="showSlashAgentHint"
          class="px-3 pb-1 text-[11px] text-amber-600 dark:text-amber-400"
          data-testid="slash-agent-only-hint"
        >
          {{ t('chat.slashCommandsAgentOnly') }}
        </div>

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
                <span data-testid="privacy-lock" :data-disabled="String(!orgPrivacyAllowed)">
                  <n-button
                    size="tiny"
                    quaternary
                    circle
                    :class="privacyMode && orgPrivacyAllowed ? 'text-green-600 dark:text-green-400' : 'text-gray-400'"
                  >
                    <template #icon><n-icon size="18"><ShieldCheckmarkOutline /></n-icon></template>
                  </n-button>
                </span>
              </template>
              <div class="flex flex-col gap-2">
                <div class="flex items-center gap-2">
                  <n-switch
                    v-model:value="privacyMode"
                    size="small"
                    :disabled="!orgPrivacyAllowed"
                    data-testid="privacy-switch"
                    :data-disabled="String(!orgPrivacyAllowed)"
                  />
                  <span class="text-xs">{{ t('chat.privacyMode') }}</span>
                </div>
                <span v-if="!orgPrivacyAllowed" class="text-xs text-gray-400" data-testid="privacy-policy-note">
                  {{ t('chat.orgPrivacyDisabled') }}
                </span>
              </div>
            </n-popover>

            <!-- issue #508：深度分析迁至 /agent 工作台后，传输开关恢复为纯
                 SSE/WS 选择——不再与 Agent 模式联动，没有锁定态。 -->
            <n-popover trigger="hover" placement="top">
              <template #trigger>
                <span data-testid="transport-control">
                  <n-button
                    size="tiny"
                    quaternary
                    circle
                    :class="useSSE ? 'text-amber-600 dark:text-amber-400' : 'text-gray-400'"
                  >
                    <template #icon><n-icon size="18"><RocketOutline /></n-icon></template>
                  </n-button>
                </span>
              </template>
              <div class="flex items-center gap-2">
                <n-switch
                  v-model:value="useSSE"
                  size="small"
                  data-testid="transport-switch"
                />
                <span class="text-xs">{{ t('chat.sseMode') }}</span>
              </div>
            </n-popover>

            <!-- issue #508（方案 B）：深度分析不再是 Chat 内的模式开关——Agent/PER
                 的审批、计划审批、取消与回放只在 /agent 工作台可用。入口保持一键
                 可达，当前输入由父组件经 ?prompt= 带过去；文案走 i18n
                 （chat.openAgentWorkbench，四语）。 -->
            <n-button
              size="tiny"
              quaternary
              class="text-purple-600 dark:text-purple-400"
              data-testid="agent-workbench-entry"
              @click="openAgentWorkbench"
            >
              <template #icon><n-icon size="16"><OpenOutline /></n-icon></template>
              {{ t('chat.openAgentWorkbench') }}
            </n-button>
            <span
              v-if="!orgPrivacyAllowed"
              class="text-[11px] text-gray-400 max-w-[260px] truncate"
              data-testid="privacy-policy-note-inline"
            >
              {{ t('chat.orgPrivacyDisabled') }}
            </span>

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
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  SendOutline, AttachOutline, FlashOutline, ShieldCheckmarkOutline,
  StopCircleOutline, DocumentTextOutline, DocumentOutline, CloseOutline,
  CloudUploadOutline, CheckmarkCircleOutline, CloseCircleOutline, ShieldOutline,
  RocketOutline, OpenOutline
} from '@vicons/ionicons5'
import type { AttachedFile } from '@/types/chat'

const { t } = useI18n()

const inputMessage = defineModel<string>('inputMessage', { default: '' })
const strictMode = defineModel<boolean>('strictMode', { default: false })
const privacyMode = defineModel<boolean>('privacyMode', { default: true })
const useSSE = defineModel<boolean>('useSSE', { default: false })
const useStream = defineModel<boolean>('useStream', { default: true })

/** issue #475：Chat 没有斜杠命令入口（后端仅在 Agent 分支拦截）——输入即如实提示。 */
const showSlashAgentHint = computed(
  () => inputMessage.value.trim().startsWith('/'),
)

withDefaults(defineProps<{
  isLoading: boolean
  attachedFiles: AttachedFile[]
  attachedFileIds: string[]
  connectionStatus: string
  connectionStatusText: string
  /** 组织策略是否允许隐私模式（B4/D5）；默认允许，保持既有行为 */
  orgPrivacyAllowed?: boolean
}>(), {
  orgPrivacyAllowed: true,
})

const emit = defineEmits<{
  send: []
  stopGeneration: []
  triggerFileUpload: []
  removeAttachment: [index: number]
  /** issue #508：前往 Agent 工作台（深度分析的新入口，只做导航）。 */
  openAgentWorkbench: []
}>()

const openAgentWorkbench = () => emit('openAgentWorkbench')

const onKeydown = (e: KeyboardEvent) => {
  if (!e.shiftKey) emit('send')
}
</script>
