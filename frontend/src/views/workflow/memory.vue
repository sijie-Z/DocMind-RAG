<template>
  <div class="min-h-screen bg-gray-50 dark:bg-gray-900 p-6">
    <div class="max-w-6xl mx-auto">
      <!-- T9 定位三件套：面包屑（DocMind / Agent 工作流 / Agent记忆）+ 返回出口（→ Agent 工作流） -->
      <PageBreadcrumb :show-back="true" class="mb-4" />

      <!-- 头部 -->
      <div class="flex items-center justify-between mb-6">
        <div>
          <h1 class="text-2xl font-bold text-gray-800 dark:text-white">{{ t('router.agentMemory') }}</h1>
          <p class="text-gray-500 dark:text-gray-400 text-sm mt-1">管理 Agent 的短期记忆、长期记忆、工作记忆和反思记忆</p>
        </div>
        <div class="flex gap-2">
          <n-button @click="loadMemory" :loading="loading">
            <template #icon><n-icon><RefreshOutline /></n-icon></template>
            刷新
          </n-button>
          <n-button type="error" @click="confirmClearAll">
            <template #icon><n-icon><TrashOutline /></n-icon></template>
            {{ t('memory.clearAll.action') }}
          </n-button>
        </div>
      </div>

      <!-- 作用域：Agent 运行时把记忆写在 session:<会话id>，页面必须能选到该命名空间 -->
      <div class="mb-6">
        <div class="flex flex-wrap items-center gap-3">
          <span class="text-sm text-gray-600 dark:text-gray-300">{{ t('memory.scope.label') }}</span>
          <n-select
            :value="scope"
            :options="scopeOptions"
            :loading="sessionsLoading"
            filterable
            class="w-full sm:w-80"
            data-testid="memory-scope-select"
            @update:value="handleScopeChange"
          />
          <template v-if="sessionsError">
            <span class="text-xs text-red-500">{{ t('memory.scope.loadFailed') }}</span>
            <n-button size="small" quaternary @click="loadSessions">{{ t('common.retry') }}</n-button>
          </template>
          <span v-if="loading" class="text-xs text-gray-400">{{ t('common.loading') }}</span>
        </div>
        <p class="text-xs text-gray-500 dark:text-gray-400 mt-2">{{ t('memory.scope.hint') }}</p>
      </div>

      <!-- 统计卡片 -->
      <div class="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
        <n-card size="small" class="shadow-sm">
          <div class="flex items-center gap-3">
            <div class="w-10 h-10 rounded-lg bg-blue-100 dark:bg-blue-900/30 flex items-center justify-center">
              <n-icon size="20" class="text-blue-500"><TimeOutline /></n-icon>
            </div>
            <div>
              <div class="text-2xl font-bold text-gray-800 dark:text-white">{{ memoryData?.short_term?.length || 0 }}</div>
              <div class="text-xs text-gray-500">短期记忆</div>
            </div>
          </div>
        </n-card>

        <n-card size="small" class="shadow-sm">
          <div class="flex items-center gap-3">
            <div class="w-10 h-10 rounded-lg bg-green-100 dark:bg-green-900/30 flex items-center justify-center">
              <n-icon size="20" class="text-green-500"><LibraryOutline /></n-icon>
            </div>
            <div>
              <div class="text-2xl font-bold text-gray-800 dark:text-white">{{ longTermCount }}</div>
              <div class="text-xs text-gray-500">长期记忆</div>
            </div>
          </div>
        </n-card>

        <n-card size="small" class="shadow-sm">
          <div class="flex items-center gap-3">
            <div class="w-10 h-10 rounded-lg bg-amber-100 dark:bg-amber-900/30 flex items-center justify-center">
              <n-icon size="20" class="text-amber-500"><BulbOutline /></n-icon>
            </div>
            <div>
              <div class="text-2xl font-bold text-gray-800 dark:text-white">{{ memoryData?.reflective?.insights?.length || 0 }}</div>
              <div class="text-xs text-gray-500">洞察</div>
            </div>
          </div>
        </n-card>

        <n-card size="small" class="shadow-sm">
          <div class="flex items-center gap-3">
            <div class="w-10 h-10 rounded-lg bg-rose-100 dark:bg-rose-900/30 flex items-center justify-center">
              <n-icon size="20" class="text-rose-500"><SchoolOutline /></n-icon>
            </div>
            <div>
              <div class="text-2xl font-bold text-gray-800 dark:text-white">{{ memoryData?.reflective?.lessons?.length || 0 }}</div>
              <div class="text-xs text-gray-500">经验教训</div>
            </div>
          </div>
        </n-card>
      </div>

      <!-- 主内容 -->
      <n-tabs type="line" animated>
        <!-- 短期记忆 -->
        <n-tab-pane name="short_term" tab="短期记忆">
          <n-card size="small" class="shadow-sm">
            <template #header>
              <div class="flex items-center justify-between">
                <span>短期记忆 (最近 {{ memoryData?.short_term?.length || 0 }} 条)</span>
                <n-button size="small" quaternary @click="clearMemoryType('short_term')">清空</n-button>
              </div>
            </template>
            <n-empty v-if="!memoryData?.short_term?.length" description="暂无短期记忆" />
            <n-timeline v-else>
              <n-timeline-item
                v-for="item in memoryData?.short_term"
                :key="item.id"
                :time="formatTime(item.created_at)"
                :title="item.content"
              >
                <div class="text-xs text-gray-400">
                  重要性: {{ item.importance.toFixed(2) }} | 访问: {{ item.access_count }} 次
                </div>
              </n-timeline-item>
            </n-timeline>
          </n-card>
        </n-tab-pane>

        <!-- 长期记忆 -->
        <n-tab-pane name="long_term" tab="长期记忆">
          <n-card size="small" class="shadow-sm">
            <n-empty v-if="longTermCount === 0" description="暂无长期记忆" />
            <div v-else class="space-y-4">
              <div v-for="(items, type) in memoryData?.long_term" :key="type">
                <div class="font-medium text-gray-700 dark:text-gray-300 mb-2">{{ type }}</div>
                <div class="space-y-2">
                  <div
                    v-for="item in items"
                    :key="item.id"
                    class="p-3 bg-gray-50 dark:bg-gray-800 rounded-lg"
                  >
                    <div class="text-sm">{{ item.content }}</div>
                    <div class="text-xs text-gray-400 mt-1 flex justify-between">
                      <span>重要性: {{ item.importance.toFixed(2) }}</span>
                      <span>{{ formatTime(item.created_at) }}</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </n-card>
        </n-tab-pane>

        <!-- 工作记忆 -->
        <n-tab-pane name="working" tab="工作记忆">
          <n-card size="small" class="shadow-sm">
            <n-descriptions label-placement="left" :column="1" bordered>
              <n-descriptions-item label="当前状态">
                <n-code :code="JSON.stringify(memoryData?.working?.state || {}, null, 2)" language="json" />
              </n-descriptions-item>
              <n-descriptions-item label="任务栈">
                <n-code :code="JSON.stringify(memoryData?.working?.task_stack || [], null, 2)" language="json" />
              </n-descriptions-item>
              <n-descriptions-item label="中间结果">
                <n-code :code="JSON.stringify(memoryData?.working?.intermediate_results || {}, null, 2)" language="json" />
              </n-descriptions-item>
              <n-descriptions-item label="变量">
                <n-code :code="JSON.stringify(memoryData?.working?.variables || {}, null, 2)" language="json" />
              </n-descriptions-item>
            </n-descriptions>
          </n-card>
        </n-tab-pane>

        <!-- 反思记忆 -->
        <n-tab-pane name="reflective" tab="反思记忆">
          <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
            <!-- 洞察 -->
            <n-card title="洞察" size="small" class="shadow-sm">
              <n-empty v-if="!memoryData?.reflective?.insights?.length" description="暂无洞察" />
              <div v-else class="space-y-2">
                <div
                  v-for="(insight, index) in memoryData?.reflective?.insights"
                  :key="index"
                  class="p-2 bg-amber-50 dark:bg-amber-900/20 rounded"
                >
                  <div class="text-sm">{{ insight.content }}</div>
                  <div class="text-xs text-gray-400 mt-1">{{ formatTime(insight.created_at) }}</div>
                </div>
              </div>
            </n-card>

            <!-- 经验教训 -->
            <n-card title="经验教训" size="small" class="shadow-sm">
              <n-empty v-if="!memoryData?.reflective?.lessons?.length" description="暂无经验教训" />
              <div v-else class="space-y-2">
                <div
                  v-for="(lesson, index) in memoryData?.reflective?.lessons"
                  :key="index"
                  class="p-2 bg-rose-50 dark:bg-rose-900/20 rounded"
                >
                  <div class="text-sm font-medium">{{ lesson.lesson }}</div>
                  <div v-if="lesson.trigger" class="text-xs text-gray-500 mt-1">触发: {{ lesson.trigger }}</div>
                  <div v-if="lesson.solution" class="text-xs text-green-600 mt-1">解决: {{ lesson.solution }}</div>
                </div>
              </div>
            </n-card>
          </div>

          <!-- 模式 -->
          <n-card title="发现的模式" size="small" class="shadow-sm mt-4">
            <n-empty v-if="!memoryData?.reflective?.patterns?.length" description="暂无模式" />
            <n-tag v-else v-for="(pattern, index) in memoryData?.reflective?.patterns" :key="index" class="mr-2 mb-2">
              {{ pattern.pattern }}
            </n-tag>
          </n-card>
        </n-tab-pane>

        <!-- 记忆检索 -->
        <n-tab-pane name="search" tab="记忆检索">
          <n-card size="small" class="shadow-sm">
            <div class="flex gap-2 mb-4">
              <n-input
                v-model:value="searchQuery"
                placeholder="输入关键词搜索记忆..."
                @keyup.enter="searchMemory"
                class="flex-1"
              />
              <n-button type="primary" @click="searchMemory" :loading="searching">
                搜索
              </n-button>
            </div>

            <n-empty v-if="!searchResults.length && !searching" description="输入关键词搜索记忆" />

            <div v-if="searchResults.length" class="space-y-2">
              <div class="text-sm text-gray-500 mb-2">找到 {{ searchResults.length }} 条相关记忆</div>
              <div
                v-for="(item, index) in searchResults"
                :key="item.id ?? 'r-' + index"
                class="p-3 bg-gray-50 dark:bg-gray-800 rounded-lg border-l-4 border-blue-400"
              >
                <div class="text-sm">{{ item.content || item.lesson }}</div>
                <div v-if="item.trigger" class="text-xs text-gray-500 mt-1">触发: {{ item.trigger }}</div>
                <div v-if="item.solution" class="text-xs text-green-600 mt-1">解决: {{ item.solution }}</div>
                <div class="text-xs text-gray-400 mt-1 flex justify-between">
                  <n-tag v-if="resultLabel(item)" size="small" :type="resultTagType(item)">
                    {{ resultLabel(item) }}
                  </n-tag>
                  <span v-else></span>
                  <span v-if="typeof item.importance === 'number'">重要性: {{ item.importance.toFixed(2) }}</span>
                </div>
              </div>
            </div>
          </n-card>
        </n-tab-pane>
      </n-tabs>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import {
  NCard, NTabs, NTabPane, NTimeline, NTimelineItem, NTag, NButton, NIcon,
  NInput, NEmpty, NDescriptions, NDescriptionsItem, NCode, NSelect, useDialog, useMessage
} from 'naive-ui'
import {
  RefreshOutline, TrashOutline, TimeOutline, LibraryOutline,
  BulbOutline, SchoolOutline
} from '@vicons/ionicons5'
import { getAgentMemory, recallMemory, clearMemory } from '@/api/memory'
import { agentApi } from '@/api/agent'
import type { AgentSession } from '@/types/agent'
import { apiUnwrap } from '@/utils/apiUnwrap'
import dayjs from 'dayjs'
import { parseApiDate } from '@/utils/format'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import PageBreadcrumb from '@/components/common/PageBreadcrumb.vue'

