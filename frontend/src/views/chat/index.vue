<template>
  <DocumentPreviewModal
    v-model:show="showPreviewModal"
    :loading="previewLoading"
    :doc="previewDoc"
    :content="previewContent"
    @download="handleDownload"
  />

  <div class="flex h-full w-full bg-gray-50 dark:bg-gray-950 overflow-hidden transition-colors duration-300">
    <input
      type="file"
      id="global-chat-file-input"
      style="display: none;"
      @change="handleFileUpload"
      accept=".pdf,.doc,.docx,.txt,.md"
    />

    <ChatSidebar
      :sidebarOpen="sidebarOpen"
      :conversations="conversations"
      :isListLoading="isListLoading"
      :currentConversationId="currentConversationId"
      @newConversation="newConversation"
      @refresh="fetchConversations"
      @selectConversation="handleSelectConversation"
      @deleteConversation="handleDeleteConversation"
    />

    <main class="flex-1 flex flex-col h-full relative min-w-0">
      <ChatHeader
        :sidebarOpen="sidebarOpen"
        :title="chatStore.currentConversation?.title"
        :hasConversation="!!currentConversationId"
        :isBoundMode="isBoundMode"
        @toggleSidebar="toggleSidebar"
        @clearChat="clearChat"
        @unbind="handleUnbind"
      />

      <ChatMessages
        ref="chatMessagesRef"
        :messages="messages"
        :isLoading="isLoading"
        :showBackToBottom="showBackToBottom"
        :userAvatar="userStore.userInfo?.avatar"
        :suggestions="suggestions"
        @scroll="handleScroll"
        @scrollToBottom="scrollToBottom"
        @useSuggestion="useSuggestion"
        @feedback="handleFeedback"
        @copy="copyText"
      />

      <ChatInput
        v-model:inputMessage="inputMessage"
        v-model:strictMode="strictMode"
        v-model:privacyMode="privacyMode"
        v-model:useSSE="useSSE"
        v-model:useStream="useStream"
        :isLoading="isLoading"
        :attachedFiles="attachedFiles"
        :attachedFileIds="attachedFileIds"
        :connectionStatus="effectiveConnectionStatus"
        :connectionStatusText="getConnectionStatusText(effectiveConnectionStatus)"
        @send="handleSend"
        @stopGeneration="stopGeneration"
        @triggerFileUpload="triggerFileUpload"
        @removeAttachment="removeAttachment"
      />
    </main>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useChatStore } from '@/stores/chat'
import { useUserStore } from '@/stores/user'
import { useAppStore } from '@/stores/app'
import { useDedupedMessage } from '@/utils/message'
import { getConversationMessages, clearConversationMessages } from '@/api/chat'
import { getDocumentDetail, getDocumentContent } from '@/api/knowledge'
import { getToken } from '@/utils/auth'

import { ChatSidebar, ChatMessages, ChatInput, ChatHeader, DocumentPreviewModal } from './components'
import { useChatAttachments, useChatMessages, useChatSessions, useChatConnection, useChatSend } from './composables'

const message = useDedupedMessage()
const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const chatStore = useChatStore()
const userStore = useUserStore()
const appStore = useAppStore()

const chatMessagesRef = ref()

const {
  messages, chatContainer, isUserScrolling, showBackToBottom, autoFollowResponse,
  handleScroll: baseHandleScroll, scrollToBottom: baseScrollToBottom,
  handleFeedback, copyText
} = useChatMessages()

const {
  attachedFiles, attachedFileIds, fileInputRef,
  triggerFileUpload, handleFileUpload: baseHandleFileUpload, removeAttachment
} = useChatAttachments()

const {
  conversations, isListLoading, sidebarOpen, currentConversationId,
  fetchConversations, handleDeleteConversation
} = useChatSessions()

const {
  connectionStatus, sseStatus, useSSE, useStream, isLoading, isRetrieving,
  effectiveConnectionStatus, connectWebSocket, startStatusPolling, stopGeneration
} = useChatConnection(messages, baseScrollToBottom, fetchConversations)

