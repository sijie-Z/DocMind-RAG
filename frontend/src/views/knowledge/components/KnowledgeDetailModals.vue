<template>
  <div>
    <n-modal
      :show="showDetail"
      @update:show="emit('update:showDetail', $event)"
      preset="card"
      class="max-w-2xl rounded-3xl shadow-2xl"
      :title="t('knowledge.viewDetail') || '文档详情'"
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
            <p class="text-sm text-gray-500">{{ currentDoc.file_name || currentDoc.filename || '-' }}</p>
          </div>
        </div>

        <n-descriptions label-placement="left" bordered :column="1" class="rounded-xl overflow-hidden">
          <n-descriptions-item label="文件大小">{{ formatFileSize(currentDoc.file_size) }}</n-descriptions-item>
          <n-descriptions-item label="上传时间">{{ formatDate(currentDoc.created_at) }}</n-descriptions-item>
          <n-descriptions-item label="处理状态">
            <n-tag :type="getStatusType(currentDoc)" size="small" round>
              {{ getStatusLabel(currentDoc) }}
            </n-tag>
          </n-descriptions-item>
          <n-descriptions-item label="上传来源">{{ currentDoc.upload_source || '知识库上传' }}</n-descriptions-item>
          <n-descriptions-item label="文件描述">{{ currentDoc.description || '暂无描述' }}</n-descriptions-item>
          <n-descriptions-item label="标签">
            <div class="flex flex-wrap gap-2">
              <n-tag v-for="tag in (currentDoc.tags || currentDoc.keywords || [])" :key="tag" size="small" round>{{ tag }}</n-tag>
              <span v-if="(currentDoc.tags || currentDoc.keywords || []).length === 0" class="text-gray-400">无</span>
            </div>
          </n-descriptions-item>
        </n-descriptions>

        <div class="bg-gray-50 dark:bg-gray-800/40 rounded-xl p-4 border border-gray-100 dark:border-gray-700/60">
          <div class="text-sm font-semibold mb-2">文档预览</div>
          <div v-if="detailLoading" class="text-xs text-gray-400">加载预览中...</div>
          <div v-else-if="currentDoc.summary" class="text-sm leading-6 text-gray-700 dark:text-gray-200 mb-3">
            {{ currentDoc.summary }}
          </div>
          <div v-if="(currentDoc.suggested_tags || []).length" class="flex flex-wrap gap-2">
            <n-tag v-for="tag in currentDoc.suggested_tags" :key="tag" size="small" :bordered="false" type="info" round>{{ tag }}</n-tag>
          </div>
          <div v-if="currentDoc.preview_content" class="max-h-56 overflow-y-auto mt-3 text-xs whitespace-pre-wrap text-gray-600 dark:text-gray-300">{{ currentDoc.preview_content }}</div>
        </div>

        <div class="flex justify-end gap-3 pt-4">
          <n-button type="primary" round @click="emit('update:showDetail', false)">关闭</n-button>
        </div>
      </div>
    </n-modal>

    <n-modal
      :show="showError"
      @update:show="emit('update:showError', $event)"
      preset="card"
      class="max-w-2xl rounded-3xl shadow-2xl"
      title="解析错误详情"
    >
      <div class="space-y-4">
        <div class="text-sm text-gray-500">以下为原始解析错误信息：</div>
        <div class="max-h-72 overflow-y-auto rounded-xl border border-red-200 dark:border-red-800/60 bg-red-50/70 dark:bg-red-900/20 p-3 text-xs leading-5 whitespace-pre-wrap break-all text-red-700 dark:text-red-300">{{ currentErrorDetail }}</div>
        <div class="flex justify-end">
          <n-button type="primary" round @click="emit('update:showError', false)">关闭</n-button>
        </div>
      </div>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
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
  getStatusType: (item: KnowledgeBase | null) => 'success' | 'warning' | 'error'
  getStatusLabel: (item: KnowledgeBase | null) => string
  formatFileSize: (bytes: number | null | undefined) => string
  formatDate: (value: string | undefined) => string
}>()

const emit = defineEmits<{
  'update:showDetail': [value: boolean]
  'update:showError': [value: boolean]
}>()

const { t } = useI18n()
void props
</script>
