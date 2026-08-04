<template>
  <div>
    <n-modal
      :show="showUploadModal"
      @update:show="emit('update:showUploadModal', $event)"
      :title="t('knowledge.uploadTitle')"
      preset="card"
      class="max-w-xl rounded-3xl shadow-2xl border-none"
    >
      <n-spin :show="uploading">
        <div class="space-y-6 mt-4">
          <div class="p-1 bg-gray-50 dark:bg-gray-800/50 rounded-2xl border-2 border-dashed border-gray-200 dark:border-gray-700 hover:border-blue-400 dark:hover:border-blue-500 transition-colors">
            <n-upload
              ref="uploadRef"
              :file-list="fileList"
              :on-change="handleFileChange"
              :on-remove="handleFileRemove"
              :max="1"
              accept=".pdf,.docx,.xlsx,.xls,.txt,.md,.csv"
              class="w-full"
            >
              <n-upload-dragger class="!bg-transparent !border-none">
                <div class="flex flex-col items-center gap-3 py-6">
                  <div class="w-16 h-16 rounded-full bg-blue-50 dark:bg-blue-900/20 flex items-center justify-center text-blue-500">
                    <n-icon size="36"><CloudUploadOutline /></n-icon>
                  </div>
                  <div class="text-center">
                    <n-text class="text-lg font-bold block">{{ t('knowledge.dragText') }}</n-text>
                    <n-p depth="3" class="text-sm mt-1 text-gray-400">{{ t('knowledge.dragHint') }}</n-p>
                  </div>
                </div>
              </n-upload-dragger>
            </n-upload>
          </div>

          <div class="grid grid-cols-1 gap-4">
            <div class="space-y-2">
              <label class="text-sm font-bold text-gray-700 dark:text-gray-300 ml-1">{{ t('knowledge.fileTitle') }}</label>
              <n-input v-model:value="uploadForm.title" :placeholder="t('knowledge.fileTitle')" round />
            </div>

            <n-form-item :label="t('knowledge.addTags')" path="tags">
              <n-select
                v-model:value="uploadForm.tags"
                multiple
                filterable
                tag
                :placeholder="t('knowledge.addTags')"
                :options="tagOptions"
                round
              />
            </n-form-item>

            <div class="space-y-2">
              <label class="text-sm font-bold text-gray-700 dark:text-gray-300 ml-1">{{ t('knowledge.fileDesc') }}</label>
              <n-input
                v-model:value="uploadForm.description"
                type="textarea"
                :placeholder="t('knowledge.fileDesc')"
                :rows="3"
                class="rounded-xl"
              />
            </div>
          </div>
        </div>
      </n-spin>

      <template #footer>
        <div class="flex justify-end gap-3">
          <n-button round @click="emit('update:showUploadModal', false)">{{ t('common.cancel') }}</n-button>
          <n-button
            type="primary"
            round
            :disabled="!canUpload"
            :loading="uploading"
            class="px-8 shadow-lg shadow-blue-500/20"
            @click="emit('upload')"
          >
            {{ t('knowledge.confirmUpload') }}
          </n-button>
        </div>
      </template>
    </n-modal>

    <n-drawer
      :show="showTaskList"
      @update:show="emit('update:showTaskList', $event)"
      :width="400"
      placement="right"
      class="rounded-l-3xl"
    >
      <n-drawer-content closable>
        <template #header>
          <div class="flex items-center gap-2">
            <n-icon size="20" class="text-blue-500"><ListOutline /></n-icon>
            <span class="font-bold">{{ t('knowledge.uploadList') || '上传列表' }}</span>
          </div>
        </template>

        <div class="space-y-4">
          <div v-if="activeTasks.length === 0" class="flex flex-col items-center justify-center py-12 text-gray-400">
            <n-icon size="48" class="opacity-20 mb-2"><DocumentAttachOutline /></n-icon>
            <p>{{ t('knowledge.noActiveTasks') || '暂无上传任务' }}</p>
          </div>

          <div v-for="task in activeTasks" :key="task.id" class="p-4 rounded-2xl bg-gray-50 dark:bg-gray-800/50 border border-gray-100 dark:border-gray-700/50 transition-all">
            <div class="flex items-center justify-between mb-2">
              <div class="flex items-center gap-2 overflow-hidden">
                <n-icon :class="task.status === 'error' ? 'text-red-500' : 'text-blue-500'">
                  <DocumentOutline />
                </n-icon>
                <span class="font-medium truncate text-sm" :title="task.name">{{ task.name }}</span>
              </div>
              <n-button quaternary circle size="small" @click="emit('removeTask', task.id)">
                <template #icon><n-icon><CloseOutline /></n-icon></template>
              </n-button>
            </div>

            <div class="space-y-1">
              <div class="flex justify-between text-xs mb-1">
                <span :class="{
                  'text-blue-500': task.status === 'uploading',
                  'text-orange-500': task.status === 'processing',
                  'text-green-500': task.status === 'completed',
                  'text-red-500': task.status === 'error'
                }">
                  {{ task.status === 'uploading' ? t('knowledge.status.uploading') || '上传中' :
                     task.status === 'processing' ? t('knowledge.status.processing') :
                     task.status === 'completed' ? t('knowledge.status.completed') :
                     t('knowledge.status.failed') }}
                </span>
                <span class="text-gray-400">{{ task.progress }}%</span>
              </div>
              <n-progress
                type="line"
                :percentage="task.progress"
                :status="task.status === 'error' ? 'error' : task.status === 'completed' ? 'success' : 'active'"
                :show-indicator="false"
                processing
                border-radius="4px"
                :height="6"
              />
            </div>

            <div v-if="task.error" class="mt-2 text-xs text-red-500 bg-red-50 dark:bg-red-900/20 p-2 rounded-lg">
              {{ task.error }}
            </div>
          </div>
        </div>

        <template #footer v-if="activeTasks.length > 0">
          <n-button block quaternary round @click="emit('clearFinished')">
            {{ t('knowledge.clearFinished') || '清除已完成' }}
          </n-button>
        </template>
      </n-drawer-content>
    </n-drawer>

    <div v-if="activeTasks.length > 0" class="fixed bottom-4 right-4 md:bottom-8 md:right-8 z-50">
      <n-badge
        :value="activeTasks.filter(t => t.status === 'uploading' || t.status === 'processing').length"
        :show="activeTasks.filter(t => t.status === 'uploading' || t.status === 'processing').length > 0"
      >
        <n-button
          circle
          type="primary"
          size="large"
          class="shadow-2xl h-14 w-14"
          @click="emit('update:showTaskList', true)"
        >
          <template #icon>
            <n-icon size="28" :class="{ 'animate-spin': activeTasks.some(t => t.status === 'uploading' || t.status === 'processing') }">
              <RefreshOutline v-if="activeTasks.some(t => t.status === 'uploading' || t.status === 'processing')" />
              <ListOutline v-else />
            </n-icon>
          </template>
        </n-button>
      </n-badge>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import type { UploadFileInfo } from 'naive-ui'
