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
        :debugEnabled="debugEnabled"
        @toggleSidebar="toggleSidebar"
        @clearChat="clearChat"
        @unbind="handleUnbind"
        @exportChat="handleExportChat"
        @toggleDebug="toggleDebug"
      />

      <!-- Mobile back-to-conversations button -->
      <div
        v-if="sidebarOpen && windowWidth < 768"
        class="fixed left-72 top-20 z-20 md:hidden"
      >
        <n-button size="tiny" quaternary @click="toggleSidebar" class="bg-white/90 dark:bg-gray-800/90 shadow-sm rounded-lg">
          <template #icon><n-icon><ArrowBackOutline /></n-icon></template>
          {{ t('chat.backToConversations') }}
        </n-button>
      </div>

      <ChatMessages
        ref="chatMessagesRef"
        :messages="messages"
        :isLoading="isLoading"
        :isLoadingHistory="isLoadingHistory"
        :showBackToBottom="showBackToBottom"
        :userAvatar="userStore.userInfo?.avatar"
        :suggestions="suggestions"
        @scroll="handleScroll"
        @scrollToBottom="scrollToBottom"
        @useSuggestion="useSuggestion"
        @feedback="handleFeedback"
        @copy="copyText"
        @regenerate="handleRegenerate"
        @openSource="handleOpenSource"
      />

      <!-- Retrieval Debug Panel -->
      <DebugPanel
        v-if="debugData"
        :debug-data="debugData"
        @close="debugData = null"
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
        :isLoading="isLoading"
        :attachedFiles="attachedFiles"
        :attachedFileIds="attachedFileIds"
        :connectionStatus="effectiveConnectionStatus"
        :connectionStatusText="getConnectionStatusText(effectiveConnectionStatus)"
        :orgPrivacyAllowed="orgPrivacyAllowed"
        @send="handleSend"
        @stopGeneration="stopGeneration"
        @triggerFileUpload="triggerFileUpload"
        @removeAttachment="removeAttachment"
        @openAgentWorkbench="openAgentWorkbench"
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
import { useConfirmDangerousAction } from '@/utils/confirm'
import { CloudUploadOutline, AlertCircleOutline, ArrowBackOutline } from '@vicons/ionicons5'
import { getConversationMessages, clearConversationMessages } from '@/api/chat'
import { getOrganizationSettings } from '@/api/organization'
import type { Conversation } from '@/api/conversation'
import { getDocumentDetail, getDocumentContent } from '@/api/knowledge'
import type { DocumentContent, DocumentDetail } from '@/api/knowledge'
import type { ChatMessage, KnowledgeSource } from '@/types/chat'
import { getToken } from '@/utils/auth'

import { ChatSidebar, ChatMessages, ChatInput, ChatHeader, DocumentPreviewModal, DebugPanel } from './components'
import { citationRank } from './utils/citations'
import { useChatAttachments, useChatMessages, useChatSessions, useChatConnection, useChatSend } from './composables'

const message = useDedupedMessage()
const { t } = useI18n()
const confirmDangerousAction = useConfirmDangerousAction()
const route = useRoute()
const router = useRouter()
const chatStore = useChatStore()
const userStore = useUserStore()
const appStore = useAppStore()

const chatMessagesRef = ref()
const msgLoadError = ref(false)
const windowWidth = ref(window.innerWidth)

// W2-T7：提示词「应用」内容上限（与 prompts/index.vue 表单 maxlength 对齐；
// 改一处必须同步另一处，chatPromptApply.test.ts 钉住边界）。
// E14/P2：B3 已删除 activeSystemPrompt 发送通道——本页不再有「当前模板」
// 状态与头部徽标（旧常驻标识宣称「已生效」与实际不符，已随死通道一并移除）。
const MAX_PROMPT_LENGTH = 5000

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
  effectiveConnectionStatus, connectWebSocket
} = useChatConnection(messages, baseScrollToBottom, fetchConversations)

