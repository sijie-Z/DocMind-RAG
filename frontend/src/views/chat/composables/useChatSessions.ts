import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import { useDedupedMessage } from '@/utils/message'
import { useI18n } from 'vue-i18n'
import { useDialog } from 'naive-ui'
import { useChatStore } from '@/stores/chat'
import { useAppStore } from '@/stores/app'
import { getConversations, deleteConversation, type Conversation } from '@/api/conversation'

export function useChatSessions() {
  const router = useRouter()
  const message = useDedupedMessage()
  const dialog = useDialog()
  const { t } = useI18n()
  const chatStore = useChatStore()
  const appStore = useAppStore()

  const conversations = ref<Conversation[]>([])
  const isListLoading = ref(false)
  const convLoadError = ref(false)
  const currentPage = ref(1)
  const hasMore = ref(true)
  const sidebarOpen = computed({
    get: () => !appStore.sidebarCollapsed,
    set: (val) => appStore.setSidebarCollapsed(!val)
  })

  const currentConversationId = computed(() => chatStore.currentConversation?.id)

  const fetchConversations = async () => {
    isListLoading.value = true
    try {
      const res = await getConversations({ page: currentPage.value, page_size: 50 })
      if (res.data.data && Array.isArray(res.data.data.data)) {
        const items = res.data.data.data
        if (currentPage.value === 1) {
          conversations.value = items
        } else {
          conversations.value = [...conversations.value, ...items]
        }
        hasMore.value = items.length >= 50
      } else {
        hasMore.value = false
      }
      convLoadError.value = false
    } catch (error) {
      message.error(t('common.loadFailed'))
      convLoadError.value = true
    } finally {
      isListLoading.value = false
    }
  }

  const loadMore = async () => {
    if (!hasMore.value || isListLoading.value) return
    currentPage.value++
    await fetchConversations()
  }

  const handleDeleteConversation = async (id: string) => {
    dialog.warning({
      title: t('common.confirm') || '确认删除',
      content: t('chat.confirmDelete') || '删除后无法恢复，确定要删除此对话吗？',
      positiveText: t('common.confirm'),
      negativeText: t('common.cancel'),
      onPositiveClick: async () => {
        try {
          await deleteConversation(id)
          message.success(t('common.deleteSuccess'))
          if (currentConversationId.value === id) {
            chatStore.setCurrentConversation(null)
            router.push({ query: {} })
          }
          await fetchConversations()
        } catch (error) {
          message.error(t('common.deleteFailed'))
        }
      }
    })
  }

  return {
    conversations,
    isListLoading,
    convLoadError,
    currentPage,
    hasMore,
    sidebarOpen,
    currentConversationId,
    fetchConversations,
    loadMore,
    handleDeleteConversation
  }
}