import {
  CloudUploadOutline,
  CloseOutline,
  DocumentAttachOutline,
  DocumentOutline,
  ListOutline,
  RefreshOutline,
} from '@vicons/ionicons5'

interface UploadForm {
  title: string
  tags: string[]
  description: string
  file?: File
}

interface UploadTask {
  id: string
  name: string
  progress: number
  status: 'uploading' | 'processing' | 'completed' | 'error'
  error?: string
}

const props = defineProps<{
  showUploadModal: boolean
  showTaskList: boolean
  uploading: boolean
  fileList: UploadFileInfo[]
  uploadForm: UploadForm
  tagOptions: readonly { readonly label: string; readonly value: string }[]
  canUpload: boolean
  activeTasks: UploadTask[]
}>()

const emit = defineEmits<{
  'update:showUploadModal': [value: boolean]
  'update:showTaskList': [value: boolean]
  'update:uploadForm': [value: UploadForm]
  'update:fileList': [value: UploadFileInfo[]]
  upload: []
  removeTask: [id: string]
  clearFinished: []
}>()

const { t } = useI18n()
const uploadRef = ref<HTMLElement | null>(null)

const handleFileChange = (options: { file: UploadFileInfo }) => {
  const file = options.file.file
  if (!file) return
  const next = {
    ...props.uploadForm,
    file,
    title: props.uploadForm.title || file.name.replace(/\.[^/.]+$/, ''),
  }
  emit('update:uploadForm', next)
  emit('update:fileList', [options.file])
}

const handleFileRemove = () => {
  emit('update:uploadForm', { ...props.uploadForm, file: undefined })
  emit('update:fileList', [])
}
</script>
