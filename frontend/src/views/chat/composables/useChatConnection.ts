import { ref, computed, onUnmounted } from 'vue'
import { useDedupedMessage } from '@/utils/message'
import { useI18n } from 'vue-i18n'
import { useChatStore } from '@/stores/chat'
import { useUserStore } from '@/stores/user'
import { wsService } from '@/utils/websocket'
import { sseService } from '@/utils/sseService'
import type { ChatMessage } from '@/types/chat'

export function useChatConnection(
  messages: { value: ChatMessage[] },
  scrollToBottom: (behavior?: ScrollBehavior, force?: boolean) => void,
  fetchConversations: () => Promise<void>
) {
  const message = useDedupedMessage()
  const { t } = useI18n()
  const chatStore = useChatStore()
  const userStore = useUserStore()

  const connectionStatus = ref<'connected' | 'disconnected' | 'connecting' | 'error'>('disconnected')
  const sseStatus = ref<'connected' | 'disconnected' | 'connecting' | 'error'>('disconnected')
  const useSSE = ref(false)
  const useStream = ref(true)
  const isLoading = ref(false)
  const isRetrieving = ref(false)

  const effectiveConnectionStatus = computed(() => {
    if (useSSE.value) return sseStatus.value
    return connectionStatus.value
  })

  let statusTimer: ReturnType<typeof setInterval> | null = null

  const connectWebSocket = async () => {
    if (useSSE.value) return

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
    wsService.on('error', (data: any) => {
      connectionStatus.value = 'error'
      if (data.content) message.error(data.content)
      isLoading.value = false
    })

    wsService.on('chunk', (data: any) => {
      isRetrieving.value = false
      if (data.conversationId && (!chatStore.currentConversation || chatStore.currentConversation.id !== data.conversationId)) {
        chatStore.setCurrentConversation({
          id: data.conversationId,
          title: data.title || (messages.value.find(m => m.messageType === 'user')?.content.slice(0, 20) || 'New Chat'),
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
        messages.value.push({ id: data.messageId, content: data.content || '', messageType: 'assistant', conversationId: data.conversationId || 0, sources: data.sources })
      }
      scrollToBottom()
    })

    wsService.on('message', (data: any) => {
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
            sources: data.sources,
            isCached
          } as any)
        } else {
          lastMsg.content = data.content
          if (data.sources) lastMsg.sources = data.sources
          if (isCached) (lastMsg as any).isCached = true
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

  const startStatusPolling = () => {
    statusTimer = setInterval(() => {
      if (wsService.isConnected()) {
        connectionStatus.value = 'connected'
      } else if (wsService.getConnectionStatus() === 'connecting') {
        connectionStatus.value = 'connecting'
      }
    }, 1000)
  }

  const stopStatusPolling = () => {
    if (statusTimer) {
      clearInterval(statusTimer)
      statusTimer = null
    }
  }

  const stopGeneration = () => {
    wsService.sendStop()
    isLoading.value = false
    isRetrieving.value = false
  }

  onUnmounted(() => {
    disconnectWebSocket()
    stopStatusPolling()
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
    startStatusPolling,
    stopGeneration
  }
}
