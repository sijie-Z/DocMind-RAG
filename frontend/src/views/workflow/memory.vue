<template>
  <div class="min-h-screen bg-gray-50 dark:bg-gray-900 p-6">
    <div class="max-w-6xl mx-auto">
      <!-- 头部 -->
      <div class="flex items-center justify-between mb-6">
        <div>
          <h1 class="text-2xl font-bold text-gray-800 dark:text-white">Agent 记忆系统</h1>
          <p class="text-gray-500 dark:text-gray-400 text-sm mt-1">管理 Agent 的短期记忆、长期记忆、工作记忆和反思记忆</p>
        </div>
        <div class="flex gap-2">
          <n-button @click="loadMemory" :loading="loading">
            <template #icon><n-icon><RefreshOutline /></n-icon></template>
            刷新
          </n-button>
          <n-button type="error" @click="confirmClearAll">
            <template #icon><n-icon><TrashOutline /></n-icon></template>
            清空所有
          </n-button>
        </div>
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
                v-for="item in searchResults"
                :key="item.id"
                class="p-3 bg-gray-50 dark:bg-gray-800 rounded-lg border-l-4 border-blue-400"
              >
                <div class="text-sm">{{ item.content }}</div>
                <div class="text-xs text-gray-400 mt-1 flex justify-between">
                  <n-tag size="small" :type="item.memory_type === 'short_term' ? 'info' : 'success'">
                    {{ item.memory_type === 'short_term' ? '短期' : '长期' }}
                  </n-tag>
                  <span>重要性: {{ item.importance?.toFixed(2) }}</span>
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
import { ref, computed, onMounted } from 'vue'
import {
  NCard, NTabs, NTabPane, NTimeline, NTimelineItem, NTag, NButton, NIcon,
  NInput, NEmpty, NDescriptions, NDescriptionsItem, NCode, useDialog, useMessage
} from 'naive-ui'
import {
  RefreshOutline, TrashOutline, TimeOutline, LibraryOutline,
  BulbOutline, SchoolOutline
} from '@vicons/ionicons5'
import { getAgentMemory, recallMemory, clearMemory } from '@/api/memory'
import dayjs from 'dayjs'

interface MemoryData {
  agent_id: string
  short_term: any[]
  long_term: Record<string, any[]>
  working: Record<string, any>
  reflective: {
    insights: any[]
    patterns: any[]
    lessons: any[]
  }
  interaction_count: number
}

const dialog = useDialog()
const message = useMessage()

const loading = ref(false)
const searching = ref(false)
const searchQuery = ref('')
const searchResults = ref<any[]>([])
const memoryData = ref<MemoryData | null>(null)

const longTermCount = computed(() => {
  if (!memoryData.value?.long_term) return 0
  return Object.values(memoryData.value.long_term).flat().length
})

const loadMemory = async () => {
  loading.value = true
  try {
    const res = await getAgentMemory()
    memoryData.value = res.data?.data
  } catch (error) {
    message.error('加载记忆失败')
  } finally {
    loading.value = false
  }
}

const searchMemory = async () => {
  if (!searchQuery.value.trim()) return

  searching.value = true
  try {
    const res = await recallMemory('default', searchQuery.value)
    searchResults.value = res.data?.data?.results || []
  } catch (error) {
    message.error('搜索失败')
  } finally {
    searching.value = false
  }
}

const clearMemoryType = async (type: string) => {
  dialog.warning({
    title: '确认清空',
    content: `确定要清空${type === 'short_term' ? '短期记忆' : '该类型'}记忆吗？`,
    positiveText: '确定',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await clearMemory('default', type)
        message.success('已清空')
        loadMemory()
      } catch (error) {
        message.error('清空失败')
      }
    }
  })
}

const confirmClearAll = () => {
  dialog.error({
    title: '确认清空所有记忆',
    content: '此操作将清空 Agent 的所有记忆数据，确定继续吗？',
    positiveText: '确定清空',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await clearMemory('default')
        message.success('所有记忆已清空')
        loadMemory()
      } catch (error) {
        message.error('清空失败')
      }
    }
  })
}

const formatTime = (time: string) => {
  return dayjs(time).format('MM-DD HH:mm:ss')
}

onMounted(() => {
  loadMemory()
})
</script>