// E7：历史加载态与生成态分离 —— 切会话拉历史不再复用 isLoading（修前会误现
// 「停止生成」按钮、生成 loading 与流式光标）。停止生成统一走 useChatSend 版本
// （中止 SSE + 冲刷 E3 节流帧 + 落「已停止」标记）；useChatConnection 的旧停止
// 只发 WS stop，SSE 模式下无法中止传输。
const isLoadingHistory = ref(false)

const debugEnabled = ref(false)
const debugData = ref<import('@/types/chat').RetrievalDebugData | null>(null)

// B4（D5）：组织隐私策略。默认允许（保持既有 privacyMode 默认 True 的行为）；
// 组织显式禁用时置灰开关并给出「组织策略」说明。后端在检索/生成链路仍会强制
// 关闭（不信任客户端），前端灰化只负责如实呈现策略来源。
const orgPrivacyAllowed = ref(true)

const loadOrgPrivacyPolicy = async () => {
  const orgId = userStore.currentOrgId
  if (!orgId) {
    orgPrivacyAllowed.value = true
    return
  }
  try {
    const res = await getOrganizationSettings(orgId)
    const entry = res.data?.settings?.allow_privacy_mode
    orgPrivacyAllowed.value = entry ? entry.value !== false : true
  } catch {
    // 读取失败不阻塞聊天：保持既有默认；服务端仍按组织策略强制。
    orgPrivacyAllowed.value = true
  }
}

const toggleDebug = () => {
  debugEnabled.value = !debugEnabled.value
  if (!debugEnabled.value) debugData.value = null
}

const {
  inputMessage, strictMode, privacyMode, handleSend: baseHandleSend, regenerateMessage,
  stopGeneration
} = useChatSend(
  messages, attachedFiles, attachedFileIds,
  baseScrollToBottom, fetchConversations,
  isLoading, isRetrieving, sseStatus, useSSE, useStream, debugEnabled, debugData,
)

// issue #508（方案 B）：深度分析（Agent/PER）只在 /agent 工作台提供（审批/计划
// 审批/取消/回放 UI 完整）。Chat 的深度分析入口改为导航——不再以
// useAgent=true 发到 /chat/stream（后端对旧客户端显式返回
// DEEP_ANALYSIS_MOVED_TO_AGENT）。当前输入经 ?prompt= 带过去，URL 至少不丢
// 用户已输入的意图；Agent 页消费该参数另开跟进（本 issue 白名单不含 agent 页）。
const openAgentWorkbench = () => {
  const prompt = inputMessage.value.trim()
  message.info(t('chat.deepAnalysisMoved'))
  router.push({ path: '/agent', query: prompt ? { prompt } : undefined })
}

// B4（D5）：组织策略禁用隐私模式时，强制关闭并持续跟随组织切换。
watch(orgPrivacyAllowed, (allowed) => {
  if (!allowed) privacyMode.value = false
})
watch(() => userStore.currentOrgId, () => { void loadOrgPrivacyPolicy() })

const handleScroll = baseHandleScroll
const scrollToBottom = baseScrollToBottom

const handleFileUpload = async (event: Event) => {
  await baseHandleFileUpload(event)
}

// E7：历史加载期间不接受发送——旧实现靠 isLoading 挡住，状态分离后需显式守卫，
// 否则历史回填会覆盖刚发出的消息。
const handleSend = async () => {
  if (isLoadingHistory.value) return
  await baseHandleSend()
}

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
        const rank = citationRank(s, i)
        // issue #185：导出与来源卡片同一诚实口径——有重排绝对分写「重排分」，
        // 否则只写排序序位；不再输出构造性的「相关度 %」。
        const scoreText = typeof s.relevanceScore === 'number' && s.rank != null
          ? t('chat.rerankScore', { score: s.relevanceScore.toFixed(2) })
          : t('chat.rankPosition', { rank })
        md += `- [${rank}] ${s.filename || t('chat.sourceDoc')} (${scoreText})\n`
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
  // E11-③：加载统一由下方 watch(conversation_id) 驱动（布局 key 收敛为
  // route.name 后同页 query 变化不再整页重挂；此处不再直接调 loadConversation，
  // 避免一次切换双重拉取）
  await router.push({ query: { conversation_id: conv.id } })
  if (window.innerWidth < 768) sidebarOpen.value = false
}