const { t } = useI18n()

interface MemoryItem {
  id: string
  content: string
  created_at: string
  importance: number
  access_count: number
  memory_type?: string
  [key: string]: unknown
}

interface InsightItem {
  content?: string
  created_at?: string
  [key: string]: unknown
}

interface PatternItem {
  pattern?: string
  [key: string]: unknown
}

interface LessonItem {
  lesson?: string
  trigger?: string
  solution?: string
  [key: string]: unknown
}

/**
 * 记忆检索（recall）结果 —— 后端三种形状混装（memory_service.py:604-641）：
 *  - short_term / long_term：to_dict 全字段 {id,content,memory_type,importance,…}
 *  - reflective insight：{type:'insight', content}
 *  - reflective lesson：{type:'lesson', lesson, trigger, solution, created_at}
 * 旧渲染按 short/long 单一形状读（item.content / memory_type 二元标签 /
 * item.importance?.toFixed），lesson 项内容空白、标签误标「长期」、
 * 重要性行半空（B14 形状漂移）。
 */
interface RecallItem {
  id?: string
  content?: string
  memory_type?: string
  importance?: number
  type?: string
  lesson?: string
  trigger?: string
  solution?: string
  created_at?: string
}

interface MemoryData {
  agent_id: string
  short_term: MemoryItem[]
  long_term: Record<string, MemoryItem[]>
  working: Record<string, unknown>
  reflective: {
    insights: InsightItem[]
    patterns: PatternItem[]
    lessons: LessonItem[]
  }
  interaction_count: number
}

