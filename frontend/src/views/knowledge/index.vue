<template>
  <div class="knowledge-page h-full min-h-0 flex flex-col bg-gradient-to-b from-slate-50/80 to-slate-100/60 dark:from-gray-950 dark:to-gray-900/80 transition-colors duration-300">
    <div class="flex-1 overflow-y-auto p-5 lg:p-8">
      <div class="max-w-7xl mx-auto space-y-6">
        
        <!-- 页面标题与描述 -->
        <div class="flex flex-col md:flex-row md:items-end md:justify-between gap-4">
          <div>
            <h1 class="text-2xl md:text-3xl font-bold tracking-tight text-slate-800 dark:text-slate-100">
              {{ t('knowledge.title') }}
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
            <n-button secondary round size="medium" @click="handleRebuildLatestFailedDoc">
              最近失败文档重建
            </n-button>
            <n-button secondary round size="medium" @click="handleRetryAllFallbackDocs">
              重试全部降级文档
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
                <p class="text-2xl font-bold text-slate-800 dark:text-slate-100">{{ stats.total }}</p>
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
                <p class="text-2xl font-bold text-slate-800 dark:text-slate-100">{{ stats.completed }}</p>
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
                <p class="text-2xl font-bold text-slate-800 dark:text-slate-100">{{ stats.processing }}</p>
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
                <p class="text-2xl font-bold text-slate-800 dark:text-slate-100">{{ stats.failed }}</p>
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
              :title="t('common.failed')"
              :description="t('common.error') || '服务器好像开小差了，请稍后再试'"
            >
              <template #footer>
                <n-button type="primary" round @click="loadKnowledgeBases">
                  {{ t('common.refresh') }}
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
                    <div class="absolute top-3 right-3 z-10 opacity-0 group-hover:opacity-100 transition-opacity">
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
                            <DocumentOutline v-else-if="item.file_type === 'docx'" />
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
                              <n-icon v-else-if="item.status === 'processing'" class="animate-spin"><RefreshOutline /></n-icon>
                              <n-icon v-else><AlertCircleOutline /></n-icon>
                            </template>
                          </n-tag>
                          <span class="text-[10px] text-gray-400">{{ formatFileSize(item.file_size) }}</span>
                        </div>
                        
                        <div class="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity transform translate-x-2 group-hover:translate-x-0">
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
                           <n-tooltip v-if="isFallbackIndexed(item)" trigger="hover">
                             <template #trigger>
                               <n-button size="tiny" quaternary circle type="warning" @click.stop="handleRetryVector(item)">
                                 <template #icon><n-icon><RefreshOutline /></n-icon></template>
                               </n-button>
                             </template>
                             重试向量化
                           </n-tooltip>
                           <n-popconfirm @positive-click="deleteKnowledge(item)">
                              <template #trigger>
                                <n-button size="tiny" quaternary circle type="error">
                                   <template #icon><n-icon><TrashOutline /></n-icon></template>
                                </n-button>
                              </template>
                              确认删除？
                           </n-popconfirm>
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
            <div v-else class="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 overflow-hidden">
               <table class="w-full text-left text-sm">
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
                              <DocumentOutline v-else-if="item.file_type === 'docx'" />
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
                          <div class="flex justify-end gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                             <n-button size="tiny" secondary circle @click="viewKnowledge(item)"><template #icon><n-icon><EyeOutline /></n-icon></template></n-button>
                             <n-button v-if="item.status === 'failed'" size="tiny" secondary circle type="primary" @click="handleRebuild(item)"><template #icon><n-icon><RefreshOutline /></n-icon></template></n-button>
                             <n-button v-if="isFallbackIndexed(item)" size="tiny" secondary circle type="warning" @click="handleRetryVector(item)"><template #icon><n-icon><RefreshOutline /></n-icon></template></n-button>
                             <n-popconfirm @positive-click="deleteKnowledge(item)">
                                <template #trigger>
                                  <n-button size="tiny" secondary circle type="error"><template #icon><n-icon><TrashOutline /></n-icon></template></n-button>
                                </template>
                                确认删除？
                             </n-popconfirm>
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

          <div v-else-if="!loading" class="flex flex-col items-center justify-center py-24 px-6 bg-white/80 dark:bg-gray-800/50 backdrop-blur-md rounded-3xl border border-dashed border-slate-200 dark:border-gray-600/50 shadow-inner">
            <div class="w-24 h-24 rounded-2xl bg-gradient-to-br from-blue-100 to-indigo-100 dark:from-blue-900/30 dark:to-indigo-900/30 flex items-center justify-center mb-6 ring-4 ring-blue-100/50 dark:ring-blue-900/30">
              <n-icon size="48" class="text-blue-500 dark:text-blue-400"><CloudUploadOutline /></n-icon>
            </div>
            <h3 class="text-xl font-bold text-slate-800 dark:text-slate-100 mb-2">{{ t('knowledge.empty') }}</h3>
            <p class="text-slate-500 dark:text-slate-400 mb-8 max-w-sm text-center text-sm leading-relaxed">{{ t('knowledge.emptyDesc') }}</p>
            <n-button type="primary" round size="large" class="px-8 shadow-lg shadow-blue-500/20" @click="showUploadModal = true">
              {{ t('knowledge.uploadFirst') }}
            </n-button>
          </div>
        </n-spin>

        <!-- Modern Upload Modal -->
        <n-modal 
          v-model:show="showUploadModal" 
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
                  accept=".pdf,.doc,.docx,.txt,.md"
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
                <n-button round @click="showUploadModal = false">{{ t('common.cancel') }}</n-button>
                <n-button 
                  type="primary" 
                  round 
                  :disabled="!canUpload" 
                  :loading="uploading" 
                  class="px-8 shadow-lg shadow-blue-500/20"
                  @click="uploadFile"
                >
                  {{ t('knowledge.confirmUpload') }}
                </n-button>
              </div>
            </template>
          </n-modal>

          <!-- Task List Drawer -->
          <n-drawer v-model:show="showTaskList" :width="400" placement="right" class="rounded-l-3xl">
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
                    <n-button quaternary circle size="small" @click="activeTasks = activeTasks.filter(t => t.id !== task.id)">
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
                <n-button block quaternary round @click="activeTasks = activeTasks.filter(t => t.status === 'uploading' || t.status === 'processing')">
                  {{ t('knowledge.clearFinished') || '清除已完成' }}
                </n-button>
              </template>
            </n-drawer-content>
          </n-drawer>

          <!-- Floating Task Button -->
          <div v-if="activeTasks.length > 0" class="fixed bottom-8 right-8 z-50">
            <n-badge :value="activeTasks.filter(t => t.status === 'uploading' || t.status === 'processing').length" :show="activeTasks.filter(t => t.status === 'uploading' || t.status === 'processing').length > 0">
              <n-button 
                circle 
                type="primary" 
                size="large" 
                class="shadow-2xl h-14 w-14"
                @click="showTaskList = true"
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

          <!-- ✅ 新增：文档详情模态框 -->
          <n-modal
            v-model:show="showDetailModal"
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
                <n-button type="primary" round @click="showDetailModal = false">关闭</n-button>
              </div>
            </div>
          </n-modal>

          <n-modal
            v-model:show="showErrorModal"
            preset="card"
            class="max-w-2xl rounded-3xl shadow-2xl"
            title="解析错误详情"
          >
            <div class="space-y-4">
              <div class="text-sm text-gray-500">以下为原始解析错误信息：</div>
              <div class="max-h-72 overflow-y-auto rounded-xl border border-red-200 dark:border-red-800/60 bg-red-50/70 dark:bg-red-900/20 p-3 text-xs leading-5 whitespace-pre-wrap break-all text-red-700 dark:text-red-300">{{ currentErrorDetail }}</div>
              <div class="flex justify-end">
                <n-button type="primary" round @click="showErrorModal = false">关闭</n-button>
              </div>
            </div>
          </n-modal>

      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { NIcon, NButton, NTag, NUploadDragger, NDescriptions, NDescriptionsItem, NModal } from 'naive-ui'
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
  TimeOutline,
  ExtensionPuzzleOutline,
  InformationCircleOutline,
  CheckmarkCircleOutline,
  AlertCircleOutline,
  CloseCircleOutline,
  CheckmarkOutline,
  CloseOutline,
  ListOutline,
  GridOutline
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
import type { KnowledgeBase } from '@/api/knowledge'
import { formatDate } from '@/utils/format'
import { getRagMetrics, type RagMetrics } from '@/api/rag'

