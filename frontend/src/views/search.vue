<template>
  <div class="h-full flex flex-col p-6 space-y-6 overflow-hidden bg-transparent">
    <!-- 顶部标题区域 -->
    <div class="flex items-center justify-between">
      <div>
        <h1
          class="text-3xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-blue-500 to-blue-700 dark:from-blue-400 dark:to-blue-600">
          {{ t('search.title') }}
        </h1>
        <p class="text-gray-500 dark:text-gray-400 mt-1">
          {{ t('search.subtitle') }}
        </p>
      </div>
      <!-- 搜索统计 (极简展示) -->
      <div v-if="searchStats" class="flex space-x-4">
        <div
          class="px-4 py-2 rounded-2xl bg-white/50 dark:bg-gray-800/50 backdrop-blur-md border border-white/20 dark:border-gray-700/30 shadow-sm">
          <span class="text-xs text-gray-400 block uppercase tracking-wider">{{ t('search.totalFiles') }}</span>
          <span class="text-xl font-bold text-blue-600 dark:text-blue-400">{{ searchStats.total_files }}</span>
        </div>
      </div>
    </div>

    <!-- 核心搜索区 (玻璃拟态大搜索框) -->
    <div class="relative group max-w-4xl mx-auto w-full">
      <div
        class="absolute -inset-1 bg-gradient-to-r from-blue-500 to-blue-600 rounded-[2rem] blur opacity-25 group-hover:opacity-40 transition duration-1000 group-hover:duration-200">
      </div>
      <div
        class="relative flex items-center bg-white/80 dark:bg-gray-900/80 backdrop-blur-2xl rounded-[1.8rem] shadow-xl border border-white/40 dark:border-gray-800/50 p-2 transition-all duration-300">
        <div class="pl-5 pr-3 text-gray-400">
          <n-icon size="24"><search-outline /></n-icon>
        </div>
        <input v-model="searchQuery" type="text" :placeholder="t('search.placeholder')"
          class="flex-1 bg-transparent border-none focus:ring-2 focus:ring-blue-500/30 focus:border-blue-500 rounded-lg text-lg py-3 px-2 text-gray-700 dark:text-gray-200 outline-none"
          @keyup.enter="handleSearch" @input="handleInputChange">

        <!-- 搜索按钮 -->
        <button
          class="ml-2 px-8 py-3 bg-gradient-to-r from-blue-500 to-blue-600 hover:from-blue-600 hover:to-blue-700 text-white font-bold rounded-2xl shadow-lg shadow-blue-500/30 transition-all active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed flex items-center space-x-2"
          :disabled="!searchQuery.trim() || loading" @click="handleSearch">
          <n-spin v-if="loading" size="small" stroke="white" />
          <span>{{ t('search.button') }}</span>
        </button>

        <!-- 搜索建议浮窗 -->
        <div v-if="suggestions.length > 0"
          class="absolute top-full left-0 right-0 mt-3 bg-white/90 dark:bg-gray-900/90 backdrop-blur-xl rounded-2xl border border-white/20 dark:border-gray-800/50 shadow-2xl z-50 overflow-hidden">
          <div v-for="suggestion in suggestions" :key="suggestion"
            class="px-6 py-3 hover:bg-slate-50 dark:hover:bg-slate-900/30 cursor-pointer transition-colors text-gray-600 dark:text-gray-300 flex items-center space-x-3"
            @click="selectSuggestion(suggestion)">
            <n-icon class="text-gray-400"><time-outline /></n-icon>
            <span>{{ suggestion }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 筛选与结果主区域 -->
    <div class="flex-1 flex space-x-6 overflow-hidden">
      <!-- 左侧极简筛选栏 -->
      <div class="w-72 flex flex-col space-y-4 overflow-y-auto pr-2 custom-scrollbar">
        <div
          class="bg-white/60 dark:bg-gray-900/60 backdrop-blur-xl rounded-3xl border border-white/20 dark:border-gray-800/30 p-5 space-y-6">
          <!-- 搜索模式 -->
          <div class="space-y-3">
            <h3 class="text-sm font-bold text-gray-400 uppercase tracking-widest">{{ t('search.type') }}</h3>
            <n-radio-group v-model:value="searchType" name="searchType" class="w-full">
              <n-space vertical>
                <n-radio value="hybrid">{{ t('search.hybrid') }}</n-radio>
                <n-radio value="semantic">{{ t('search.semantic') }}</n-radio>
                <n-radio value="keyword">{{ t('search.keyword') }}</n-radio>
              </n-space>
            </n-radio-group>
          </div>

          <!-- 文件类型 -->
          <div class="space-y-3">
            <h3 class="text-sm font-bold text-gray-400 uppercase tracking-widest">{{ t('search.fileType') }}</h3>
            <n-checkbox-group v-model:value="selectedFileTypes">
              <div class="grid grid-cols-2 gap-2">
                <n-checkbox v-for="type in fileTypes" :key="type.value" :value="type.value">
                  {{ type.label }}
                </n-checkbox>
              </div>
            </n-checkbox-group>
          </div>

          <!-- 排序方式 -->
          <div class="space-y-3">
            <h3 class="text-sm font-bold text-gray-400 uppercase tracking-widest">排序</h3>
            <n-select v-model:value="sortBy" :options="sortOptions" size="small" />
          </div>

          <!-- 重置按钮 -->
          <n-button block quaternary round @click="clearFilters">
            {{ t('search.clear') }}
          </n-button>
        </div>
      </div>

      <!-- 右侧结果列表 -->
      <div class="flex-1 flex flex-col overflow-hidden">
        <!-- 结果状态栏 -->
        <div v-if="searchResults.length > 0" class="mb-4 flex items-center justify-between text-sm text-gray-500">
          <span>{{ t('search.results') }}: <span class="font-bold text-blue-600">{{ searchResults.length
          }}</span></span>
        </div>

        <div class="flex-1 overflow-y-auto pr-2 custom-scrollbar">
          <!-- 结果列表 -->
          <div v-if="searchResults.length > 0" class="space-y-4">
            <div v-for="result in sortedResults" :key="result.id"
              class="group bg-white/60 dark:bg-gray-900/60 backdrop-blur-xl rounded-3xl border border-white/20 dark:border-gray-800/30 p-5 hover:shadow-xl hover:shadow-blue-500/10 hover:border-blue-500/30 transition-all duration-300 cursor-pointer"
              @click="showResultDetail(result)">
              <div class="flex justify-between items-start mb-3">
                <div class="flex items-center space-x-3">
                  <div
                    class="w-10 h-10 rounded-xl bg-slate-50 dark:bg-slate-900/30 flex items-center justify-center">
                    <n-icon size="22" class="text-slate-500 dark:text-slate-400">
                      <component :is="getFileTypeIcon(result.file_type)" />
                    </n-icon>
                  </div>
                  <div>
                    <h3
                      class="font-bold text-gray-800 dark:text-gray-100 group-hover:text-blue-600 dark:group-hover:text-blue-400 transition-colors">
                      {{ result.filename }}
                    </h3>
                    <div class="flex items-center space-x-3 text-xs text-gray-400">
                      <span>{{ formatFileSize(result.file_size) }}</span>
                      <span>•</span>
                      <span>{{ formatDate(result.upload_time) }}</span>
                    </div>
                  </div>
                </div>
                <div class="flex items-center space-x-2">
                  <n-tag :bordered="false" type="success" size="small" round
                    class="bg-emerald-50 dark:bg-emerald-900/20 text-emerald-600">
                    {{ (result.relevance_score * 100).toFixed(1) }}% {{ t('search.relevanceScore') }}
                  </n-tag>
                </div>
              </div>

              <div class="text-sm text-gray-600 dark:text-gray-400 line-clamp-2 leading-relaxed mb-3">
                {{ result.content }}
              </div>

              <div class="flex items-center justify-between mt-4 pt-4 border-t border-gray-100 dark:border-gray-800/50">
                <div class="flex items-center space-x-2">
                  <n-button size="tiny" secondary round type="primary" @click.stop="askAI(result)">
                    <template #icon><n-icon><ChatbubblesOutline /></n-icon></template>
                    {{ t('search.askAI', '问问 AI') }}
                  </n-button>
                </div>
                <n-button size="tiny" quaternary circle @click.stop="copyResultContent(result)">
                  <template #icon><n-icon><CopyOutline /></n-icon></template>
                </n-button>
              </div>
            </div>
          </div>

          <!-- 无结果/初始状态 -->
          <div v-else-if="hasSearched && !loading"
            class="h-full flex flex-col items-center justify-center text-center p-12">
            <div
              class="w-24 h-24 bg-gray-100 dark:bg-gray-800 rounded-[2.5rem] flex items-center justify-center mb-6 grayscale opacity-50">
              <n-icon size="48" class="text-gray-400"><SearchOutline /></n-icon>
            </div>
            <h3 class="text-xl font-bold text-gray-700 dark:text-gray-200 mb-2">{{ t('search.noResults') }}</h3>
            <p class="text-gray-500 max-w-xs">{{ t('search.noResultsDesc') }}</p>
          </div>

          <div v-else-if="!hasSearched && !loading"
            class="h-full flex flex-col items-center justify-center text-center p-12 opacity-30">
            <div class="mb-6">
              <n-icon size="64" class="text-gray-300 dark:text-gray-600"><SparklesOutline /></n-icon>
            </div>
            <p class="text-xl font-medium text-gray-400">开始探索您的知识库</p>
          </div>
        </div>
      </div>
    </div>

    <!-- 结果详情模态框 (现代风格) -->
    <n-modal v-model:show="showDetailModal" preset="card" style="width: min(800px, 90vw); border-radius: 2rem;"
      class="backdrop-blur-2xl bg-white/80 dark:bg-gray-900/80" :title="selectedResult?.filename">
      <div v-if="selectedResult" class="space-y-6 p-2">
        <div class="grid grid-cols-4 gap-4">
          <div class="p-3 rounded-2xl bg-gray-50 dark:bg-gray-800/50">
            <span class="text-xs text-gray-400 block mb-1">{{ t('search.fileType') }}</span>
            <span class="font-bold text-gray-700 dark:text-gray-200">{{ selectedResult.file_type.toUpperCase() }}</span>
          </div>
          <div class="p-3 rounded-2xl bg-gray-50 dark:bg-gray-800/50">
            <span class="text-xs text-gray-400 block mb-1">{{ t('search.fileSize') }}</span>
            <span class="font-bold text-gray-700 dark:text-gray-200">{{ formatFileSize(selectedResult.file_size)
            }}</span>
          </div>
          <div class="p-3 rounded-2xl bg-gray-50 dark:bg-gray-800/50">
            <span class="text-xs text-gray-400 block mb-1">匹配度</span>
            <span class="font-bold text-emerald-600">{{ (selectedResult.relevance_score * 100).toFixed(1) }}%</span>
          </div>
          <div class="p-3 rounded-2xl bg-gray-50 dark:bg-gray-800/50">
            <span class="text-xs text-gray-400 block mb-1">日期</span>
            <span class="font-bold text-gray-700 dark:text-gray-200">{{ formatDate(selectedResult.upload_time).split('')[0]
            }}</span>
          </div>
        </div>

        <div class="space-y-2">
          <h4 class="text-sm font-bold text-gray-400 uppercase tracking-widest">{{ t('search.fileContent') }}</h4>
          <div
            class="p-6 rounded-3xl bg-gray-50 dark:bg-gray-800/50 text-gray-700 dark:text-gray-300 leading-loose whitespace-pre-wrap max-h-96 overflow-y-auto custom-scrollbar border border-gray-100 dark:border-gray-700/30">
            {{ selectedResult.content }}
          </div>
        </div>

        <div class="flex justify-end gap-3 pt-4 border-t border-gray-100 dark:border-gray-800/50">
          <n-button round secondary @click="copyResultContent(selectedResult)">
            <template #icon><n-icon><CopyOutline /></n-icon></template>
            {{ t('common.copy', '复制内容') }}
          </n-button>
          <n-button type="primary" round class="px-8 shadow-lg shadow-blue-500/20" @click="askAI(selectedResult)">
            <template #icon><n-icon><ChatbubblesOutline /></n-icon></template>
            {{ t('search.askAI', '问问 AI') }}
          </n-button>
        </div>
      </div>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useDedupedMessage } from '@/utils/message'