const dialog = useDialog()
const message = useMessage()

const loading = ref(false)
const searching = ref(false)
const searchQuery = ref('')
const searchResults = ref<RecallItem[]>([])
const memoryData = ref<MemoryData | null>(null)

const route = useRoute()
const router = useRouter()

const GLOBAL_SCOPE = 'default'
// 与后端 _normalize_agent_id 同口径（endpoints/memory.py）：非法作用域不发请求，
// 回落全局，避免把深链里的任意字符串拼进记忆命名空间。
const SCOPE_PATTERN = /^[a-zA-Z0-9_:-]{1,64}$/

const normalizeScope = (raw: unknown): string =>
  typeof raw === 'string' && SCOPE_PATTERN.test(raw) ? raw : GLOBAL_SCOPE

const scope = ref(normalizeScope(route.query.scope))
const sessions = ref<AgentSession[]>([])
const sessionsLoading = ref(false)
const sessionsError = ref(false)

const scopeOptions = computed<Array<{ label: string; value: string }>>(() => {
  const options = [{ label: t('memory.scope.global'), value: GLOBAL_SCOPE }]
  for (const session of sessions.value) {
    options.push({ label: session.title?.trim() || session.id, value: `session:${session.id}` })
  }
  // 深链指向的会话可能已不在最近列表（或删除）：如实保留该选项，不静默改回全局
  if (!options.some((option) => option.value === scope.value)) {
    options.push({ label: t('memory.scope.outsideList', { scope: scope.value }), value: scope.value })
  }
  return options
})