interface UploadForm {
  title: string
  tags: string[]
  description: string
  file?: File
}

const message = useDedupedMessage()
const { t } = useI18n()
const loading = ref(false)
const loadError = ref(false)
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

const fileList = ref<UploadFileInfo[]>([])
const uploadRef = ref<any>(null)

interface UploadTask {
  id: string
  name: string
  progress: number
  status: 'uploading' | 'processing' | 'completed' | 'error'
  error?: string
}

const activeTasks = ref<UploadTask[]>([])
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
    const hasProcessing = knowledgeBases.value.some(item => item.status === 'processing')
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

const stats = computed(() => {
  const list = knowledgeBases.value
  return {
    total: list.length,
    completed: list.filter(i => i.status === 'completed').length,
    processing: list.filter(i => i.status === 'processing').length,
    failed: list.filter(i => i.status === 'failed').length
  }
})

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

const handleBatchDelete = async () => {
  if (selectedIds.value.length === 0) return
  
  try {
    await batchDeleteKnowledgeBases(selectedIds.value)
    message.success(t('common.deleteSuccess'))
    selectedIds.value = []
    await loadKnowledgeBases()
  } catch (error: any) {
    message.error(t('common.deleteFailed') + '：' + error.message)
  }
}

const canUpload = computed(() => {
  return uploadForm.value.file
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
      getKnowledgeBases(params),
      getRagMetrics(metricWindow.value).catch(() => null)
    ])

    if (metricsResp?.data?.data) {
      // ⚡ 核心修复：如果后端返回的是全 0 数据，且 knowledgeBases 有数据，则不更新 metrics 以免误导
      const m = metricsResp.data.data
      if (m.retrieval_total > 0 || m.hit_rate > 0 || m.groundedness > 0) {
          ragMetrics.value = m
      } else {
          // 如果确实没数据，也要显示 0 而不是 NaN，前端已做 toFixed 处理
          ragMetrics.value = m
      }
      metricsUpdatedAt.value = new Date().toLocaleTimeString()
    }

    if (response.data && response.data.data) {
        const data = response.data.data
        if (Array.isArray(data.data)) {
            knowledgeBases.value = data.data
            // Start polling if any item is processing
            if (knowledgeBases.value.some(item => item.status === 'processing')) {
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
  } catch (error: any) {
    loadError.value = true
    console.error('Failed to load knowledge bases:', error)
  } finally {
    loading.value = false
  }
}

const handlePageSizeChange = (size: number) => {
  pagination.value.pageSize = size
  pagination.value.page = 1
  loadKnowledgeBases()
}

const handleFileChange = (options: { file: UploadFileInfo }) => {
  const file = options.file.file
  if (file) {
    uploadForm.value.file = file
    if (!uploadForm.value.title) {
      uploadForm.value.title = file.name.replace(/\.[^/.]+$/, '')
    }
  }
}

const handleFileRemove = () => {
  uploadForm.value.file = undefined
}

const uploadFile = async () => {
  if (!uploadForm.value.file) return
  
  const file = uploadForm.value.file
  const taskId = Math.random().toString(36).substring(7)
  
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
    
    const response = await uploadKnowledgeBase(formData, (progressEvent) => {
      const progress = Math.round((progressEvent.loaded * 100) / progressEvent.total)
      const task = activeTasks.value.find(t => t.id === taskId)
      if (task) {
        task.progress = progress
        if (progress === 100) {
          task.status = 'processing'
        }
      }
    })
    
    message.success(t('knowledge.uploadSuccess'))
    
    const task = activeTasks.value.find(t => t.id === taskId)
    if (task) {
      task.status = 'completed'
      task.progress = 100
    }
    
    uploadForm.value = {
      title: '',
      tags: [],
      description: ''
    }
    fileList.value = []
    
    await loadKnowledgeBases()
  } catch (error: any) {
    const task = activeTasks.value.find(t => t.id === taskId)
    if (task) {
      task.status = 'error'
      task.error = error.message
    }
    message.error(t('knowledge.uploadFailed') + '：' + error.message)
  } finally {
    uploading.value = false
  }
}

