<template>
  <DocumentPreviewModal
    v-model:show="showPreviewModal"
    :loading="previewLoading"
    :doc="previewDoc"
    :content="previewContent"
    @download="handleDownload"
  />

  <div class="flex h-full w-full bg-gray-50 dark:bg-gray-950 overflow-hidden transition-colors duration-300">
    <!-- Mobile overlay for sidebar -->
    <div
      v-if="sidebarOpen && windowWidth < 768"
      class="fixed inset-0 z-10 bg-black/40 md:hidden"
      @click="toggleSidebar"
    />

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
      :convLoadError="convLoadError"
      :currentConversationId="currentConversationId"
      @newConversation="newConversation"
      @refresh="fetchConversations"
      @selectConversation="handleSelectConversation"
      @deleteConversation="handleDeleteConversation"
    />

    <main
      class="flex-1 flex flex-col h-full relative min-w-0"
      @dragover.prevent="onDragOver"
      @dragenter.prevent="onDragEnter"
      @dragleave.prevent="onDragLeave"
      @drop.prevent="onDrop"
    >
      <ChatHeader
        :sidebarOpen="sidebarOpen"
        :title="chatStore.currentConversation?.title"
        :hasConversation="!!currentConversationId"
        :isBoundMode="isBoundMode"
        @toggleSidebar="toggleSidebar"
        @clearChat="clearChat"
        @unbind="handleUnbind"
        @exportChat="handleExportChat"
      />

      <!-- Mobile back-to-conversations button -->
      <div
        v-if="sidebarOpen && windowWidth < 768"
        class="fixed left-72 top-20 z-20 md:hidden"
      >
        <n-button size="tiny" quaternary @click="toggleSidebar" class="bg-white/90 dark:bg-gray-800/90 shadow-sm rounded-lg">
          <template #icon><n-icon><ArrowBackOutline /></n-icon></template>
          {{ t('chat.backToConversations') || '返回对话列表' }}
        </n-button>
      </div>

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
        @regenerate="handleRegenerate"
      />

      <!-- Error recovery banner for message loading -->
      <div v-if="msgLoadError" class="flex items-center justify-center gap-3 py-3 px-4 mx-4 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800/40 rounded-lg">
        <n-icon size="18" class="text-red-500"><AlertCircleOutline /></n-icon>
        <span class="text-sm text-red-600 dark:text-red-400">{{ t('chat.msgLoadFailed') }}</span>
        <n-button size="tiny" type="error" ghost @click="msgLoadError = false; loadConversation(String(currentConversationId!))">{{ t('chat.retry') }}</n-button>
      </div>

      <!-- Drag-and-drop overlay -->
      <transition name="fade">
        <div
          v-if="isDragging"
          class="absolute inset-0 z-50 flex items-center justify-center bg-blue-500/10 dark:bg-blue-400/10 backdrop-blur-sm border-2 border-dashed border-blue-400 dark:border-blue-500 rounded-lg pointer-events-none"
        >
          <div class="flex flex-col items-center gap-3">
            <n-icon size="48" class="text-blue-500 dark:text-blue-400">
              <CloudUploadOutline />
            </n-icon>
            <span class="text-lg font-semibold text-blue-600 dark:text-blue-400">{{ t('chat.dropFiles') }}</span>
          </div>
        </div>
      </transition>

      <ChatInput
        v-model:inputMessage="inputMessage"
        v-model:strictMode="strictMode"
        v-model:privacyMode="privacyMode"
        v-model:useSSE="useSSE"
        v-model:useStream="useStream"
        v-model:useAgent="useAgent"
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
import { ref, computed, onMounted, watch, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useChatStore } from '@/stores/chat'
import { useUserStore } from '@/stores/user'
import { useAppStore } from '@/stores/app'
import { useDedupedMessage } from '@/utils/message'
import { CloudUploadOutline, AlertCircleOutline, ArrowBackOutline } from '@vicons/ionicons5'
import { getConversationMessages, clearConversationMessages } from '@/api/chat'
import type { Conversation } from '@/api/conversation'
import type { ChatMessage } from '@/types/chat'
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
const msgLoadError = ref(false)
const windowWidth = ref(window.innerWidth)

const {
  messages, showBackToBottom,
  handleScroll: baseHandleScroll, scrollToBottom: baseScrollToBottom,
  handleFeedback, copyText
} = useChatMessages()

