<template>
  <div>
    <n-modal
      :show="showDetail"
      @update:show="emit('update:showDetail', $event)"
      preset="card"
      class="knowledge-detail-modal max-w-2xl rounded-3xl shadow-2xl"
      data-testid="knowledge-detail-modal"
      :title="t('knowledge.viewDetail')"
    >
      <div v-if="currentDoc" class="space-y-6">
        <div class="flex items-center gap-4 p-4 bg-gray-50 dark:bg-gray-800/50 rounded-2xl">
          <div class="p-3 bg-white dark:bg-gray-700 rounded-xl shadow-sm">
            <n-icon size="32" :color="getFileIconColor(currentDoc.file_type)">
              <DocumentTextOutline v-if="currentDoc.file_type === 'pdf'" />
              <DocumentOutline v-else />
            </n-icon>
          </div>
          <div>
            <h3 class="font-bold text-lg">{{ currentDoc.title }}</h3>
            <p class="text-sm text-gray-500 dark:text-gray-400">{{ currentDoc.file_name || currentDoc.filename || '-' }}</p>
          </div>
        </div>

        <n-descriptions label-placement="left" bordered :column="1" class="rounded-xl overflow-hidden">
          <n-descriptions-item :label="t('knowledge.fileSize')">{{ formatFileSize(currentDoc.file_size) }}</n-descriptions-item>
          <n-descriptions-item :label="t('knowledge.table.uploadTime')">{{ formatDateTime(currentDoc.created_at) }}</n-descriptions-item>
          <n-descriptions-item :label="t('knowledge.processingStatus')">
            <n-tag :type="getStatusType(currentDoc)" size="small" round>
              {{ getStatusLabel(currentDoc) }}
            </n-tag>
          </n-descriptions-item>
          <n-descriptions-item :label="t('knowledge.uploadSource')">{{ uploadSourceText }}</n-descriptions-item>
          <n-descriptions-item :label="t('knowledge.fileDescription')">{{ currentDoc.description || t('common.noDescription') }}</n-descriptions-item>
          <n-descriptions-item :label="t('knowledge.tableTags')">
            <div class="flex flex-wrap gap-2">
              <n-tag v-for="tag in (currentDoc.tags || currentDoc.keywords || [])" :key="tag" size="small" round>{{ tag }}</n-tag>
              <span v-if="(currentDoc.tags || currentDoc.keywords || []).length === 0" class="text-gray-500 dark:text-gray-400">{{ t('common.none') }}</span>
            </div>
          </n-descriptions-item>
        </n-descriptions>

        <div class="bg-gray-50 dark:bg-gray-800/40 rounded-xl p-4 border border-gray-100 dark:border-gray-700/60">
          <div class="text-sm font-semibold mb-2">{{ t('knowledge.documentPreviewTitle') }}</div>
          <n-alert
            v-if="currentDoc.preview_content && currentDoc.preview_provenance !== 'parser_original' && currentDoc.preview_provenance !== 'none'"
            type="warning"
            :bordered="false"
            class="mb-3"
            data-testid="knowledge-preview-provenance"
          >
            {{ t('knowledge.previewOriginalUnavailable') }}
          </n-alert>
          <n-alert
            v-if="currentDoc.preview_truncated"
            type="info"
            :bordered="false"
            class="mb-3"
            data-testid="knowledge-preview-truncated"
          >
            {{ t('knowledge.previewTruncated') }}
          </n-alert>
          <div v-if="detailLoading" class="text-xs text-gray-500 dark:text-gray-400">{{ t('knowledge.previewLoading') }}</div>
          <div v-else-if="currentDoc.summary" class="text-sm leading-6 text-gray-700 dark:text-gray-200 mb-3">
            {{ currentDoc.summary }}
          </div>
          <div v-if="(currentDoc.suggested_tags || []).length" class="flex flex-wrap gap-2">
            <n-tag v-for="tag in currentDoc.suggested_tags" :key="tag" size="small" :bordered="false" type="info" round>{{ tag }}</n-tag>
          </div>
          <div v-if="currentDoc.preview_content" class="max-h-56 overflow-y-auto mt-3 text-xs whitespace-pre-wrap text-gray-600 dark:text-gray-300">{{ currentDoc.preview_content }}</div>
        </div>

        <div class="flex justify-end gap-3 pt-4">
          <n-button
            type="primary"
            round
            :disabled="!currentDoc?.id"
            data-testid="knowledge-open-document-page"
            @click="emit('openDocument', currentDoc?.id)"
          >
            {{ t('knowledge.documentDetail.openFromPreview') }}
          </n-button>
          <n-button round @click="emit('update:showDetail', false)">{{ t('common.close') }}</n-button>
        </div>
      </div>
    </n-modal>

    <n-modal
      :show="showError"
      @update:show="emit('update:showError', $event)"
      preset="card"
      class="knowledge-detail-modal max-w-2xl rounded-3xl shadow-2xl"
      :title="t('knowledge.parseErrorTitle')"
    >
      <div class="space-y-4">
        <div class="text-sm text-gray-500 dark:text-gray-400">{{ t('knowledge.parseErrorRawHint') }}</div>
        <div class="max-h-72 overflow-y-auto rounded-xl border border-red-200 dark:border-red-800/60 bg-red-50/70 dark:bg-red-900/20 p-3 text-xs leading-5 whitespace-pre-wrap break-all text-red-700 dark:text-red-300">{{ currentErrorDetail }}</div>
        <div class="flex justify-end">
          <n-button type="primary" round @click="emit('update:showError', false)">{{ t('common.close') }}</n-button>
        </div>
      </div>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { DocumentOutline, DocumentTextOutline } from '@vicons/ionicons5'
import type { KnowledgeBase } from '@/api/knowledge'

const props = defineProps<{
  showDetail: boolean
  showError: boolean
  currentDoc: KnowledgeBase | null
  currentErrorDetail: string
  detailLoading: boolean
  getFileIconColor: (type: string) => string
  getStatusType: (item: KnowledgeBase | null) => 'success' | 'warning' | 'error' | 'info' | 'default'
  getStatusLabel: (item: KnowledgeBase | null) => string
  formatFileSize: (bytes: number | null | undefined) => string
  formatDateTime: (value: string | undefined) => string
}>()

const emit = defineEmits<{
  'update:showDetail': [value: boolean]
  'update:showError': [value: boolean]
  openDocument: [id?: string]
}>()

const { t } = useI18n()

const uploadSourceText = computed(() => {
  const doc = props.currentDoc
  if (!doc) return ''
  if (doc.upload_source_code === 'chat') return t('knowledge.documentDetail.uploadSourceChat')
  if (doc.upload_source_code === 'knowledge') return t('knowledge.documentDetail.uploadSourceKnowledge')
  return doc.upload_source || t('knowledge.documentDetail.uploadSourceKnowledge')
})
</script>

<style>
/* 弹窗 teleport 到 body 后 scoped 选择器失效：用弹窗根类约束触控高度。 */
@media (max-width: 1024px) {
  .knowledge-detail-modal .n-button {
    min-height: 44px;
  }
  .knowledge-detail-modal .n-card-header__close {
    width: 44px;
    height: 44px;
  }
}
</style>
