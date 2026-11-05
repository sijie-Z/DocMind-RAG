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
  fetchConversations: () => Promise<void>,
  getGeneration: () => string = () => '',
) {
  const message = useDedupedMessage()
  const { t } = useI18n()
  const chatStore = useChatStore()
  const userStore = useUserStore()

  // idle = 就绪但当前没有在途连接（SSE 空闲态、WS 手动断开）；避免把
  // 「还没开始」渲染成「未连接」告警。error 一旦出现则保持到重新连上为止。
  const connectionStatus = ref<'idle' | 'connected' | 'disconnected' | 'connecting' | 'error'>('idle')
  const sseStatus = ref<'idle' | 'connected' | 'disconnected' | 'connecting' | 'error'>('idle')
  const useSSE = ref(true)
  const useStream = ref(true)
  const isLoading = ref(false)
  const isRetrieving = ref(false)

  const effectiveConnectionStatus = computed(() => {
    if (useSSE.value) return sseStatus.value
    return connectionStatus.value
  })

  const connectWebSocket = async () => {
    const generation = getGeneration()
    const ownsGeneration = () => generation === getGeneration()
    if (useSSE.value) return
    if (!userStore.isLoggedIn) return

    let userId = userStore.userInfo?.id
    if (!userId || userId <= 0) {
      try {
        await userStore.getUserInfo()
        if (!ownsGeneration()) return
        userId = userStore.userInfo?.id
        await new Promise(resolve => setTimeout(resolve, 100))
        if (!ownsGeneration()) return
      } catch { return }
    }
    if (!userId || userId <= 0 || !ownsGeneration()) return

    connectionStatus.value = 'connecting'

    wsService.off('connect'); wsService.off('disconnect'); wsService.off('error'); wsService.off('message'); wsService.off('chunk')

    wsService.on('connect', () => {
      if (ownsGeneration()) connectionStatus.value = 'connected'
    })
    wsService.on('disconnect', () => {
      // 失败后连接器会继续自动重连：状态保持在 error，直到真正重连成功，
      // 不把一次失败「降级」成看似正常的未连接。
      if (!ownsGeneration()) return
      if (connectionStatus.value !== 'error') connectionStatus.value = 'disconnected'
    })
    wsService.on('error', (data: WebSocketMessage) => {
      if (!ownsGeneration()) return
      connectionStatus.value = 'error'
      if (data.content) message.error(data.content)
      isLoading.value = false
    })

    wsService.on('chunk', (data: WebSocketMessage) => {
      if (!ownsGeneration()) return
      isRetrieving.value = false
      if (data.conversationId && (!chatStore.currentConversation || chatStore.currentConversation.id !== data.conversationId)) {
        chatStore.setCurrentConversation({
          id: data.conversationId,
          // chat WS 三类载荷（chunk/message/error，chat.py:520-545）均无 title 键——
          // 旧读点 data.title 恒 undefined（B41）；直接用首条用户消息回退命名
          title: messages.value.find(m => m.messageType === 'user')?.content?.slice(0, 20) || t('chat.newConversation'),
          userId: userId!,
          createdAt: new Date().toISOString(),
          updatedAt: new Date().toISOString()
        })
        fetchConversations()
      }
      // W2-T7 项 4：流式开关关闭时 chunk 不逐字上屏，等待 'message' 终态一次性落屏
      // （WS 与 SSE 两条传送带同一开关语义）。
      if (!useStream.value) {
        scrollToBottom()
        return
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
      if (!ownsGeneration()) return
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
    // 主动断开 = 回到就绪，而不是断线。
    connectionStatus.value = 'idle'
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