const {
  attachedFiles, attachedFileIds,
  triggerFileUpload, handleFileUpload: baseHandleFileUpload, removeAttachment
} = useChatAttachments()

const {
  conversations, isListLoading, sidebarOpen, currentConversationId,
  fetchConversations, handleDeleteConversation, convLoadError
} = useChatSessions()

const {
  sseStatus, useSSE, useStream, isLoading, isRetrieving,
  effectiveConnectionStatus, connectWebSocket, stopGeneration
} = useChatConnection(messages, baseScrollToBottom, fetchConversations)

const useAgent = ref(false)

const {
  inputMessage, strictMode, privacyMode, handleSend: baseHandleSend, regenerateMessage
} = useChatSend(
  messages, attachedFiles, attachedFileIds,
  baseScrollToBottom, fetchConversations,
  isLoading, isRetrieving, sseStatus, useSSE, useAgent
)

const handleScroll = baseHandleScroll
const scrollToBottom = baseScrollToBottom

const handleFileUpload = async (event: Event) => {
  await baseHandleFileUpload(event)
}

const handleSend = baseHandleSend

// Feature 1: Regenerate last assistant response
const handleRegenerate = (assistantMsg: ChatMessage) => {
  const lastAssistantIdx = messages.value.lastIndexOf(assistantMsg)
  if (lastAssistantIdx < 1) return
  // Find the last user message before this assistant message
  let userMsgContent = ''
  for (let i = lastAssistantIdx - 1; i >= 0; i--) {
    if (messages.value[i].messageType === 'user') {
      userMsgContent = messages.value[i].content
      break
    }
  }
  if (!userMsgContent) return
  // Remove the assistant message
  messages.value.splice(lastAssistantIdx, 1)
  // Re-send the user message
  nextTick(() => regenerateMessage(userMsgContent))
}

// Feature 2: Drag-and-drop file upload
const isDragging = ref(false)
let dragCounter = 0

const onDragOver = (e: DragEvent) => {
  e.preventDefault()
  if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy'
}

const onDragEnter = (e: DragEvent) => {
  e.preventDefault()
  dragCounter++
  if (e.dataTransfer?.types?.includes('Files')) {
    isDragging.value = true
  }
}

const onDragLeave = (e: DragEvent) => {
  e.preventDefault()
  dragCounter--
  if (dragCounter <= 0) {
    dragCounter = 0
    isDragging.value = false
  }
}

const onDrop = async (e: DragEvent) => {
  isDragging.value = false
  dragCounter = 0
  if (!e.dataTransfer?.files?.length) return
  // Create a synthetic input event to reuse existing file upload logic
  const dt = e.dataTransfer
  const input = document.getElementById('global-chat-file-input') as HTMLInputElement | null
  if (input) {
    // Use DataTransfer to set files on the hidden input
    const dataTransfer = new DataTransfer()
    for (let i = 0; i < dt.files.length; i++) {
      dataTransfer.items.add(dt.files[i])
    }
    input.files = dataTransfer.files
    input.dispatchEvent(new Event('change', { bubbles: true }))
    // Reset input so same file can be selected again
    input.value = ''
  }
}