import { NIcon, NSpin, NRadioGroup, NRadio, NSpace, NCheckboxGroup, NCheckbox, NSelect, NTag, NButton, NModal } from 'naive-ui'
import { SearchOutline, TimeOutline, ChatbubblesOutline, CopyOutline, SparklesOutline, DocumentTextOutline, DocumentOutline, GridOutline, NewspaperOutline } from '@vicons/ionicons5'
import { searchKnowledge, getSearchSuggestions, getSearchStats } from '@/api/search'
import type { SearchResult, SearchStats } from '@/api/search'
import { debounce } from 'lodash-es'
import { useI18n } from 'vue-i18n'
import { useUserStore } from '@/stores/user'

const { t } = useI18n()
const userStore = useUserStore()
const router = useRouter()
const message = useDedupedMessage()

// 搜索状态
const searchQuery = ref('')
const searchType = ref<'semantic' | 'keyword' | 'hybrid'>('hybrid')
const selectedFileTypes = ref<string[]>([])
const sortBy = ref<'relevance' | 'date' | 'name'>('relevance')

// 结果状态
const searchResults = ref<SearchResult[]>([])
const searchStats = ref<SearchStats | null>(null)
const suggestions = ref<string[]>([])
const selectedResult = ref<SearchResult | null>(null)
const showDetailModal = ref(false)
const loading = ref(false)
const hasSearched = ref(false)