const {
  inputMessage, strictMode, privacyMode, handleSend: baseHandleSend, handleKeydown
} = useChatSend(
  messages, attachedFiles, attachedFileIds,
  baseScrollToBottom, fetchConversations,
  isLoading, isRetrieving, sseStatus, useSSE
)

const handleScroll = baseHandleScroll
const scrollToBottom = baseScrollToBottom

const handleFileUpload = async (event: Event) => {
  await baseHandleFileUpload(event)
}

const handleSend = baseHandleSend

const suggestions = computed(() => [
  { title: t('chat.suggestions.quantum'), desc: t('chat.suggestions.quantumDesc') },
  { title: t('chat.suggestions.code'), desc: t('chat.suggestions.codeDesc') },
  { title: t('chat.suggestions.report'), desc: t('chat.suggestions.reportDesc') },
  { title: t('chat.suggestions.travel'), desc: t('chat.suggestions.travelDesc') }
])

const useSuggestion = (s: any) => { inputMessage.value = s.title + '，' + s.desc }

const isBoundMode = computed(() => {
  return chatStore.currentConversation?.settings?.bound_document_ids?.length > 0
})

const handleUnbind = async () => {
  try {
    await chatStore.unbindDocuments()
    message.success(t('chat.unbindSuccess'))
  } catch {
    message.error(t('chat.unbindFailed'))
  }
}

const checkScreenSize = () => {
  if (window.innerWidth < 768) appStore.setSidebarCollapsed(true)
  else appStore.setSidebarCollapsed(false)
}

const toggleSidebar = () => appStore.toggleSidebar()

const newConversation = () => {
  messages.value = []
  attachedFiles.value = []
  chatStore.setCurrentConversation(null)
  router.push({ query: {} })
  if (window.innerWidth < 768) sidebarOpen.value = false
}

const handleSelectConversation = async (conv: any) => {
  if (currentConversationId.value === conv.id) return
  router.push({ query: { conversation_id: conv.id } })
  await loadConversation(conv.id)
  if (window.innerWidth < 768) sidebarOpen.value = false
}

const loadConversation = async (id: string) => {
  isLoading.value = true
  try {
    const res = await getConversationMessages(id)
    if (res.data) {
      const data = (res.data as any).data || res.data
      const msgs = data.messages || []
      chatStore.setCurrentConversation({
        id: data.id, title: data.title, userId: userStore.userInfo?.id || 0,
        createdAt: data.created_at, updatedAt: data.updated_at || data.created_at,
        settings: data.settings
      })
      messages.value = msgs.map((m: any) => ({
        id: m.id, content: m.content, messageType: m.message_type || m.messageType,
        conversationId: data.id, createdAt: m.created_at, sources: m.sources, files: m.files
      }))
    }
  } catch {
    message.error(t('chat.historyFailed'))
  } finally {
    isLoading.value = false
    scrollToBottom('auto', true)
  }
}

const clearChat = async () => {
  if (!currentConversationId.value) return
  try {
    const res = await clearConversationMessages(String(currentConversationId.value))
    if (res.data?.success) {
      messages.value = []
      message.success(t('chat.chatCleared'))
    }
  } catch {
    message.error(t('chat.clearFailed'))
  }
}

const showPreviewModal = ref(false)
const previewLoading = ref(false)
const previewDoc = ref<any>(null)
const previewContent = ref('')

