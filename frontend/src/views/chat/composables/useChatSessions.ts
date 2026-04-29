import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import { useDedupedMessage } from '@/utils/message'
import { useI18n } from 'vue-i18n'
import { useChatStore } from '@/stores/chat'
import { useAppStore } from '@/stores/app'
import { getConversations } from '@/api/conversation'
import { deleteConversation } from '@/api/chat'

export function useChatSessions() {
  const router = useRouter()
  const message = useDedupedMessage()
  const { t } = useI18n()
  const chatStore = useChatStore()
  const appStore = useAppStore()

  const conversations = ref<any[]>([])
  const isListLoading = ref(false)
  const sidebarOpen = computed({
    get: () => !appStore.sidebarCollapsed,
    set: (val) => appStore.setSidebarCollapsed(!val)
  })

  const currentConversationId = computed(() => chatStore.currentConversation?.id)

  const fetchConversations = async () => {
    isListLoading.value = true
    try {
      const res = await getConversations({ page: 1, page_size: 50 })
      if (res.data.data && Array.isArray(res.data.data.data)) {
        conversations.value = res.data.data.data
      }
    } catch (error) {
      console.error(error)
    } finally {
      isListLoading.value = false
    }
  }

  const handleDeleteConversation = async (id: string) => {
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

  return {
    conversations,
    isListLoading,
    sidebarOpen,
    currentConversationId,
    fetchConversations,
    handleDeleteConversation
  }
}