const scopeLabel = computed(
  () => scopeOptions.value.find((option) => option.value === scope.value)?.label || scope.value,
)

const loadSessions = async () => {
  sessionsLoading.value = true
  sessionsError.value = false
  try {
    const res = await agentApi.listSessions({ page: 1, page_size: 20 }, { silent: true })
    sessions.value = apiUnwrap<{ sessions: AgentSession[] } | null>(res)?.sessions || []
  } catch {
    sessions.value = []
    sessionsError.value = true
  } finally {
    sessionsLoading.value = false
  }
}

/** URL 深链同步：?scope= 可刷新/分享直达；回到全局时清掉，其余 query 原样保留。 */
const syncRouteScope = (value: string) => {
  const query = { ...route.query }
  if (value === GLOBAL_SCOPE) delete query.scope
  else query.scope = value
  router.replace({ query }).catch(() => {})
}

const handleScopeChange = async (value: string) => {
  const next = normalizeScope(value)
  if (next === scope.value) return
  scope.value = next
  // 上一作用域的命中不得冒充当前作用域的检索结果
  searchResults.value = []
  syncRouteScope(next)
  await loadMemory()
}

// 同路由仅 query 变化时组件实例复用，深链到达也要生效
watch(
  () => route.query.scope,
  (raw) => {
    if (route.name !== 'AgentMemory') return
    const next = normalizeScope(raw)
    if (next === scope.value) return
    scope.value = next
    searchResults.value = []
    loadMemory()
  },
)

