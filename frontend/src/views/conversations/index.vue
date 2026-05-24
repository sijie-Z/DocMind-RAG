<template>
  <div class="min-h-screen bg-gray-50 dark:bg-gray-900 p-6">
    <div class="max-w-4xl mx-auto">
      <!-- 头部 -->
      <div class="mb-6">
        <div class="flex items-center justify-between">
          <div>
            <h1 class="text-2xl font-bold text-gray-800 dark:text-white">{{ t('conversations.title') }}</h1>
            <p class="text-gray-500 dark:text-gray-400 text-sm mt-1">查看和管理对话记录</p>
          </div>
          <n-button type="primary" @click="startNewChat">
            <template #icon><n-icon><AddOutline /></n-icon></template>
            新对话
          </n-button>
        </div>

        <!-- 搜索 -->
        <div class="mt-4 flex gap-3">
          <n-input v-model:value="searchText" placeholder="搜索对话..." clearable @clear="loadConversations" @keydown.enter="loadConversations">
            <template #prefix><n-icon><SearchOutline /></n-icon></template>
          </n-input>
          <n-button @click="loadConversations" :loading="loading">
            <template #icon><n-icon><RefreshOutline /></n-icon></template>
          </n-button>
        </div>
      </div>

      <!-- 列表 -->
      <n-spin :show="loading">
        <template #description>
          <span>加载中...</span>
        </template>

        <!-- Skeleton loading state -->
        <div v-if="loading" class="space-y-2">
          <div v-for="n in 5" :key="n" class="flex items-center justify-between p-4 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
            <div class="flex items-center gap-4 flex-1 min-w-0">
              <n-skeleton width="40px" height="40px" circle />
              <div class="flex-1 space-y-2">
                <n-skeleton text width="50%" />
                <n-skeleton text width="30%" />
              </div>
            </div>
            <div class="flex items-center gap-2">
              <n-skeleton width="60px" height="28px" />
              <n-skeleton width="28px" height="28px" />
            </div>
          </div>
        </div>

        <!-- Error state -->
        <div v-if="!loading && loadError" class="py-16 text-center">
          <n-result status="error" title="加载失败" :description="loadErrorMsg">
            <template #footer>
              <n-button type="primary" @click="loadConversations">重试</n-button>
            </template>
          </n-result>
        </div>

        <!-- Empty state -->
        <div v-else-if="!loadError && conversations.length === 0 && !loading" class="py-16 text-center">
          <n-empty description="暂无对话记录">
            <template #extra>
              <n-button type="primary" @click="startNewChat">开始新对话</n-button>
            </template>
          </n-empty>
        </div>

        <div v-else class="space-y-2">
          <div
            v-for="item in conversations"
            :key="item.id"
            class="group flex items-center justify-between p-4 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 hover:border-blue-300 dark:hover:border-blue-600 cursor-pointer transition-colors"
            @click="viewConversation(item)"
          >
            <div class="flex items-center gap-4 flex-1 min-w-0">
              <div class="w-10 h-10 rounded-lg bg-blue-100 dark:bg-blue-900/30 flex items-center justify-center flex-shrink-0">
                <n-icon size="20" class="text-blue-600 dark:text-blue-400"><ChatbubblesOutline /></n-icon>
              </div>
              <div class="flex-1 min-w-0">
                <h3 class="font-medium text-gray-800 dark:text-gray-100 truncate">{{ item.title || '未命名对话' }}</h3>
                <div class="flex items-center gap-3 text-xs text-gray-400 mt-1">
                  <span>{{ formatDate(item.updated_at) }}</span>
                  <span>{{ item.message_count || 0 }} 条消息</span>
                </div>
              </div>
            </div>

            <div class="flex items-center gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
              <n-button size="small" quaternary @click.stop="viewConversation(item)">继续</n-button>
              <n-button size="small" quaternary type="error" @click.stop="deleteItem(item)">
                <template #icon><n-icon><TrashOutline /></n-icon></template>
              </n-button>
            </div>
          </div>
        </div>
      </n-spin>

      <!-- 分页 -->
      <div v-if="conversations.length > 0" class="mt-6 flex justify-center">
        <n-pagination
          v-model:page="pagination.page"
          v-model:page-size="pagination.pageSize"
          :item-count="pagination.itemCount"
          :page-sizes="[10, 20, 50]"
          show-size-picker
          @update:page="handlePageChange"
          @update:page-size="handlePageSizeChange"
        />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { SearchOutline, RefreshOutline, ChatbubblesOutline, TrashOutline, AddOutline } from '@vicons/ionicons5'
import { getConversations, deleteConversation } from '@/api/conversation'
import { useDialog } from 'naive-ui'
import { useDedupedMessage } from '@/utils/message'
import dayjs from 'dayjs'

interface Conversation {
  id: string
  title: string
  created_at: string
  updated_at: string
  message_count: number
  last_message?: string
}

const { t } = useI18n()
const message = useDedupedMessage()
const dialog = useDialog()
const router = useRouter()
const loading = ref(false)
const loadError = ref(false)
const loadErrorMsg = ref('')
const searchText = ref('')
const conversations = ref<Conversation[]>([])

const pagination = reactive({
  page: 1,
  pageSize: 10,
  itemCount: 0
})

const loadConversations = async () => {
  loading.value = true
  try {
    const params = { page: pagination.page, page_size: pagination.pageSize, search: searchText.value }
    const response = await getConversations(params)
    if (response.data?.data) {
      conversations.value = Array.isArray(response.data.data.data) ? response.data.data.data : []
      pagination.itemCount = response.data.data.total || 0
    }
    loadError.value = false
  } catch (error) {
    message.error('加载失败')
    loadError.value = true
    loadErrorMsg.value = '加载失败'
  } finally {
    loading.value = false
  }
}

const handlePageChange = (page: number) => {
  pagination.page = page
  loadConversations()
}

const handlePageSizeChange = (pageSize: number) => {
  pagination.pageSize = pageSize
  pagination.page = 1
  loadConversations()
}

const viewConversation = (item: Conversation) => {
  router.push(`/chat?conversation_id=${item.id}`)
}

const startNewChat = () => {
  router.push('/chat')
}

const deleteItem = (item: Conversation) => {
  dialog.warning({
    title: t('common.confirm') || '确认删除',
    content: t('chat.confirmDelete') || '确定要删除此对话吗？删除后无法恢复。',
    positiveText: t('common.confirm'),
    negativeText: t('common.cancel'),
    onPositiveClick: async () => {
      try {
        await deleteConversation(item.id)
        message.success('删除成功')
        loadConversations()
      } catch (error) {
        message.error('删除失败')
      }
    }
  })
}

const formatDate = (date: string) => {
  return dayjs(date).format('MM-DD HH:mm')
}

onMounted(() => {
  loadConversations()
})
</script>
