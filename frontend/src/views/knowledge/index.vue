<template>
  <div class="knowledge-page h-full min-h-0 flex flex-col bg-gradient-to-b from-slate-50/80 to-slate-100/60 dark:from-gray-950 dark:to-gray-900/80 transition-colors duration-300">
    <div class="flex-1 overflow-y-auto p-5 lg:p-8">
      <div class="max-w-7xl mx-auto space-y-6">

        <!-- 页面标题与描述 -->
        <div class="flex flex-col md:flex-row md:items-end md:justify-between gap-4">
          <div>
            <h1 class="text-2xl md:text-3xl font-bold tracking-tight text-slate-800 dark:text-slate-100">
              {{ t('menu.knowledge') }}
            </h1>
            <p class="text-slate-500 dark:text-slate-400 mt-1 text-sm flex items-center gap-2">
              <span class="w-6 h-0.5 bg-blue-500 rounded-full"></span>
              {{ t('knowledge.subtitle') }}
            </p>
          </div>
          <div class="flex items-center gap-3 flex-wrap">
            <n-button
              v-if="selectedIds.length > 0"
              type="error"
              secondary
              round
              @click="handleBatchDelete"
            >
              <template #icon><n-icon><TrashOutline /></n-icon></template>
              {{ t('common.batchDelete') || '批量删除' }} ({{ selectedIds.length }})
            </n-button>
            <n-input
              v-model:value="searchText"
              :placeholder="t('knowledge.searchPlaceholder')"
              clearable
              round
              class="w-56 md:w-72"
              size="medium"
            >
              <template #prefix>
                <n-icon class="text-slate-400"><SearchOutline /></n-icon>
              </template>
            </n-input>
            <n-button
              type="primary"
              round
              size="medium"
              class="shadow-lg shadow-blue-500/25"
              @click="showUploadModal = true"
            >
              <template #icon><n-icon><CloudUploadOutline /></n-icon></template>
              {{ t('knowledge.upload') }}
            </n-button>
            <n-button secondary circle size="medium" @click="loadKnowledgeBases">
              <template #icon><n-icon><RefreshOutline /></n-icon></template>
            </n-button>
            <n-button secondary size="medium" @click="$router.push('/knowledge/graph')">
              <template #icon><n-icon><GitNetworkOutline /></n-icon></template>
              {{ t('router.knowledgeGraph') }}
            </n-button>
            <n-button secondary round size="medium" @click="handleRebuildLatestFailedDoc">
              最近失败文档重建
            </n-button>

          </div>
        </div>

        <!-- 统计卡片条 -->
        <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
          <div class="bg-white/90 dark:bg-gray-800/90 backdrop-blur rounded-2xl border border-slate-200/60 dark:border-gray-700/60 p-4 shadow-sm">
            <div class="flex items-center gap-3">
              <div class="w-10 h-10 rounded-xl bg-blue-100 dark:bg-blue-900/40 flex items-center justify-center">
                <n-icon size="20" class="text-blue-600 dark:text-blue-400"><DocumentTextOutline /></n-icon>
              </div>
              <div>
                <p class="text-2xl font-bold text-slate-800 dark:text-slate-100">{{ loadError ? '—' : stats.total }}</p>
                <p class="text-xs text-slate-500 dark:text-slate-400">{{ t('knowledge.stats.total') || '文档总数' }}</p>
              </div>
            </div>
          </div>
          <div class="bg-white/90 dark:bg-gray-800/90 backdrop-blur rounded-2xl border border-slate-200/60 dark:border-gray-700/60 p-4 shadow-sm">
            <div class="flex items-center gap-3">
              <div class="w-10 h-10 rounded-xl bg-emerald-100 dark:bg-emerald-900/40 flex items-center justify-center">
                <n-icon size="20" class="text-emerald-600 dark:text-emerald-400"><CheckmarkCircleOutline /></n-icon>
              </div>
              <div>
                <p class="text-2xl font-bold text-slate-800 dark:text-slate-100">{{ loadError ? '—' : stats.completed }}</p>
                <p class="text-xs text-slate-500 dark:text-slate-400">{{ t('knowledge.stats.completed') || '已索引' }}</p>
              </div>
            </div>
          </div>
          <div class="bg-white/90 dark:bg-gray-800/90 backdrop-blur rounded-2xl border border-slate-200/60 dark:border-gray-700/60 p-4 shadow-sm">
            <div class="flex items-center gap-3">
              <div class="w-10 h-10 rounded-xl bg-amber-100 dark:bg-amber-900/40 flex items-center justify-center">
                <n-icon size="20" class="text-amber-600 dark:text-amber-400"><RefreshOutline /></n-icon>
              </div>
              <div>
                <p class="text-2xl font-bold text-slate-800 dark:text-slate-100">{{ loadError ? '—' : stats.processing }}</p>
                <p class="text-xs text-slate-500 dark:text-slate-400">{{ t('knowledge.stats.processing') || '处理中' }}</p>
              </div>
            </div>
          </div>
          <div class="bg-white/90 dark:bg-gray-800/90 backdrop-blur rounded-2xl border border-slate-200/60 dark:border-gray-700/60 p-4 shadow-sm">
            <div class="flex items-center gap-3">
              <div class="w-10 h-10 rounded-xl bg-red-100 dark:bg-red-900/40 flex items-center justify-center">
                <n-icon size="20" class="text-red-600 dark:text-red-400"><CloseCircleOutline /></n-icon>
              </div>
              <div>
                <p class="text-2xl font-bold text-slate-800 dark:text-slate-100">{{ loadError ? '—' : stats.failed }}</p>
                <p class="text-xs text-slate-500 dark:text-slate-400">{{ t('knowledge.stats.failed') || '失败' }}</p>
              </div>
            </div>
          </div>
        </div>

        <!-- RAG 指标卡 -->
        <div class="flex items-center justify-between mt-3" v-if="ragMetrics">
          <div class="text-xs text-slate-500 dark:text-slate-400 space-y-1">
            <div>RAG 指标窗口：{{ metricWindowLabel }}</div>
            <div>{{ metricsUpdatedLabel }}</div>
          </div>
          <n-select
            v-model:value="metricWindow"
            :options="metricWindowOptions"
            size="small"
            class="w-40"
            @update:value="loadKnowledgeBases"
          />
        </div>
        <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mt-2" v-if="ragMetrics">
          <div class="bg-white/90 dark:bg-gray-800/90 backdrop-blur rounded-2xl border border-slate-200/60 dark:border-gray-700/60 p-4 shadow-sm">
            <p class="text-xs text-slate-500 dark:text-slate-400">检索命中率</p>
            <p class="text-xl font-bold text-slate-800 dark:text-slate-100">{{ ragMetricDisplay.hitRate }}</p>
          </div>
          <div class="bg-white/90 dark:bg-gray-800/90 backdrop-blur rounded-2xl border border-slate-200/60 dark:border-gray-700/60 p-4 shadow-sm">
            <p class="text-xs text-slate-500 dark:text-slate-400">有据回答率</p>
            <p class="text-xl font-bold text-slate-800 dark:text-slate-100">{{ ragMetricDisplay.groundedness }}</p>
          </div>
          <div class="bg-white/90 dark:bg-gray-800/90 backdrop-blur rounded-2xl border border-slate-200/60 dark:border-gray-700/60 p-4 shadow-sm">
            <p class="text-xs text-slate-500 dark:text-slate-400">平均延迟</p>
            <p class="text-xl font-bold text-slate-800 dark:text-slate-100">{{ ragMetricDisplay.latency }}</p>
          </div>
          <div class="bg-white/90 dark:bg-gray-800/90 backdrop-blur rounded-2xl border border-slate-200/60 dark:border-gray-700/60 p-4 shadow-sm">
            <p class="text-xs text-slate-500 dark:text-slate-400">缓存命中 / 重试</p>
            <p class="text-xl font-bold text-slate-800 dark:text-slate-100">{{ ragMetricDisplay.cacheRetry }}</p>
          </div>
        </div>

        <!-- Content Grid -->
        <n-spin :show="loading">
          <div v-if="loadError" class="flex flex-col items-center justify-center py-20 bg-white/50 dark:bg-gray-900/50 backdrop-blur-md rounded-3xl border border-dashed border-red-300 dark:border-red-900/30">
            <n-result
              status="500"
              :title="t('knowledge.loadFailed')"
              :description="loadErrorMsg || t('knowledge.loadFailedDesc')"
            >
              <template #footer>
                <n-button type="primary" round @click="loadKnowledgeBases">
                  {{ t('common.retry') }}
                </n-button>
              </template>
            </n-result>
          </div>

          <div v-else-if="filteredKnowledgeBases && filteredKnowledgeBases.length > 0">
            <!-- 切换视图模式按钮 (可选) -->
            <div class="flex justify-end mb-4 px-2">
               <n-radio-group v-model:value="viewMode" size="small">
                  <n-radio-button value="grid"><n-icon><GridOutline /></n-icon></n-radio-button>
                  <n-radio-button value="list"><n-icon><ListOutline /></n-icon></n-radio-button>
               </n-radio-group>
            </div>

            <div v-if="viewMode === 'grid'">
              <n-grid :x-gap="16" :y-gap="16" cols="1 s:2 m:3 l:4 xl:5" responsive="screen">
                <n-gi v-for="item in filteredKnowledgeBases" :key="item.id">
                  <div class="group relative bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 hover:border-blue-400 dark:hover:border-blue-500 transition-all duration-300 hover:shadow-lg overflow-hidden flex flex-col h-[228px]">
                    
                    <!-- 顶部状态条 -->
                    <div class="absolute top-0 left-0 right-0 h-1 bg-gray-100 dark:bg-gray-700 group-hover:bg-blue-500 transition-colors"></div>

                    <!-- 选择框 -->
                    <div class="absolute top-3 right-3 z-10 kb-card-select">
                       <n-checkbox 
                        :checked="selectedIds.includes(item.id)" 
                        @update:checked="(val: boolean) => toggleSelect(item.id, val)"
                      />
                    </div>

                    <div class="p-4 flex flex-col h-full">
                      <!-- 图标与标题 -->
                      <div class="flex items-start gap-3 mb-3">
                        <div class="w-10 h-10 rounded-lg bg-gray-50 dark:bg-gray-900 flex-shrink-0 flex items-center justify-center">
                          <n-icon size="24" :color="getFileIconColor(item.file_type)">
                            <DocumentTextOutline v-if="item.file_type === 'pdf'" />
                            <DocumentOutline v-else-if="item.file_type === 'word'" />
                            <DocumentAttachOutline v-else />
                          </n-icon>
                        </div>
                        <div class="flex-1 min-w-0">
                          <h3 class="font-medium text-gray-900 dark:text-gray-100 truncate text-sm leading-tight mb-1" :title="item.title">
                            {{ item.title }}
                          </h3>
                          <p class="text-xs text-gray-400 truncate">{{ item.file_name }}</p>
                        </div>
                      </div>

                      <!-- 标签 -->
                      <div class="flex flex-wrap gap-1 mb-auto content-start h-12 overflow-hidden">
                         <n-tag 
                          v-for="tag in item.tags.slice(0, 3)" 
                          :key="tag" 
                          size="tiny" 
                          :bordered="false" 
                          class="bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 px-1.5"
                        >
                          #{{ tag }}
                        </n-tag>
                      </div>

                      <!-- 底部信息 -->
                      <div class="mt-3 pt-3 border-t border-gray-100 dark:border-gray-700/50">
                        <div class="flex items-center justify-between">
                          <div class="flex items-center gap-2 min-w-0">
                           <n-tag 
                            size="tiny" 
                            :type="getStatusType(item)" 
                            :bordered="false"
                            round
                            class="px-0 bg-transparent"
                          >
                            <template #icon>
                              <n-icon v-if="item.status === 'completed'"><CheckmarkCircleOutline /></n-icon>
                              <n-icon v-else-if="item.status === 'parsing' || item.status === 'parsed'" class="animate-spin"><RefreshOutline /></n-icon>
                              <n-icon v-else-if="item.status === 'failed'"><AlertCircleOutline /></n-icon>
                              <n-icon v-else><TimeOutline /></n-icon>
                            </template>
                          </n-tag>
                          <span class="text-[10px] text-gray-400">{{ formatFileSize(item.file_size) }}</span>
                        </div>
                        
                        <div class="flex gap-1 kb-card-actions">
                           <n-tooltip trigger="hover">
                             <template #trigger>
                               <n-button size="tiny" quaternary circle @click.stop="viewKnowledge(item)">
                                 <template #icon><n-icon><EyeOutline /></n-icon></template>
                               </n-button>
                             </template>
                             查看
                           </n-tooltip>
                           <n-tooltip v-if="item.status === 'failed'" trigger="hover">
                             <template #trigger>
                               <n-button size="tiny" quaternary circle type="primary" @click.stop="handleRebuild(item)">
                                 <template #icon><n-icon><RefreshOutline /></n-icon></template>
                               </n-button>
                             </template>
                             重新解析
                           </n-tooltip>
                           <n-button size="tiny" quaternary circle type="error" @click.stop="deleteKnowledge(item)">
                              <template #icon><n-icon><TrashOutline /></n-icon></template>
                           </n-button>
                        </div>
                        </div>
                        <div 
                          v-if="item.status === 'failed' && item.parse_error" 
                          class="mt-2 text-[11px] leading-4 text-red-500 bg-red-50/70 dark:bg-red-900/20 border border-red-100 dark:border-red-800/50 rounded-md px-2 py-1.5 break-all line-clamp-2 cursor-pointer hover:bg-red-100 dark:hover:bg-red-900/30 transition-colors shadow-sm" 
                          :title="item.parse_error"
                          @click.stop="showErrorDetail(item.parse_error)"
                        >
                          <div class="flex items-center gap-1 mb-0.5">
                            <n-icon size="12"><AlertCircleOutline /></n-icon>
                            <span class="font-bold">解析失败 (点击查看详情)</span>
                          </div>
                          {{ item.parse_error }}
                        </div>
                      </div>
                    </div>
                  </div>
                </n-gi>
              </n-grid>
            </div>
            
            <!-- 列表模式 -->
            <div v-else class="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 overflow-x-auto">
               <table class="w-full text-left text-sm min-w-[600px]">
                  <thead class="bg-gray-50 dark:bg-gray-900/50 text-gray-500 border-b border-gray-200 dark:border-gray-700">
                    <tr>
                      <th class="p-4 w-12"><n-checkbox :checked="isAllSelected" @update:checked="toggleSelectAll" /></th>
                      <th class="p-4 font-medium">文件名称</th>
                      <th class="p-4 font-medium hidden md:table-cell">标签</th>
                      <th class="p-4 font-medium">大小</th>
                      <th class="p-4 font-medium hidden sm:table-cell">上传时间</th>
                      <th class="p-4 font-medium">状态</th>
                      <th class="p-4 font-medium text-right">操作</th>
                    </tr>
                  </thead>
                  <tbody class="divide-y divide-gray-100 dark:divide-gray-700/50">
                    <tr v-for="item in filteredKnowledgeBases" :key="item.id" class="hover:bg-blue-50/50 dark:hover:bg-blue-900/10 transition-colors group">
                       <td class="p-4"><n-checkbox :checked="selectedIds.includes(item.id)" @update:checked="(val: boolean) => toggleSelect(item.id, val)" /></td>
                       <td class="p-4">
                         <div class="flex items-center gap-3">
                            <n-icon size="20" :color="getFileIconColor(item.file_type)">
                              <DocumentTextOutline v-if="item.file_type === 'pdf'" />
                              <DocumentOutline v-else-if="item.file_type === 'word'" />
                              <DocumentAttachOutline v-else />
                            </n-icon>
                            <div>
                              <div class="font-medium text-gray-900 dark:text-gray-100">{{ item.title }}</div>
                              <div class="text-xs text-gray-400">{{ item.file_name }}</div>
                            </div>
                         </div>
                       </td>
                       <td class="p-4 hidden md:table-cell">
                          <div class="flex gap-1 flex-wrap">
                            <n-tag v-for="tag in item.tags.slice(0, 2)" :key="tag" size="tiny" :bordered="false" class="bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300">#{{ tag }}</n-tag>
                          </div>
                       </td>
                       <td class="p-4 text-gray-500">{{ formatFileSize(item.file_size) }}</td>
                       <td class="p-4 hidden sm:table-cell text-gray-500">{{ formatDate(item.created_at) }}</td>
                       <td class="p-4">
                          <div class="space-y-1">
                            <n-tag 
                              size="tiny" 
                              :type="getStatusType(item)" 
                              :bordered="false"
                              round
                            >
                              {{ getStatusLabel(item) }}
                            </n-tag>
                            <div 
                              v-if="item.status === 'failed' && item.parse_error" 
                              class="text-[11px] text-red-500 max-w-[220px] truncate cursor-pointer hover:underline decoration-dotted" 
                              :title="item.parse_error"
                              @click.stop="showErrorDetail(item.parse_error)"
                            >
                              {{ item.parse_error }}
                            </div>
                          </div>
                       </td>
                       <td class="p-4 text-right">
                          <div class="flex justify-end gap-2 kb-row-actions">
                             <n-button size="tiny" secondary circle @click="viewKnowledge(item)"><template #icon><n-icon><EyeOutline /></n-icon></template></n-button>
                             <n-button v-if="item.status === 'failed'" size="tiny" secondary circle type="primary" @click="handleRebuild(item)"><template #icon><n-icon><RefreshOutline /></n-icon></template></n-button>
                             <n-button size="tiny" secondary circle type="error" @click="deleteKnowledge(item)"><template #icon><n-icon><TrashOutline /></n-icon></template></n-button>
                          </div>
                       </td>
                    </tr>
                  </tbody>
               </table>
            </div>

            <div class="flex justify-end mt-5">
              <n-pagination
                v-model:page="pagination.page"
                v-model:page-size="pagination.pageSize"
                :item-count="pagination.itemCount"
                :page-sizes="pagination.pageSizes"
                show-size-picker
                show-quick-jumper
                @update:page="loadKnowledgeBases"
                @update:page-size="handlePageSizeChange"
              />
            </div>
          </div>

          <div v-else-if="!loading && isSearchEmpty" class="flex flex-col items-center justify-center py-24 px-6 bg-white/80 dark:bg-gray-800/50 backdrop-blur-md rounded-3xl border border-dashed border-slate-200 dark:border-gray-600/50 shadow-inner">
            <div class="w-24 h-24 rounded-2xl bg-gradient-to-br from-slate-100 to-slate-200 dark:from-gray-800 dark:to-gray-900 flex items-center justify-center mb-6 ring-4 ring-slate-100/50 dark:ring-gray-800/30">
              <n-icon size="48" class="text-slate-400 dark:text-slate-500"><SearchOutline /></n-icon>
            </div>
            <h3 class="text-xl font-bold text-slate-800 dark:text-slate-100 mb-2">{{ t('knowledge.searchEmptyTitle') }}</h3>
            <p class="text-slate-500 dark:text-slate-400 mb-8 max-w-sm text-center text-sm leading-relaxed">{{ t('knowledge.searchEmptyDesc', { query: searchText.trim() }) }}</p>
            <n-button round size="large" @click="clearSearch">
              {{ t('knowledge.clearSearch') }}
            </n-button>
          </div>

          <div v-else-if="!loading" class="flex flex-col items-center justify-center py-24 px-6 bg-white/80 dark:bg-gray-800/50 backdrop-blur-md rounded-3xl border border-dashed border-slate-200 dark:border-gray-600/50 shadow-inner">
            <div class="w-24 h-24 rounded-2xl bg-gradient-to-br from-blue-100 to-slate-100 dark:from-blue-900/30 dark:to-slate-900/30 flex items-center justify-center mb-6 ring-4 ring-blue-100/50 dark:ring-blue-900/30">
              <n-icon size="48" class="text-blue-500 dark:text-blue-400"><CloudUploadOutline /></n-icon>
            </div>
            <h3 class="text-xl font-bold text-slate-800 dark:text-slate-100 mb-2">{{ t('knowledge.empty') }}</h3>
            <p class="text-slate-500 dark:text-slate-400 mb-8 max-w-sm text-center text-sm leading-relaxed">{{ t('knowledge.emptyDesc') }}</p>
            <n-button type="primary" round size="large" class="px-8 shadow-lg shadow-blue-500/20" @click="showUploadModal = true">
              {{ t('knowledge.uploadFirst') }}
            </n-button>
          </div>
        </n-spin>

        <KnowledgeUploadPanel
          v-model:show-upload-modal="showUploadModal"
          v-model:show-task-list="showTaskList"
          v-model:upload-form="uploadForm"
          v-model:file-list="fileList"
          :uploading="uploading"
          :tag-options="tagOptions"
          :can-upload="canUpload"
          :active-tasks="activeTasks"
          @upload="uploadFile"
          @remove-task="removeUploadTask"
          @clear-finished="clearFinishedTasks"
        />
        <KnowledgeDetailModals
          v-model:show-detail="showDetailModal"
          v-model:show-error="showErrorModal"
          :current-doc="currentDoc"
          :current-error-detail="currentErrorDetail"
          :detail-loading="detailLoading"
          :get-file-icon-color="getFileIconColor"
          :get-status-type="getStatusType"
          :get-status-label="getStatusLabel"
          :format-file-size="formatFileSize"
          :format-date="formatDate"
        />

      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { NIcon, NButton, NTag } from 'naive-ui'