const currentOrgId = computed(() => userStore.userInfo?.organizations?.[0]?.id)

const sortOptions = [
  { label: t('search.relevance'), value: 'relevance' },
  { label: t('search.date'), value: 'date' },
  { label: t('search.name'), value: 'name' }
]

const fileTypes = [
  { value: 'pdf', label: 'PDF' },
  { value: 'docx', label: 'Word' },
  { value: 'xlsx', label: 'Excel' },
  { value: 'txt', label: '文本' },
  { value: 'md', label: 'Markdown' }
]

const sortedResults = computed(() => {
  const results = [...searchResults.value]
  if (sortBy.value === 'relevance') return results.sort((a, b) => b.relevance_score - a.relevance_score)
  if (sortBy.value === 'date') return results.sort((a, b) => new Date(b.upload_time).getTime() - new Date(a.upload_time).getTime())
  if (sortBy.value === 'name') return results.sort((a, b) => a.filename.localeCompare(b.filename))
  return results
})

const getFileTypeIcon = (type: string) => {
  const map: Record<string, unknown> = { pdf: DocumentOutline, docx: DocumentTextOutline, xlsx: GridOutline, txt: DocumentTextOutline, md: NewspaperOutline }
  return map[type.toLowerCase()] || DocumentOutline
}

const formatFileSize = (bytes: number) => {
  if (bytes === 0) return '0 B'
  const i = Math.floor(Math.log(bytes) / Math.log(1024))
  return (bytes / Math.pow(1024, i)).toFixed(2) + ' ' + ['B', 'KB', 'MB', 'GB'][i]
}