// Feature 4: Export chat as markdown
const handleExportChat = () => {
  if (messages.value.length === 0) return
  let md = `# ${chatStore.currentConversation?.title || t('chat.exportTitle')}\n\n`
  md += `> ${t('chat.exportTitle')} ${new Date().toLocaleString()}\n\n---\n\n`
  for (const msg of messages.value) {
    const role = msg.messageType === 'user' ? t('chat.roleUser') : t('chat.roleAssistant')
    md += `### ${role}\n\n${msg.content}\n\n`
    if (msg.sources && msg.sources.length > 0) {
      md += '**来源：**\n\n'
      msg.sources.forEach((s, i) => {
        md += `- [${i + 1}] ${s.filename || t('chat.sourceDoc')}${s.relevanceScore ? ` (${t('chat.relevanceMatch', { score: (s.relevanceScore * 100).toFixed(0) })})` : ''}\n`
      })
      md += '\n'
    }
    md += '---\n\n'
  }
  const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${chatStore.currentConversation?.title || t('chat.exportTitle')}-${Date.now()}.md`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

const suggestions = computed(() => [
  { title: t('chat.suggestions.quantum'), desc: t('chat.suggestions.quantumDesc') },
  { title: t('chat.suggestions.code'), desc: t('chat.suggestions.codeDesc') },
  { title: t('chat.suggestions.report'), desc: t('chat.suggestions.reportDesc') },
  { title: t('chat.suggestions.travel'), desc: t('chat.suggestions.travelDesc') }
])

const useSuggestion = (s: { title: string; desc: string }) => { inputMessage.value = s.title + '，' + s.desc }

const isBoundMode = computed(() => {
  return (chatStore.currentConversation?.settings?.bound_document_ids?.length ?? 0) > 0
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

const handleSelectConversation = async (conv: Conversation) => {
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
      const rawData = res.data as unknown as Record<string, unknown>
      const data = (rawData?.data as Record<string, unknown>) || rawData
      const msgs = (data.messages as Record<string, unknown>[]) || []
      chatStore.setCurrentConversation({
        id: data.id as string, title: data.title as string, userId: userStore.userInfo?.id || 0,
        createdAt: data.created_at as string, updatedAt: (data.updated_at as string) || (data.created_at as string),
        settings: data.settings as Record<string, unknown> | undefined
      })
      messages.value = msgs.map((m: Record<string, unknown>) => ({
        id: m.id as string, content: m.content as string, messageType: (m.message_type || m.messageType) as 'user' | 'assistant',
        conversationId: data.id as string, createdAt: m.created_at as string, sources: m.sources as ChatMessage['sources'], files: m.files as ChatMessage['files']
      }))
    }
  } catch {
    message.error(t('chat.historyFailed'))
    msgLoadError.value = true
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
const previewDoc = ref<{
  id?: string
  title?: string
  filename?: string
  file_name?: string
  file_type?: string
  file_size?: number
  source?: string
  created_at?: string
  description?: string
  summary?: string
  keywords?: string[]
  suggested_tags?: string[]
} | undefined>(undefined)
const previewContent = ref('')

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
  const handleResize = () => { windowWidth.value = window.innerWidth }
  window.addEventListener('resize', handleResize)
  await fetchConversations()
  const conversationId = route.query.conversation_id as string
  if (conversationId) await loadConversation(conversationId)

  const promptContent = route.query.prompt as string
  const promptName = route.query.promptName as string
  if (promptContent) {
    router.replace({ query: { ...route.query, prompt: undefined, promptName: undefined } })
    inputMessage.value = ''
    message.info(t('chat.promptApplied', { name: promptName || t('chat.promptDefaultName') }))
    localStorage.setItem('activeSystemPrompt', promptContent)
    localStorage.setItem('activeSystemPromptName', promptName || '')
  }

  // 确保连接 WebSocket（不依赖 watcher 的时序问题）
  if (!userStore.userInfo?.id) {
    try { await userStore.getUserInfo() } catch { /* 已登录但获取失败，仍尝试连接 */ }
  }
  if (userStore.token || getToken()) {
    connectWebSocket()
  }
})
</script>

<style scoped>
.scrollbar-thin::-webkit-scrollbar { width: 4px; }
.scrollbar-thin::-webkit-scrollbar-track { background: transparent; }
.scrollbar-thin::-webkit-scrollbar-thumb { background-color: rgba(156, 163, 175, 0.3); border-radius: 20px; }
.dark .scrollbar-thin::-webkit-scrollbar-thumb { background-color: rgba(156, 163, 175, 0.1); }
.scrollbar-thin:hover::-webkit-scrollbar-thumb { background-color: rgba(156, 163, 175, 0.5); }

/* Mobile: sidebar as fixed overlay */
@media (max-width: 767px) {
  aside[class*="flex-shrink-0"] {
    position: fixed !important;
    inset: 0 !important;
    z-index: 50 !important;
    height: 100vh !important;
    width: 100vw !important;
    max-width: 320px !important;
    border-radius: 0 16px 16px 0;
  }
  main {
    padding-bottom: env(safe-area-inset-bottom, 0px);
  }
  /* Chat input footer: fixed at bottom on mobile */
  footer[class*="absolute bottom-0"] {
    position: fixed !important;
    padding-bottom: calc(env(safe-area-inset-bottom, 0px) + 12px) !important;
  }
  /* Messages area: adjust bottom padding for fixed input */
  div[class*="pt-20 pb-40"] {
    padding-bottom: 160px !important;
  }
}
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