import { useDialog } from 'naive-ui'
import { useDedupedMessage } from '@/utils/message'
import { useI18n } from 'vue-i18n'
import {
  SearchOutline,
  CloudUploadOutline,
  RefreshOutline,
  DocumentTextOutline,
  DocumentOutline,
  DocumentAttachOutline,
  EyeOutline,
  TrashOutline,
  CheckmarkCircleOutline,
  AlertCircleOutline,
  CloseCircleOutline,
  ListOutline,
  GridOutline,
  GitNetworkOutline,
  TimeOutline
} from '@vicons/ionicons5'
import type { UploadFileInfo } from 'naive-ui'
import { 
  getKnowledgeBases, 
  getDocumentDetail,
  getDocumentContent,
  uploadKnowledgeBase, 
  deleteKnowledgeBase,
  batchDeleteKnowledgeBases,
  rebuildKnowledgeBase
} from '@/api/knowledge'
import type { KnowledgeBase, KnowledgeBaseListResponse, DocumentDetail, DocumentContent } from '@/api/knowledge'
import { formatDate } from '@/utils/format'
import { apiUnwrap } from '@/utils/apiUnwrap'
import { getErrorMessage } from '@/utils/apiError'
import { getRagMetrics, type RagMetrics } from '@/api/rag'
import { useUserStore } from '@/stores/user'
import { docIconColor, docStatusLabel, docStatusType, isInProgressDoc, computeDocStats } from './docDisplay'
import KnowledgeUploadPanel from './components/KnowledgeUploadPanel.vue'
import KnowledgeDetailModals from './components/KnowledgeDetailModals.vue'