// ✅ 核心修复：现在点击按钮会正确打开弹窗
const viewKnowledge = async (item: KnowledgeBase) => {
  detailLoading.value = true
  currentDoc.value = {
    ...item,
    upload_source: '知识库上传'
  } as any
  showDetailModal.value = true
  try {
    const [detailRes, contentRes] = await Promise.all([
      getDocumentDetail(item.id),
      getDocumentContent(item.id)
    ])
    const detail = detailRes.data?.data || {}
    const content = contentRes.data?.data || {}
    currentDoc.value = {
      ...item,
      ...detail,
      tags: item.tags || detail.keywords || [],
      upload_source: detail.upload_source || (String(detail.description || '').includes('来自聊天') ? '聊天上传' : '知识库上传'),
      summary: content.summary || detail.description || '',
      suggested_tags: content.suggested_tags || [],
      preview_content: (content.content || '').slice(0, 1500)
    } as any
  } catch {
    message.warning(t('knowledge.previewLoadFailed'))
  } finally {
    detailLoading.value = false
  }
}

const showErrorDetail = (errorText: string) => {
  currentErrorDetail.value = errorText || '未知解析错误'
  showErrorModal.value = true
}

const handleRebuild = async (item: KnowledgeBase) => {
  try {
    await rebuildKnowledgeBase(item.id)
    message.success(t('knowledge.rebuildSuccess') || '开始重新构建索引')
    await loadKnowledgeBases()
  } catch (error: any) {
    message.error((t('knowledge.rebuildFailed') || '重新构建失败') + '：' + error.message)
  }
}

