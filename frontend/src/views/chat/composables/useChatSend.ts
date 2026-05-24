import { ref } from 'vue'
import { useDedupedMessage } from '@/utils/message'
import { useI18n } from 'vue-i18n'
import { useChatStore } from '@/stores/chat'
import { useUserStore } from '@/stores/user'
import { wsService } from '@/utils/websocket'
import { sseService } from '@/utils/sseService'
import type { ChatMessage, AttachedFile, KnowledgeSource } from '@/types/chat'
import type { SSEMessage } from '@/types/api'

export function useChatSend(
  messages: { value: ChatMessage[] },
  attachedFiles: { value: AttachedFile[] },
  attachedFileIds: { value: string[] },
  scrollToBottom: (_behavior?: ScrollBehavior, _force?: boolean) => void,
  fetchConversations: () => Promise<void>,
  isLoading: { value: boolean },
  isRetrieving: { value: boolean },
  sseStatus: { value: string },
  useSSE: { value: boolean },
  useAgent: { value: boolean }
) {
  const message = useDedupedMessage()
  const { t } = useI18n()
  const chatStore = useChatStore()
  const userStore = useUserStore()

  const inputMessage = ref('')
  const strictMode = ref(false)
  const privacyMode = ref(true)

  const handleSSESend = async () => {
    let userMessage = inputMessage.value.trim()
    if (!userMessage && attachedFileIds.value.length > 0) {
      userMessage = t('chat.analyzeFiles', { n: attachedFiles.value.length })
    }
    if (!userMessage) return

    inputMessage.value = ''
    const currentFiles = [...attachedFiles.value]
    attachedFiles.value = []

    const userMsgId = Date.now()
    messages.value.push({
      id: userMsgId,
      content: userMessage,
      messageType: 'user',
      conversationId: chatStore.currentConversation?.id || 0,
      files: currentFiles.map(f => ({ ...f }))
    })
    scrollToBottom()
    isLoading.value = true
    isRetrieving.value = true

    const aiMsgId = 'sse-' + Date.now()
    let fullContent = ''
    messages.value.push({
      id: aiMsgId,
      content: '',
      messageType: 'assistant',
      conversationId: chatStore.currentConversation?.id || 0
    })

    sseService.off('chunk')
    sseService.off('message')
    sseService.off('error')
    sseService.off('retry')
    sseService.off('thinking')
    sseService.off('tool_call')
    sseService.off('tool_result')
    sseService.off('tool_error')

    const onChunk = (data: SSEMessage) => {
      sseStatus.value = 'connected'
      if (data.conversationId && (!chatStore.currentConversation || chatStore.currentConversation.id !== data.conversationId)) {
        chatStore.setCurrentConversation({
          id: data.conversationId,
          title: userMessage.slice(0, 20),
          userId: userStore.userInfo?.id || 0,
          createdAt: new Date().toISOString(),
          updatedAt: new Date().toISOString()
        })
        fetchConversations()
      }
      isRetrieving.value = false
      const lastMsg = messages.value[messages.value.length - 1]
      if (lastMsg && lastMsg.messageType === 'assistant' && lastMsg.id === aiMsgId) {
        fullContent += data.content || ''
        lastMsg.content = fullContent
        if (data.sources) lastMsg.sources = data.sources as KnowledgeSource[]
      }
      scrollToBottom()
    }

    const onMessage = (data: SSEMessage) => {
      isLoading.value = false
      isRetrieving.value = false
      sseStatus.value = 'connected'
      const lastMsg = messages.value[messages.value.length - 1]
      if (lastMsg && lastMsg.id === aiMsgId) {
        lastMsg.content = data.content || fullContent
        if (data.sources) lastMsg.sources = data.sources as KnowledgeSource[]
        if ((data as unknown as Record<string, unknown>).is_cached) lastMsg.isCached = true
      }
      fetchConversations()
      scrollToBottom()
    }

    const onThinking = (data: SSEMessage) => {
      if (['reasoning', 'planning', 'correction'].includes(data.thinkingType || '')) {
        const lastMsg = messages.value[messages.value.length - 1]
        if (lastMsg && lastMsg.messageType === 'assistant' && lastMsg.id === aiMsgId) {
          lastMsg.thinking = (lastMsg.thinking || '') + data.content
        }
      }
    }

    const onToolCall = (data: SSEMessage) => {
      const lastMsg = messages.value[messages.value.length - 1]
      if (lastMsg && lastMsg.messageType === 'assistant' && lastMsg.id === aiMsgId) {
        const stamp = `\n\n> 🔧 正在调用: **${data.toolName || 'tool'}**...\n`
        fullContent += stamp
        lastMsg.content = fullContent
      }
      scrollToBottom()
    }

    const onToolResult = (data: SSEMessage) => {
      const lastMsg = messages.value[messages.value.length - 1]
      if (lastMsg && lastMsg.messageType === 'assistant' && lastMsg.id === aiMsgId) {
        const stamp = `\n> ✅ 工具完成` + (data.toolDurationMs ? ` (${Math.round(data.toolDurationMs)}ms)` : '') + `\n`
        fullContent += stamp
        lastMsg.content = fullContent
      }
    }

    const onToolError = (data: SSEMessage) => {
      const lastMsg = messages.value[messages.value.length - 1]
      if (lastMsg && lastMsg.messageType === 'assistant' && lastMsg.id === aiMsgId) {
        const stamp = `\n> ⚠️ 工具调用异常: ${data.content || '未知错误'}\n`
        fullContent += stamp
        lastMsg.content = fullContent
      }
    }

    const onError = (data: SSEMessage) => {
      isLoading.value = false
      isRetrieving.value = false
      sseStatus.value = 'error'
      message.error(data.content || t('chat.sseError'))
    }

    sseService.on('chunk', onChunk)
    sseService.on('message', onMessage)
    sseService.on('error', onError)
    sseService.on('thinking', onThinking)
    sseService.on('tool_call', onToolCall)
    sseService.on('tool_result', onToolResult)
    sseService.on('tool_error', onToolError)

    const systemPrompt = localStorage.getItem('activeSystemPrompt')

    try {
      const sent = await sseService.post('stream', {
        content: userMessage,
        conversationId: chatStore.currentConversation?.id,
        fileIds: currentFiles.filter(f => f.status === 'done' && f.id).map(f => f.id!),
        useAgent: useAgent.value,
        payload: {
          strictMode: strictMode.value,
          privacyMode: privacyMode.value,
          systemPrompt: systemPrompt || undefined
        }
      })

      if (!sent) {
        message.error(t('chat.sendFailed'))
        isLoading.value = false
        attachedFiles.value = currentFiles
      }
    } catch (error) {
      message.error(t('chat.networkError'))
      isLoading.value = false
      attachedFiles.value = currentFiles
    }
  }

  const handleSend = async () => {
    if (attachedFiles.value.some(f => f.status === 'uploading')) {
      message.warning(t('chat.waitForUpload'))
      return
    }

    const pendingFiles = attachedFiles.value.filter(f => ['parsing', 'indexing'].includes(f.status))
    if (pendingFiles.length > 0) {
      message.warning(t('chat.waitForParsing'))
      return
    }

    if (attachedFiles.value.some(f => f.status === 'error')) {
      message.warning(t('chat.removeFailedAttachments'))
      return
    }

    let userMessage = inputMessage.value.trim()
    if (!userMessage && attachedFileIds.value.length > 0) {
      userMessage = t('chat.analyzeFiles', { n: attachedFiles.value.length })
    } else if (!userMessage && attachedFiles.value.length > 0 && attachedFileIds.value.length === 0) {
      message.warning(t('chat.attachmentsNotReady'))
      return
    }
    if (!userMessage) return
    if (isLoading.value) return

    if (useSSE.value) {
      await handleSSESend()
      return
    }

    if (!wsService.isConnected()) {
      message.warning(t('chat.connecting'))
      return
    }

    inputMessage.value = ''
    const currentFiles = [...attachedFiles.value]
    attachedFiles.value = []

    messages.value.push({
      id: Date.now(),
      content: userMessage,
      messageType: 'user',
      conversationId: chatStore.currentConversation?.id || 0,
      files: currentFiles.map(f => ({ ...f }))
    })
    scrollToBottom()
    isLoading.value = true
    isRetrieving.value = true

    try {
      const sent = wsService.send(
        userMessage,
        chatStore.currentConversation?.id,
        currentFiles.filter(f => f.status === 'done' && f.id).map(f => f.id!),
        strictMode.value,
        privacyMode.value
      )
      if (!sent) {
        message.error(t('chat.sendFailed'))
        isLoading.value = false
        attachedFiles.value = currentFiles
      }
    } catch (error) {
      message.error(t('chat.networkError'))
      isLoading.value = false
      attachedFiles.value = currentFiles
    }
  }

  const handleKeydown = (e: KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) handleSend()
  }

  const regenerateMessage = async (previousUserMessage: string) => {
    if (!previousUserMessage || isLoading.value) return
    inputMessage.value = previousUserMessage
    await handleSend()
  }

  return {
    inputMessage,
    strictMode,
    privacyMode,
    handleSend,
    handleSSESend,
    handleKeydown,
    regenerateMessage
  }
}