interface UploadForm {
  title: string
  tags: string[]
  description: string
  file?: File
}

const message = useDedupedMessage()
const dialog = useDialog()
const { t } = useI18n()
const userStore = useUserStore()
const loading = ref(false)
const loadError = ref(false)
const loadErrorMsg = ref('')
const uploading = ref(false)
const searchText = ref('')
const viewMode = ref<'grid' | 'list'>('grid') // 视图模式
const knowledgeBases = ref<KnowledgeBase[]>([])
const selectedIds = ref<string[]>([])
const showUploadModal = ref(false)
const showTaskList = ref(false)
// ✅ 新增：控制详情弹窗
const showDetailModal = ref(false)
const showErrorModal = ref(false)
const currentErrorDetail = ref('')
const currentDoc = ref<KnowledgeBase | null>(null)
const detailLoading = ref(false)
// 连续打开不同文档时，慢的旧请求不得覆盖新文档的详情/预览（证据对应
// 用户最后点击的文档，而不是最后返回的请求）。
let previewRequestSeq = 0

const fileList = ref<UploadFileInfo[]>([])

interface UploadTask {
  id: string
  name: string
  progress: number
  status: 'uploading' | 'processing' | 'completed' | 'error'
  error?: string
}

const activeTasks = ref<UploadTask[]>([])