const loadConversation = async (id: string) => {
  // E7：历史加载用独立状态（isLoadingHistory），不再复用生成态 isLoading
  isLoadingHistory.value = true
  msgLoadError.value = false
  try {
    const res = await getConversationMessages(id)
    if (res.data) {
      const rawData = res.data as unknown as Record<string, unknown>
      // 无权/会话不存在：HTTP 200 但 { success:false, message:"无权访问" }（chat.py:340）
      // ——旧实现静默落入空聊天态不报错（C7）；现在如实提示并中止
      if (rawData?.success === false) {
        message.error((rawData.message as string) || t('chat.historyFailed'))
        return
      }
      const data = (rawData?.data as Record<string, unknown>) || rawData
      const msgs = (data.messages as Record<string, unknown>[]) || []
      chatStore.setCurrentConversation({
        id: data.id as string, title: data.title as string, userId: userStore.userInfo?.id || 0,
        createdAt: data.created_at as string, updatedAt: (data.updated_at as string) || (data.created_at as string),
        settings: data.settings as Record<string, unknown> | undefined
      })
      messages.value = msgs.map((m: Record<string, unknown>) => ({
        id: m.id as string, content: m.content as string,
        messageType: (m.messageType ?? m.message_type) as 'user' | 'assistant',
        conversationId: data.id as string,
        // 真键 camelCase（chat.py:353-359）：createdAt/feedback/feedbackNote——
        // 旧映射读 m.created_at（时间丢，B36）且丢失 feedback（重载后点赞高亮必丢，B34）
        createdAt: m.createdAt as string,
        feedback: (m.feedback ?? undefined) as number | undefined,
        feedbackNote: (m.feedbackNote ?? undefined) as string | undefined,
        sources: m.sources as ChatMessage['sources'], files: m.files as ChatMessage['files']
      }))
    }
  } catch {
    message.error(t('chat.historyFailed'))
    msgLoadError.value = true
  } finally {
    isLoadingHistory.value = false
    scrollToBottom('auto', true)
  }
}

// E11-③：布局页面包装 key 由 fullPath 收敛为 route.name 后，同页仅 query
// 变化不再整页重挂载——conversation_id 变化（侧栏切会话、浏览器前进/后退）
// 由本 watch 接管加载；清空 conversation_id（新对话 / 当前会话被删）回到
// 空会话态并清空消息，避免旧会话内容挂在新 URL 下。
watch(() => route.query.conversation_id, (newId) => {
  const id = typeof newId === 'string' ? newId : ''
  if (!id) {
    messages.value = []
    chatStore.setCurrentConversation(null)
    return
  }
  if (id === chatStore.currentConversation?.id) return
  void loadConversation(id)
})