// recall 结果按真实形状打中文标签（见 RecallItem 注释）；无从推导时返回 ''
const resultLabel = (item: RecallItem): string => {
  if (item.memory_type === 'short_term') return '短期'
  if (item.memory_type === 'long_term') return '长期'
  if (item.type === 'insight') return '洞察'
  if (item.type === 'lesson') return '经验'
  return ''
}

const resultTagType = (item: RecallItem): 'default' | 'info' | 'success' | 'warning' | 'error' => {
  if (item.memory_type === 'short_term') return 'info'
  if (item.memory_type === 'long_term') return 'success'
  if (item.type === 'insight') return 'warning'
  if (item.type === 'lesson') return 'error'
  return 'default'
}

const longTermCount = computed(() => {
  if (!memoryData.value?.long_term) return 0
  return Object.values(memoryData.value.long_term).flat().length
})

const loadMemory = async () => {
  const requestScope = scope.value
  loading.value = true
  try {
    const res = await getAgentMemory(requestScope)
    // GET /memory/{agent_id} → {success, data: <导出记忆>} → apiUnwrap 解一层
    if (requestScope !== scope.value) return
    memoryData.value = apiUnwrap<MemoryData | null>(res)
  } catch {
    if (requestScope === scope.value) message.error('加载记忆失败')
  } finally {
    // 作用域已切换时由新一轮请求掌管 loading，旧请求不得提前收起
    if (requestScope === scope.value) loading.value = false
  }
}

const searchMemory = async () => {
  if (!searchQuery.value.trim()) return

  const requestScope = scope.value
  searching.value = true
  try {
    const res = await recallMemory(requestScope, searchQuery.value)
    // POST /memory/{agent_id}/recall → {success, data: {query, results, count}}
    if (requestScope !== scope.value) return
    searchResults.value = apiUnwrap<{ results: RecallItem[] } | null>(res)?.results || []
  } catch {
    if (requestScope === scope.value) message.error('搜索失败')
  } finally {
    if (requestScope === scope.value) searching.value = false
  }
}

const clearMemoryType = (type: string) => {
  // 确认框弹出后作用域仍可能被切换：清空目标锁定在弹框时的作用域
  const targetScope = scope.value
  const targetLabel = scopeLabel.value
  dialog.warning({
    title: t('memory.clear.title'),
    content: t('memory.clear.content', {
      scope: targetLabel,
      type: type === 'short_term' ? t('memory.clear.shortTerm') : t('memory.clear.otherType'),
    }),
    positiveText: t('common.confirm'),
    negativeText: t('common.cancel'),
    onPositiveClick: async () => {
      try {
        await clearMemory(targetScope, type)
        message.success(t('memory.clear.success'))
        loadMemory()
      } catch {
        message.error(t('memory.clear.failed'))
      }
    }
  })
}

const confirmClearAll = () => {
  const targetScope = scope.value
  const targetLabel = scopeLabel.value
  dialog.error({
    title: t('memory.clearAll.title'),
    content: t('memory.clearAll.content', { scope: targetLabel }),
    positiveText: t('memory.clearAll.positive'),
    negativeText: t('common.cancel'),
    onPositiveClick: async () => {
      try {
        await clearMemory(targetScope)
        message.success(t('memory.clearAll.success', { scope: targetLabel }))
        loadMemory()
      } catch {
        message.error(t('memory.clear.failed'))
      }
    }
  })
}

const formatTime = (time?: string) => {
  const parsed = parseApiDate(time)
  return parsed ? dayjs(parsed).format('MM-DD HH:mm:ss') : '-'
}

onMounted(() => {
  loadSessions()
  loadMemory()
})
</script>