/**
 * 进行中上传的 AbortController（taskId → controller）。
 * 放在响应式状态之外：AbortController 是宿主对象，经 Vue proxy 包裹后调用
 * abort() 会触发 brand check 异常；任务对象本身保持纯数据，便于渲染与断言。
 */
const uploadControllers = new Map<string, AbortController>()

const isTaskInFlight = (task: UploadTask) => task.status === 'uploading' || task.status === 'processing'

/**
 * 上传列表「×」语义（E9/B7）：
 * - 进行中（uploading / processing）→ 真取消：AbortController 中止请求，任务移除
 *   并提示「已取消上传」；传输阶段中止后后端拿不到完整表单，文档不会落库；
 * - 已终态（completed / error）→ 仅从列表移除（不改动任何请求）。
 */
const removeUploadTask = (id: string) => {
  const task = activeTasks.value.find(t => t.id === id)
  const controller = uploadControllers.get(id)
  if (task && isTaskInFlight(task) && controller) {
    controller.abort()
    message.info(t('knowledge.uploadCancelled'))
  }
  uploadControllers.delete(id)
  activeTasks.value = activeTasks.value.filter(t => t.id !== id)
}

const clearFinishedTasks = () => {
  activeTasks.value = activeTasks.value.filter(
    t => t.status === 'uploading' || t.status === 'processing'
  )
}
const ragMetrics = ref<RagMetrics | null>(null)
const metricWindow = ref<number>(300)
const metricWindowOptions = [
  { label: '最近5分钟', value: 300 },
  { label: '最近1小时', value: 3600 },
  { label: '累计', value: 0 }
]
const metricsUpdatedAt = ref<string>('')