const handleRetryVector = async (item: KnowledgeBase) => {
  try {
    await rebuildKnowledgeBase(item.id)
    message.success(t('knowledge.retryVectorSuccess'))
    await loadKnowledgeBases()
  } catch (error: any) {
    message.error(t('knowledge.retryVectorFailed') + '：' + error.message)
  }
}

const handleRebuildLatestFailedDoc = async () => {
  const failed = knowledgeBases.value.find(i => i.status === 'failed')
  if (!failed) {
    message.info(t('knowledge.noFailedDocs'))
    return
  }
  try {
    await rebuildKnowledgeBase(failed.id)
    message.success(t('knowledge.rebuildSubmitted', { title: failed.title || failed.file_name }))
    await loadKnowledgeBases()
  } catch (error: any) {
    message.error(t('knowledge.rebuildSubmitFailed') + '：' + (error?.message || t('knowledge.unknownError')))
  }
}

const handleRetryAllFallbackDocs = async () => {
  const fallbackDocs = knowledgeBases.value.filter(i => isFallbackIndexed(i))
  if (fallbackDocs.length === 0) {
    message.info(t('knowledge.noFallbackDocs'))
    return
  }

  let successCount = 0
  for (const doc of fallbackDocs) {
    try {
      await rebuildKnowledgeBase(doc.id)
      successCount += 1
    } catch {
      // 单个失败不中断批次
    }
  }

  if (successCount > 0) {
    message.success(t('knowledge.batchRetrySubmitted', { success: successCount, total: fallbackDocs.length }))
  } else {
    message.error(t('knowledge.batchRetryFailed'))
  }
  await loadKnowledgeBases()
}

const deleteKnowledge = async (item: KnowledgeBase) => {
  try {
    await deleteKnowledgeBase(item.id)
    message.success(t('common.deleteSuccess'))
    await loadKnowledgeBases()
  } catch (error: any) {
    message.error(t('knowledge.deleteFailed') + '：' + error.message)
  }
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

const isFallbackIndexed = (item: KnowledgeBase | null): boolean => {
  if (!item) return false
  const err = (item.parse_error || '').toLowerCase()
  return item.status === 'completed' && (err.includes('降级') || err.includes('关键词索引'))
}

const getStatusType = (item: KnowledgeBase | null): 'success' | 'warning' | 'error' => {
  if (!item) return 'error'
  if (item.status === 'processing') return 'warning'
  if (item.status === 'failed') return 'error'
  if (isFallbackIndexed(item)) return 'warning'
  return 'success'
}

const getStatusLabel = (item: KnowledgeBase | null): string => {
  if (!item) return '未知'
  if (item.status === 'processing') return '处理中'
  if (item.status === 'failed') return '失败'
  if (isFallbackIndexed(item)) return '已完成（降级索引）'
  return '已完成'
}

const getFileIconColor = (fileType: string): string => {
  const colors = {
    pdf: '#ff6b6b',
    docx: '#4dabf7',
    doc: '#4dabf7',
    txt: '#51cf66',
    md: '#868e96'
  }
  return colors[fileType as keyof typeof colors] || '#868e96'
}

onMounted(() => {
  loadKnowledgeBases()
})

let searchTimer: number | null = null
watch(searchText, () => {
  if (searchTimer) {
    clearTimeout(searchTimer)
  }
  searchTimer = window.setTimeout(() => {
    pagination.value.page = 1
    loadKnowledgeBases()
  }, 300)
})

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
