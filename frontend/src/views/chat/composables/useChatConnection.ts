import { ref, computed, onUnmounted } from 'vue'
import { useDedupedMessage } from '@/utils/message'
import { useI18n } from 'vue-i18n'
import { useChatStore } from '@/stores/chat'
import { useUserStore } from '@/stores/user'
import { wsService } from '@/utils/websocket'
import type { ChatMessage, KnowledgeSource } from '@/types/chat'
import type { WebSocketMessage } from '@/utils/websocket'

export function useChatConnection(
  messages: { value: ChatMessage[] },
  scrollToBottom: (_behavior?: ScrollBehavior, _force?: boolean) => void,
  fetchConversations: () => Promise<void>
) {
  const message = useDedupedMessage()
  const { t } = useI18n()
  const chatStore = useChatStore()
  const userStore = useUserStore()

  const connectionStatus = ref<'connected' | 'disconnected' | 'connecting' | 'error'>('disconnected')
  const sseStatus = ref<'connected' | 'disconnected' | 'connecting' | 'error'>('disconnected')
  const useSSE = ref(true)
  const useStream = ref(true)
  const isLoading = ref(false)
  const isRetrieving = ref(false)

  const effectiveConnectionStatus = computed(() => {
    if (useSSE.value) return sseStatus.value
    return connectionStatus.value
  })

  const connectWebSocket = async () => {
    if (useSSE.value) return
    if (!userStore.isLoggedIn) return

    let userId = userStore.userInfo?.id
    if (!userId || userId <= 0) {
      try {
        await userStore.getUserInfo()
        userId = userStore.userInfo?.id
        await new Promise(resolve => setTimeout(resolve, 100))
      } catch (e) { return }
    }
    if (!userId || userId <= 0) return

    connectionStatus.value = 'connecting'

    wsService.off('connect'); wsService.off('disconnect'); wsService.off('error'); wsService.off('message'); wsService.off('chunk')

    wsService.on('connect', () => { connectionStatus.value = 'connected' })
    wsService.on('disconnect', () => { connectionStatus.value = 'disconnected' })
    wsService.on('error', (data: WebSocketMessage) => {
      connectionStatus.value = 'error'
      if (data.content) message.error(data.content)
      isLoading.value = false
    })

    wsService.on('chunk', (data: WebSocketMessage) => {
      isRetrieving.value = false
      if (data.conversationId && (!chatStore.currentConversation || chatStore.currentConversation.id !== data.conversationId)) {
        chatStore.setCurrentConversation({
          id: data.conversationId,
          title: data.title || (messages.value.find(m => m.messageType === 'user')?.content.slice(0, 20) || t('chat.newConversation')),
          userId: userId!,
          createdAt: new Date().toISOString(),
          updatedAt: new Date().toISOString()
        })
        fetchConversations()
      }
      const lastMsg = messages.value[messages.value.length - 1]
      if (lastMsg && lastMsg.messageType === 'assistant' && lastMsg.id === data.messageId) {
        lastMsg.content += (data.content || '')
      } else {
        messages.value.push({ id: data.messageId, content: data.content || '', messageType: 'assistant', conversationId: data.conversationId || 0, sources: data.sources as KnowledgeSource[] | undefined })
      }
      scrollToBottom()
    })

    wsService.on('message', (data: WebSocketMessage) => {
      if (data.content) {
        isRetrieving.value = false
        const lastMsg = messages.value[messages.value.length - 1]
        const isCached = data.is_cached
        if (!lastMsg || lastMsg.id !== data.messageId) {
          messages.value.push({
            id: data.messageId,
            content: data.content,
            messageType: 'assistant',
            conversationId: data.conversationId || chatStore.currentConversation?.id || 0,
            sources: data.sources as KnowledgeSource[] | undefined,
            isCached
          })
        } else {
          lastMsg.content = data.content
          if (data.sources) lastMsg.sources = data.sources as KnowledgeSource[]
          if (isCached) lastMsg.isCached = true
        }
        isLoading.value = false
        scrollToBottom()
        fetchConversations()
      }
    })

    wsService.connect(userId, chatStore.currentConversation?.id)
  }

  const disconnectWebSocket = () => {
    if (wsService && typeof wsService.disconnect === 'function') wsService.disconnect()
    connectionStatus.value = 'disconnected'
  }

  const stopGeneration = () => {
    wsService.sendStop()
    isLoading.value = false
    isRetrieving.value = false
  }

  onUnmounted(() => {
    disconnectWebSocket()
  })

  return {
    connectionStatus,
    sseStatus,
    useSSE,
    useStream,
    isLoading,
    isRetrieving,
    effectiveConnectionStatus,
    connectWebSocket,
    disconnectWebSocket,
    stopGeneration
  }
}