const metricWindowLabel = computed(() => {
  if (metricWindow.value === 0) return '累计'
  if (metricWindow.value >= 3600) return `最近 ${Math.floor(metricWindow.value / 3600)} 小时`
  return `最近 ${Math.floor(metricWindow.value / 60)} 分钟`
})

const metricsUpdatedLabel = computed(() => {
  return metricsUpdatedAt.value ? `最近刷新：${metricsUpdatedAt.value}` : '最近刷新：--'
})

const ragMetricDisplay = computed(() => {
  const m = ragMetrics.value
  if (!m) {
    return {
      hitRate: '--',
      groundedness: '--',
      latency: '--',
      cacheRetry: '--'
    }
  }
  const hasRequests = (m.retrieval_total || 0) > 0
  return {
    hitRate: hasRequests ? `${(m.hit_rate * 100).toFixed(1)}%` : '--',
    groundedness: hasRequests ? `${(m.groundedness * 100).toFixed(1)}%` : '--',
    latency: hasRequests ? `${m.avg_latency_ms.toFixed(0)} ms` : '--',
    cacheRetry: hasRequests ? `${m.cache_hit} / ${m.retry_total}` : '--'
  }
})

const pagination = ref({
  page: 1,
  pageSize: 12,
  itemCount: 0,
  pageSizes: [12, 24, 48]
})