const handlePreviewFile = async (fileId?: string) => {
  if (!fileId) return
  showPreviewModal.value = true
  previewLoading.value = true
  previewDoc.value = null
  previewContent.value = ''
  try {
    const [statusRes, contentRes] = await Promise.all([
      getDocumentDetail(fileId),
      getDocumentContent(fileId)
    ])
    if (statusRes.data?.data) {
      const data = statusRes.data.data
      previewDoc.value = {
        id: data.id, filename: data.filename, file_size: data.file_size,
        created_at: data.created_at, description: data.description, keywords: data.keywords,
        source: data.upload_source || (data.description?.includes('来自聊天') ? '聊天上传' : '知识库上传')
      }
    }
    if (contentRes.data?.data) {
      previewContent.value = contentRes.data.data.content
      if (previewDoc.value) {
        previewDoc.value.summary = contentRes.data.data.summary || ''
        previewDoc.value.suggested_tags = contentRes.data.data.suggested_tags || []
      }
    }
  } catch {
    message.error(t('chat.previewFailed'))
  } finally {
    previewLoading.value = false
  }
}

const handleDownload = (fileId?: string) => {
  if (!fileId) return
  window.open(`${import.meta.env.VITE_API_URL || ''}/api/v1/documents/${fileId}/download`, '_blank')
}

const getConnectionStatusText = (status?: string) => {
  const s = status || effectiveConnectionStatus.value
  switch (s) {
    case 'connected': return t('chat.status.connected')
    case 'connecting': return t('chat.status.connecting')
    case 'disconnected': return t('chat.status.disconnected')
    case 'error': return t('chat.status.error')
    default: return t('chat.status.unknown')
  }
}

onMounted(async () => {
  const token = userStore.token || getToken()
  if (!token) { router.push({ name: 'Login' }); return }
  checkScreenSize()
  window.addEventListener('resize', checkScreenSize)
  await fetchConversations()
  const conversationId = route.query.conversation_id as string
  if (conversationId) await loadConversation(conversationId)

  const promptContent = route.query.prompt as string
  const promptName = route.query.promptName as string
  if (promptContent) {
    router.replace({ query: { ...route.query, prompt: undefined, promptName: undefined } })
    inputMessage.value = ''
    message.info(`已应用提示词模板"${promptName || '未命名'}"，请开始对话`)
    localStorage.setItem('activeSystemPrompt', promptContent)
    localStorage.setItem('activeSystemPromptName', promptName || '')
  }

  startStatusPolling()
  watch(() => userStore.userInfo?.id, () => { connectWebSocket() }, { immediate: true })
})
</script>

<style scoped>
.scrollbar-thin::-webkit-scrollbar { width: 4px; }
.scrollbar-thin::-webkit-scrollbar-track { background: transparent; }
.scrollbar-thin::-webkit-scrollbar-thumb { background-color: rgba(156, 163, 175, 0.3); border-radius: 20px; }
.dark .scrollbar-thin::-webkit-scrollbar-thumb { background-color: rgba(156, 163, 175, 0.1); }
.scrollbar-thin:hover::-webkit-scrollbar-thumb { background-color: rgba(156, 163, 175, 0.5); }
</style>

<style>
.markdown-body { color: inherit; font-size: 0.95rem; line-height: 1.6; }
.markdown-body p { margin-bottom: 0.8em; }
.markdown-body p:last-child { margin-bottom: 0; }
.markdown-body pre { background-color: #f6f8fa; border-radius: 6px; padding: 12px; margin: 10px 0; overflow-x: auto; }
.dark .markdown-body pre { background-color: #1f2937; color: #e5e7eb; }
.markdown-body code { font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace; background-color: rgba(175, 184, 193, 0.2); padding: 0.2em 0.4em; border-radius: 4px; font-size: 85%; }
.dark .markdown-body code { background-color: rgba(110, 118, 129, 0.4); color: #e5e7eb; }
.markdown-body pre code { background-color: transparent; padding: 0; color: inherit; }
.markdown-body ul, .markdown-body ol { padding-left: 1.5em; margin-bottom: 0.8em; }
.markdown-body li { margin-bottom: 0.2em; }

@keyframes fade-in { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
.animate-fade-in { animation: fade-in 0.5s ease-out forwards; }
@keyframes slide-in-bottom { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }
.animate-slide-in-bottom { animation: slide-in-bottom 0.3s ease-out forwards; }
</style>
