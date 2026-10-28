<template>
  <n-modal
    v-model:show="show"
    preset="card"
    :title="doc?.filename || t('chat.documentPreview')"
    class="max-w-4xl w-[90vw]"
    style="height: 80vh"
    :segmented="{ content: true, footer: true }"
  >
    <div class="h-full flex flex-col overflow-hidden">
      <div class="bg-gray-50 dark:bg-gray-900/50 p-4 rounded-xl mb-4 border border-gray-100 dark:border-gray-800 flex flex-col gap-3">
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-2">
            <n-tag size="small" :type="doc?.source === '聊天上传' ? 'info' : 'success'" round>
              {{ doc?.source || t('chat.knowledgeUpload') }}
            </n-tag>
            <n-tag v-if="doc?.file_size" size="small" quaternary round>
              {{ ((doc.file_size as number) / 1024).toFixed(2) }} KB
            </n-tag>
          </div>
          <div class="text-xs text-gray-400">{{ t('chat.uploadedAt') }}: {{ doc?.created_at ? new Date(doc.created_at).toLocaleString() : '-' }}</div>
        </div>

        <div v-if="doc?.description" class="text-xs text-gray-600 dark:text-gray-400 italic">
          <span class="font-bold not-italic text-gray-800 dark:text-gray-200">{{ t('chat.description') }}：</span>
          {{ doc.description }}
        </div>

        <div v-if="doc?.summary" class="text-xs text-gray-600 dark:text-gray-400">
          <span class="font-bold text-gray-800 dark:text-gray-200">{{ t('chat.contentSummary') }}：</span>
          {{ doc.summary }}
        </div>

        <div v-if="doc?.keywords && doc.keywords.length > 0" class="flex flex-wrap gap-1">
          <n-tag v-for="tag in doc.keywords" :key="tag" size="tiny" quaternary round># {{ tag }}</n-tag>
        </div>
        <div v-else-if="doc?.suggested_tags?.length" class="flex flex-wrap gap-1">
          <n-tag v-for="tag in doc.suggested_tags" :key="tag" size="tiny" quaternary round># {{ tag }}</n-tag>
        </div>
      </div>

      <n-alert
        v-if="content && textProvenance !== 'parser_original' && textProvenance !== 'none'"
        type="warning"
        :bordered="false"
        class="mb-3"
        data-testid="chat-preview-provenance"
      >
        {{ t('chat.previewOriginalUnavailable') }}
      </n-alert>
      <n-alert
        v-if="truncated"
        type="info"
        :bordered="false"
        class="mb-3"
        data-testid="chat-preview-truncated"
      >
        {{ t('chat.previewTruncated') }}
      </n-alert>

      <div class="flex-1 overflow-y-auto pr-2 scrollbar-thin">
        <n-skeleton v-if="loading" :repeat="10" />
        <div v-else-if="content" class="markdown-body text-sm leading-relaxed p-2 whitespace-pre-wrap">
          {{ content }}
        </div>
        <n-empty v-else :description="t('chat.noContent')" />
      </div>
      <div
        v-if="downloadError"
        data-testid="download-error"
        role="alert"
        class="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-300"
      >
        {{ downloadError }}
      </div>
    </div>

    <template #footer>
      <div class="flex justify-end gap-3">
        <n-button @click="show = false">{{ t('common.close') }}</n-button>
        <n-button
          type="primary"
          secondary
          data-testid="download-document"
          :loading="downloadLoading"
          :disabled="downloadLoading || !doc?.id"
          @click="$emit('download', doc?.id as string)"
        >
          {{ downloadLoading ? t('chat.downloadPreparing') : t('chat.downloadDocument') }}
        </n-button>
      </div>
    </template>
  </n-modal>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { DocumentContent } from '@/api/knowledge'

const { t } = useI18n()

const props = defineProps<{
  show: boolean
  loading: boolean
  doc?: {
    id?: string
    title?: string
    filename?: string
    file_name?: string
    file_type?: string
    file_size?: number
    source?: string
    created_at?: string
    description?: string
    summary?: string
    keywords?: string[]
    suggested_tags?: string[]
  }
  content: string
  /** 预览内容原文口径；缺失（旧后端/回滚）与未知值一律按不可信处理。 */
  textProvenance?: DocumentContent['text_provenance']
  /** 服务端读取有界：true 时 content 不是全文。 */
  truncated?: boolean
  downloadLoading?: boolean
  downloadError?: string
}>()

const emit = defineEmits<{
  'update:show': [value: boolean]
  download: [id?: string]
}>()

const show = computed({
  get: () => props.show,
  set: (val) => emit('update:show', val)
})
</script>