const uploadForm = ref<UploadForm>({
  title: '',
  tags: [],
  description: ''
})

// Polling for processing items
let pollTimer: number | null = null

const startPolling = () => {
  if (pollTimer) return
  pollTimer = window.setInterval(async () => {
    const hasProcessing = knowledgeBases.value.some(item => isInProgressDoc(item.status))
    if (hasProcessing) {
      await loadKnowledgeBases()
    } else {
      stopPolling()
    }
  }, 5000) // Poll every 5 seconds
}

const stopPolling = () => {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

const tagOptions = computed(() => [
  { label: t('knowledge.tags.tech'), value: '技术文档' },
  { label: t('knowledge.tags.manual'), value: '产品手册' },
  { label: t('knowledge.tags.rules'), value: '规章制度' },
  { label: t('knowledge.tags.training'), value: '培训资料' },
  { label: t('knowledge.tags.other'), value: '其他' }
] as const)

const filteredKnowledgeBases = computed(() => knowledgeBases.value)

/** E14/C1 空态分流：搜索无命中 ≠ 空库（前者给清除搜索，后者才劝上传）。 */
const isSearchEmpty = computed(
  () => searchText.value.trim().length > 0 && knowledgeBases.value.length === 0
)

const stats = computed(() => computeDocStats(knowledgeBases.value))

const isAllSelected = computed(() => {
  return filteredKnowledgeBases.value.length > 0 && selectedIds.value.length === filteredKnowledgeBases.value.length
})

const toggleSelect = (id: string, checked: boolean) => {
  if (checked) {
    if (!selectedIds.value.includes(id)) {
      selectedIds.value.push(id)
    }
  } else {
    selectedIds.value = selectedIds.value.filter(i => i !== id)
  }
}

const toggleSelectAll = (checked: boolean) => {
  if (checked) {
    selectedIds.value = filteredKnowledgeBases.value.map(i => i.id)
  } else {
    selectedIds.value = []
  }
}

const handleBatchDelete = () => {
  if (selectedIds.value.length === 0) return
  dialog.warning({
    title: t('common.confirm') || '确认删除',
    content: t('knowledge.batchDeleteConfirm', { count: selectedIds.value.length }),
    positiveText: t('common.confirm'),
    negativeText: t('common.cancel'),
    onPositiveClick: async () => {
      try {
        // A2：补 organization_id（后端 knowledge.py:642-646 对非超管强校验
        // body.organization_id == 有效组织，缺省必 403）。口径照 search P1-2：
        // 从 user store 取 currentOrgId；无组织时省略，由后端解析（超管语义）。
        await batchDeleteKnowledgeBases(selectedIds.value, userStore.currentOrgId, { silent: true })
        message.success(t('common.deleteSuccess'))
        selectedIds.value = []
        await loadKnowledgeBases()
      } catch (error: unknown) {
        message.error(t('common.deleteFailed') + '：' + getErrorMessage(error, t('common.tryAgain')))
      }
    }
  })
}

const canUpload = computed(() => {
  return !!uploadForm.value.file
})

const loadKnowledgeBases = async () => {
  loading.value = true
  loadError.value = false
  try {
    const params = {
      page: pagination.value.page,
      page_size: pagination.value.pageSize,
      search: searchText.value
    }
    const [response, metricsResp] = await Promise.all([
      // E14/D1：页面自呈现错误（页内错误态 + 重试），请求静默，拦截器不再重复 toast
      getKnowledgeBases(params, { silent: true }),
      getRagMetrics(metricWindow.value).catch(() => null)
    ])

    // 宽容解包：GET /chat/metrics 为 {success, data: <RagMetrics>} 信封
    const m = metricsResp ? apiUnwrap<RagMetrics | null>(metricsResp) : null
    if (m) {
      // ⚡ 核心修复：如果后端返回的是全 0 数据，且 knowledgeBases 有数据，则不更新 metrics 以免误导
      if (m.retrieval_total > 0 || m.hit_rate > 0 || m.groundedness > 0) {
          ragMetrics.value = m
      } else {
          // 如果确实没数据，也要显示 0 而不是 NaN，前端已做 toFixed 处理
          ragMetrics.value = m
      }
      metricsUpdatedAt.value = new Date().toLocaleTimeString()
    }

    // T11：宽容解包（口径见 @/types/common.ts）——对现行双层响应与旧手写解包
    // 恒等（契约定在 api/__tests__/knowledgeShapeContract.test.ts）
    const data = apiUnwrap<KnowledgeBaseListResponse>(response)
    if (data) {
        if (Array.isArray(data.data)) {
            knowledgeBases.value = data.data
            // Start polling if any item is in a non-terminal state
            if (knowledgeBases.value.some(item => isInProgressDoc(item.status))) {
              startPolling()
            }
        } else {
            knowledgeBases.value = []
        }
        pagination.value.itemCount = data.total || 0
        
        // ⚡ 自动修复：如果后端统计接口挂了，用当前页数据补齐基本统计
    } else {
        knowledgeBases.value = []
        pagination.value.itemCount = 0
    }
  } catch (error: unknown) {
    // E14/D2：加载/搜索失败必须清空旧结果——陈旧卡片与统计不得继续冒充当前数据
    loadError.value = true
    loadErrorMsg.value = getErrorMessage(error, t('knowledge.loadFailedDesc'))
    knowledgeBases.value = []
    pagination.value.itemCount = 0
    selectedIds.value = []
  } finally {
    loading.value = false
  }
}

const handlePageSizeChange = (size: number) => {
  pagination.value.pageSize = size
  pagination.value.page = 1
  loadKnowledgeBases()
}

const uploadFile = async () => {
  if (!uploadForm.value.file) return

  const file = uploadForm.value.file
  const taskId = Math.random().toString(36).substring(7)
  const controller = new AbortController()
  uploadControllers.set(taskId, controller)

  const newTask: UploadTask = {
    id: taskId,
    name: file.name,
    progress: 0,
    status: 'uploading'
  }

  activeTasks.value.unshift(newTask)
  showTaskList.value = true
  showUploadModal.value = false
  uploading.value = true

  try {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('title', uploadForm.value.title || file.name)
    formData.append('tags', JSON.stringify(uploadForm.value.tags))
    formData.append('description', uploadForm.value.description)
    // 移除硬编码的 organization_id，后端会从 Form 默认值或 User Context 获取

    const res = await uploadKnowledgeBase(formData, (progressEvent) => {
      const progress = progressEvent.total ? Math.round((progressEvent.loaded * 100) / progressEvent.total) : 0
      const task = activeTasks.value.find(t => t.id === taskId)
      if (task) {
        task.progress = progress
        if (progress === 100) {
          task.status = 'processing'
        }
      }
    }, controller.signal)

    // 取消竞态：用户已点「×」但响应恰好先到 —— 不再报成功、不再刷新列表
    if (controller.signal.aborted) return

    // issue #301 批 D（#23）：后端在「文件已保存、但处理任务启动失败」时返回
    // success=false（doc 同步已标记 failed）。此前无条件 message.success —— 失败
    // 被伪造成「上传成功」。
    const uploadBody = res.data as { success?: boolean; message?: string }
    const task = activeTasks.value.find(t => t.id === taskId)
    if (uploadBody?.success === false) {
      if (task) {
        task.status = 'error'
        task.error = uploadBody.message || t('knowledge.uploadFailed')
      }
      message.warning(uploadBody.message || t('knowledge.uploadFailed'))
    } else {
      message.success(t('knowledge.uploadSuccess'))
      if (task) {
        task.status = 'completed'
        task.progress = 100
      }
    }

    uploadForm.value = {
      title: '',
      tags: [],
      description: ''
    }
    fileList.value = []

    await loadKnowledgeBases()
  } catch (error: unknown) {
    // 用户主动取消不是失败：任务已由 removeUploadTask 移除并提示，这里不再误报
    if (!controller.signal.aborted) {
      const errMsg = getErrorMessage(error, t('knowledge.unknownError'))
      const task = activeTasks.value.find(t => t.id === taskId)
      if (task) {
        task.status = 'error'
        task.error = errMsg
      }
      message.error(t('knowledge.uploadFailed') + '：' + errMsg)
    }
  } finally {
    uploadControllers.delete(taskId)
    // 并发上传：uploading 反映「是否仍有在途任务」，取消 A 不得清掉 B 的进行中状态
    uploading.value = uploadControllers.size > 0
  }
}

// ✅ 核心修复：现在点击按钮会正确打开弹窗
const viewKnowledge = async (item: KnowledgeBase) => {
  const requestSeq = ++previewRequestSeq
  const ownsRequest = () => requestSeq === previewRequestSeq
  detailLoading.value = true
  currentDoc.value = {
    ...item,
    upload_source: '知识库上传'
  } as KnowledgeBase
  showDetailModal.value = true
  try {
    const [detailRes, contentRes] = await Promise.all([
      getDocumentDetail(item.id, { silent: true }),
      getDocumentContent(item.id, { silent: true })
    ])
    if (!ownsRequest()) return
    // T11：宽容解包（与既有 detailRes/contentRes 手写解包恒等）
    const detail = apiUnwrap<DocumentDetail>(detailRes) || {}
    const content = apiUnwrap<DocumentContent>(contentRes) || {}
    currentDoc.value = {
      ...item,
      ...detail,
      tags: item.tags || detail.keywords || [],
      upload_source: detail.upload_source || (String(detail.description || '').includes('来自聊天') ? '聊天上传' : '知识库上传'),
      summary: content.summary || detail.description || '',
      suggested_tags: content.suggested_tags || [],
      preview_content: (content.content || '').slice(0, 1500),
      // 预览原文口径透传给详情弹窗；legacy/mixed 与截断必须在界面上可见，
      // 不能把可能带生成摘要的文本静默当作原文。客户端只展示前 1500 字，
      // 超出同样属于截断，不能因为服务端 truncated=false 就冒充完整预览。
      preview_provenance: content.text_provenance,
      preview_truncated: content.truncated === true || (content.content || '').length > 1500
    } as KnowledgeBase
  } catch {
    if (ownsRequest()) message.warning(t('knowledge.previewLoadFailed'))
  } finally {
    if (ownsRequest()) detailLoading.value = false
  }
}

const showErrorDetail = (errorText: string) => {
  currentErrorDetail.value = errorText || '未知解析错误'
  showErrorModal.value = true
}

const handleRebuild = async (item: KnowledgeBase) => {
  try {
    await rebuildKnowledgeBase(item.id, { silent: true })
    message.success(t('knowledge.rebuildSuccess') || '开始重新构建索引')
    await loadKnowledgeBases()
  } catch (error: unknown) {
    message.error((t('knowledge.rebuildFailed') || '重新构建失败') + '：' + getErrorMessage(error, t('common.tryAgain')))
  }
}

const handleRebuildLatestFailedDoc = async () => {
  const failed = knowledgeBases.value.find(i => i.status === 'failed')
  if (!failed) {
    message.info(t('knowledge.noFailedDocs'))
    return
  }
  try {
    await rebuildKnowledgeBase(failed.id, { silent: true })
    message.success(t('knowledge.rebuildSubmitted', { title: failed.title || failed.file_name }))
    await loadKnowledgeBases()
  } catch (error: unknown) {
    message.error(t('knowledge.rebuildSubmitFailed') + '：' + getErrorMessage(error, t('knowledge.unknownError')))
  }
}

const deleteKnowledge = (item: KnowledgeBase) => {
  dialog.warning({
    title: t('common.confirm') || '确认删除',
    content: t('knowledge.deleteConfirm'),
    positiveText: t('common.confirm'),
    negativeText: t('common.cancel'),
    onPositiveClick: async () => {
      try {
        await deleteKnowledgeBase(item.id, { silent: true })
        message.success(t('common.deleteSuccess'))
        await loadKnowledgeBases()
      } catch (error: unknown) {
        message.error(t('knowledge.deleteFailed') + '：' + getErrorMessage(error, t('common.tryAgain')))
      }
    }
  })
}

const formatFileSize = (bytes: number | null | undefined): string => {
  if (bytes === null || bytes === undefined || Number.isNaN(Number(bytes))) return '-'
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const safe = Number(bytes)
  const i = Math.floor(Math.log(safe) / Math.log(k))
  return parseFloat((safe / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
}

// 幽灵「降级索引」标识已整体移除（B17）：后端不存在任何"降级为关键词索引"的
// 字段或 parse_error 哨兵（models/document.py 无 vector_index_status 列；
// parse_error 全部写入点复核：worker/doc_processor.py:291,393 清空、:409 写
// 任意异常串；成功路径必为 None）——原 isFallbackIndexed 恒为 false，行内
// 「重试向量化」与工具栏「重试全部降级文档」从未真实可达（控件形同虚设）。
// 产品缺口：如需"降级索引"语义，须后端先落字段，前端再接真值。

const getStatusType = (item: KnowledgeBase | null) => docStatusType(item?.status)

const getStatusLabel = (item: KnowledgeBase | null) => docStatusLabel(item?.status)

const getFileIconColor = (fileType: string): string => docIconColor(fileType)

onMounted(() => {
  loadKnowledgeBases()
})

let searchTimer: number | null = null
/** 清除搜索时立即回载，跳过防抖 watcher 的重复触发。 */
let skipSearchWatch = false
watch(searchText, () => {
  if (skipSearchWatch) {
    skipSearchWatch = false
    return
  }
  if (searchTimer) {
    clearTimeout(searchTimer)
  }
  searchTimer = window.setTimeout(() => {
    pagination.value.page = 1
    loadKnowledgeBases()
  }, 300)
})

const clearSearch = () => {
  if (searchTimer) {
    clearTimeout(searchTimer)
    searchTimer = null
  }
  skipSearchWatch = true
  searchText.value = ''
  pagination.value.page = 1
  void loadKnowledgeBases()
}

watch(metricWindow, () => {
  loadKnowledgeBases()
})

watch(filteredKnowledgeBases, (list) => {
  const validIds = new Set(list.map(item => item.id))
  selectedIds.value = selectedIds.value.filter(id => validIds.has(id))
})

onUnmounted(() => {
  stopPolling()
  if (searchTimer) {
    clearTimeout(searchTimer)
  }
})
</script>

<style scoped>
/* Responsive: stat cards stack vertically on mobile (already grid-cols-2 md:grid-cols-4) */
/* Responsive: table horizontal scroll on mobile */
@media (max-width: 767px) {
  .knowledge-page .overflow-x-auto {
    -webkit-overflow-scrolling: touch;
  }
}

/* I1 操作排显隐（键盘 / 触屏可达，鼠标行为不回退，同 ChatMessages T11 模式）：
   - 默认隐藏，保持界面干净；
   - 键盘聚焦 :has(:focus-visible) 常显（媒体查询之外，任何设备都生效）；
     不用 :focus-within——鼠标点击按钮也会聚焦，移开后操作排会滞留；
   - hover 显隐只在支持 hover 的设备上生效，触屏不依赖它；
   - 无 hover 能力的设备（触屏）常显，保证操作可点可达；
   - 语义类替代旧的属性子串选择器（工具类一改即静默失效）。 */
.kb-card-actions {
  opacity: 0;
  transform: translateX(0.5rem);
  transition: opacity 0.2s ease, transform 0.2s ease;
}
.kb-card-select {
  opacity: 0;
  transition: opacity 0.2s ease;
}
.kb-row-actions {
  opacity: 0;
  transition: opacity 0.2s ease;
}
.kb-card-actions:has(:focus-visible),
.kb-card-select:has(:focus-visible),
.kb-row-actions:has(:focus-visible) {
  opacity: 1;
}
.kb-card-actions:has(:focus-visible) {
  transform: translateX(0);
}
@media (hover: hover) {
  .group:hover .kb-card-actions,
  .group:hover .kb-card-select,
  .group:hover .kb-row-actions {
    opacity: 1;
  }
  .group:hover .kb-card-actions {
    transform: translateX(0);
  }
}
@media (hover: none) {
  .kb-card-actions {
    opacity: 1;
    transform: none;
  }
  .kb-card-select {
    opacity: 1;
  }
  .kb-row-actions {
    opacity: 1;
  }
}
</style>