const formatDate = (ds: string) => new Date(ds).toLocaleString('zh-CN')

const handleSearch = async () => {
  if (!searchQuery.value.trim()) return
  loading.value = true
  hasSearched.value = true
  try {
    searchResults.value = await searchKnowledge({
      query: searchQuery.value,
      search_type: searchType.value,
      file_types: selectedFileTypes.value.length ? selectedFileTypes.value : undefined,
      limit: 50
    })
  } catch (e) {
    message.error(t('search.searchFailed') || t('common.error'))
  } finally {
    loading.value = false
  }
}

const handleInputChange = debounce(async () => {
  if (!searchQuery.value.trim() || !currentOrgId.value) { suggestions.value = []; return }
  suggestions.value = await getSearchSuggestions(searchQuery.value, currentOrgId.value)
}, 300)

const selectSuggestion = (s: string) => { searchQuery.value = s; suggestions.value = []; handleSearch() }

const clearFilters = () => {
  searchType.value = 'hybrid'
  selectedFileTypes.value = []
  sortBy.value = 'relevance'
}

const showResultDetail = (r: SearchResult) => { selectedResult.value = r; showDetailModal.value = true }

const askAI = (r: SearchResult) => {
  router.push({ name: 'Chat', query: { q: `关于文件 "${r.filename}"...`, context_id: r.id } })
}

const copyResultContent = async (r: SearchResult | null) => {
  if (!r) return
  await navigator.clipboard.writeText(r.content)
  message.success(t('common.copySuccess'))
}

onMounted(async () => {
  if (currentOrgId.value) searchStats.value = await getSearchStats(currentOrgId.value)
})
</script>

<style scoped>
.custom-scrollbar::-webkit-scrollbar { width: 6px; }
.custom-scrollbar::-webkit-scrollbar-thumb { background: rgba(99, 102, 241, 0.1); border-radius: 10px; }
.line-clamp-2 {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