const clearChat = async () => {
  if (!currentConversationId.value) return
  const confirmed = await confirmDangerousAction(t('chat.clearChat'), t('chat.clearConfirm'))
  if (!confirmed) return
  try {
    const res = await clearConversationMessages(String(currentConversationId.value))
    if (res.data?.success) {
      messages.value = []
      message.success(t('chat.chatCleared'))
    } else {
      // E2：success:false（如无权限）此前静默无任何反馈——如实提示服务端 message
      const serverMsg = (res.data as { message?: string } | undefined)?.message
      message.error(serverMsg || t('chat.clearFailed'))
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

// W2-T7 项 5：来源卡 → 原文预览接通（第三轮终检 §2.4：来源 tag 可点样式但零处理，
// DocumentPreviewModal 的四个 ref 全树无写入点 → 弹窗永不打开）。现在点击来源卡
// 拉取文档详情（/documents/{id}）+ 全文（/documents/{id}/content）后打开弹窗；
// 失败明确报错（loading 收敛，不 silent）。
// 取数键 = documentId（chat_service.build_sources_list 真形；旧按 fileId 取值
// 在实机恒 undefined——类型同轮修真，见 types/chat.ts）。
const handleOpenSource = async (source: KnowledgeSource) => {
  const docId = source?.documentId
  if (docId === undefined || docId === null || docId === '') return
  const id = String(docId)
  showPreviewModal.value = true
  previewLoading.value = true
  previewDoc.value = { id, filename: source.filename }
  previewContent.value = ''
  try {
    const [docRes, contentRes] = await Promise.all([
      getDocumentDetail(id),
      getDocumentContent(id),
    ])
    const doc = (docRes?.data as { data?: DocumentDetail } | undefined)?.data
    const contentData = (contentRes?.data as { data?: DocumentContent } | undefined)?.data
    previewDoc.value = {
      id,
      title: doc?.title,
      filename: doc?.filename || source.filename,
      file_type: doc?.file_type,
      file_size: doc?.file_size,
      source: doc?.upload_source,
      created_at: doc?.created_at,
      description: doc?.description,
      keywords: doc?.keywords,
      summary: contentData?.summary,
      suggested_tags: contentData?.suggested_tags,
    }
    previewContent.value = contentData?.content || ''
  } catch {
    message.error(t('chat.sourcePreviewFailed'))
  } finally {
    previewLoading.value = false
  }
}

onMounted(async () => {
  const token = userStore.token || getToken()
  if (!token) { router.push({ name: 'Login' }); return }
  checkScreenSize()
  window.addEventListener('resize', checkScreenSize)
  const handleResize = () => { windowWidth.value = window.innerWidth }
  window.addEventListener('resize', handleResize)
  void loadOrgPrivacyPolicy()
  await fetchConversations()
  const conversationId = route.query.conversation_id as string
  if (conversationId) await loadConversation(conversationId)

  // URL 消费型参数一次性捕获 + 统一清理：prompt（提示词填入输入框）与
  // q/context（搜索「问问 AI」跨页上下文）用后即清；promptName 为旧链接
  // 兼容清理（E14/P2 起不再有消费方）。分开 replace 会因导航异步互相覆盖
  // （后一次 spread 仍读到旧 query），故合并为一次。
  const promptContent = route.query.prompt as string
  const searchQuestion = route.query.q as string
  const searchContext = route.query.context as string
  if (promptContent || searchQuestion) {
    router.replace({
      query: {
        ...route.query,
        prompt: undefined,
        promptName: undefined,
        q: undefined,
        context: undefined,
      },
    })
  }

  if (promptContent) {
    // W2-T7：上限与提示词表单 maxlength（prompts/index.vue）对齐 5000——
    // 修前这里只接受 <=2000 且无 else 分支：2001~5000 字的提示词点「应用」后
    // 静默丢弃（第三轮终检 §2.4）。超限改为明确报错，不再无声失败。
    // E14/P2：B3 后模板不再随消息发送——不写 activeSystemPrompt 死键、
    // 不声称「已应用」；改为把提示词填入输入框，由用户确认后自行发送。
    if (typeof promptContent === 'string' && promptContent.length <= MAX_PROMPT_LENGTH) {
      inputMessage.value = promptContent
      message.info(t('chat.promptFilled'))
    } else {
      message.error(t('chat.promptTooLong', { max: MAX_PROMPT_LENGTH }))
    }
  }

  // E11-①：搜索页「问问 AI」跨页带参——q 预填输入框，context（所点结果正文
  // 片段）以 Markdown 引用块随行，保留跨页用户意图；参数用后即从 URL 清除，
  // 刷新不重复预填。选择预填而非自动发送：用户可编辑/确认，避免未经确认
  // 触发模型调用与计费。
  if (typeof searchQuestion === 'string' && searchQuestion.trim()) {
    inputMessage.value = typeof searchContext === 'string' && searchContext.trim()
      ? `${searchQuestion}\n\n> ${searchContext}`
      : searchQuestion
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
