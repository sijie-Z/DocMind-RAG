import { ref, onUnmounted } from 'vue'
import { useDedupedMessage } from '@/utils/message'
import { useI18n } from 'vue-i18n'
import { useChatStore } from '@/stores/chat'
import { useUserStore } from '@/stores/user'
import { wsService } from '@/utils/websocket'
import { sseService } from '@/utils/sseService'
import type { ChatMessage, AttachedFile, KnowledgeSource, RetrievalDebugData } from '@/types/chat'
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
  useStream: { value: boolean },
  debugEnabled?: { value: boolean },
  debugData?: { value: RetrievalDebugData | null },
) {
  const message = useDedupedMessage()
  const { t } = useI18n()
  const chatStore = useChatStore()
  const userStore = useUserStore()

  const inputMessage = ref('')
  const strictMode = ref(false)
  const privacyMode = ref(true)

  // E3：当前活动流的落屏节流器（同一时刻至多一个流）。stopGeneration 时冲刷
  // 挂起的一帧，组件卸载时取消，避免尾部内容丢失或在卸载后写入状态。
  let activeStream: { flush: () => void; cancel: () => void } | null = null

  const buildFileContext = (files: AttachedFile[]): string | null => {
    const withContent = files.filter(f => f.parsedContent)
    if (!withContent.length) return null
    const parts = withContent.map(f =>
      `以下是从文件「${f.name}」中提取的内容：\n${f.parsedContent}`
    )
    return parts.join('\n\n---\n\n')
  }

  const handleSSESend = async (preprocessedMessage?: string) => {
    let userMessage = preprocessedMessage ?? inputMessage.value.trim()
    const currentFiles = [...attachedFiles.value]
    const hasParsedContent = currentFiles.some(f => f.parsedContent)

    // 如果有即时解析的文件但用户没打字，自动生成提示
    if (!userMessage && (attachedFileIds.value.length > 0 || hasParsedContent)) {
      userMessage = t('chat.analyzeFiles', { n: currentFiles.length })
    }
    if (!userMessage) return

    // 只有没经过 handleSend 预处理的消息才在这里注入文件内容
    if (!preprocessedMessage) {
      const fileContext = buildFileContext(currentFiles)
      if (fileContext) {
        userMessage = `${fileContext}\n\n---\n\n基于以上文件内容，${userMessage}`
      }
    }

    inputMessage.value = ''
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
    sseService.off('warning')
    sseService.off('retry')
    sseService.off('debug')

    // E3 流式渲染性能：chunk 内容只累积进 fullContent，落屏按帧（rAF；无 rAF
    // 环境回退 33ms）节流合并——每个 chunk 不再触发一次全量 Markdown+DOMPurify
    // 渲染（原实现累计 O(n²)）。message 终态直达写入最终内容，保证最终一致性。
    let cancelScheduledFlush: (() => void) | null = null

    const writeStreamContent = () => {
      cancelScheduledFlush = null
      const lastMsg = messages.value[messages.value.length - 1]
      if (lastMsg && lastMsg.messageType === 'assistant' && lastMsg.id === aiMsgId) {
        lastMsg.content = fullContent
      }
    }

    const scheduleStreamContent = () => {
      if (cancelScheduledFlush) return
      if (typeof requestAnimationFrame === 'function') {
        const frameId = requestAnimationFrame(writeStreamContent)
        cancelScheduledFlush = () => {
          cancelAnimationFrame(frameId)
          cancelScheduledFlush = null
        }
      } else {
        const timerId = setTimeout(writeStreamContent, 33)
        cancelScheduledFlush = () => {
          clearTimeout(timerId)
          cancelScheduledFlush = null
        }
      }
    }

    const flushStreamContent = () => {
      cancelScheduledFlush?.()
      writeStreamContent()
    }

    activeStream = {
      flush: flushStreamContent,
      cancel: () => cancelScheduledFlush?.(),
    }

    // W2-T7 项 4：「流式响应」开关真接线——关闭时只累积 fullContent 不逐字落屏，
    // 等 message 终态一次性落屏（非流式展示）；开启时按帧节流落屏（默认行为不回归）。
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
        // E3：侧栏列表刷新移出 chunk（流式）路径——本地已 setCurrentConversation，
        // 列表改在流完成（onMessage）或切页时刷新，避免流式期间拉列表干扰渲染。
      }
      isRetrieving.value = false
      const lastMsg = messages.value[messages.value.length - 1]
      if (lastMsg && lastMsg.messageType === 'assistant' && lastMsg.id === aiMsgId) {
        fullContent += data.content || ''
        if (useStream.value) scheduleStreamContent()
        if (data.sources) lastMsg.sources = data.sources as KnowledgeSource[]
      }
      scrollToBottom()
    }

    const onMessage = (data: SSEMessage) => {
      isLoading.value = false
      isRetrieving.value = false
      sseStatus.value = 'connected'
      // B2（D3）：没有 chunk 的冷场回答（空正文/仅终态）也要接管后端生成的
      // conversationId——否则下一轮会新建会话，上一轮落库的问答成为孤儿。
      if (data.conversationId && (!chatStore.currentConversation || chatStore.currentConversation.id !== data.conversationId)) {
        chatStore.setCurrentConversation({
          id: data.conversationId,
          title: userMessage.slice(0, 20),
          userId: userStore.userInfo?.id || 0,
          createdAt: new Date().toISOString(),
          updatedAt: new Date().toISOString()
        })
      }
      // E3：终态直达——取消挂起的节流落屏，一次性写入最终内容（不丢尾帧、不重复渲染）
      activeStream?.cancel()
      activeStream = null
      const lastMsg = messages.value[messages.value.length - 1]
      if (lastMsg && lastMsg.id === aiMsgId) {
        lastMsg.content = data.content || fullContent
        if (data.sources) lastMsg.sources = data.sources as KnowledgeSource[]
        if (data.is_cached) lastMsg.isCached = true
      }
      // 侧栏列表在流完成时刷新（chunk 路径不再触发，见 onChunk）
      fetchConversations()
      scrollToBottom()
    }

    const onError = (data: SSEMessage) => {
      isLoading.value = false
      isRetrieving.value = false
      sseStatus.value = 'error'
      // E3：出错终止时冲刷挂起的一帧，已到达的部分内容不丢（与旧逐 chunk 落屏观感一致）
      activeStream?.flush()
      activeStream = null
      message.error(data.content || t('chat.sseError'))
    }

    // ISS-3：后端 warning 事件（落库失败可见 / Agent 侧降级提示）此前被传输层
    // 白名单静默丢弃，聊天页无任何提示；补订阅，content 原样提示并带 i18n 兜底。
    const onWarning = (data: SSEMessage) => {
      message.warning(data.content || t('chat.sseWarning'))
    }

    const onDebug = (data: SSEMessage) => {
      if (debugData && debugEnabled?.value) {
        debugData.value = (data as unknown as Record<string, unknown>).data as RetrievalDebugData
      }
    }

    // issue #508（方案 B）：Chat 不再承载深度分析（Agent/PER）。此前的 agent
    // 事件消费者（thinking / tool_* / plan_* / reflection / run_report /
    // execution_context / retry_hint）与「半套状态」一并移除——审批/计划审批/
    // 取消/命令等事件在 Chat 既无卡片也无恢复入口，只会造成同一 run 在两个
    // 入口 UI 不同构。全部 Agent 事件消费收敛到 /agent 工作台；普通 RAG 聊天
    // 只消费 chunk/message/error/warning/debug（+ 客户端 retry）。
    // 覆盖度静态契约见 __tests__/chatEventCoverage.test.ts。

    // issue #289 G4：'retry' 是 sseService 的纯客户端事件（429/503 退避时
    // emit，sseService.ts），此前 Chat 页只 off 未 on —— 自动退避等待期用户
    // 毫无提示；现在退避时给出进度提示。
    const onRetry = (data: SSEMessage) => {
      message.info(t('chat.retrying', {
        attempt: data.attempt ?? 1,
        maxRetries: data.maxRetries ?? 3,
      }))
    }

    sseService.on('chunk', onChunk)
    sseService.on('message', onMessage)
    sseService.on('error', onError)
    sseService.on('warning', onWarning)
    sseService.on('debug', onDebug)
    sseService.on('retry', onRetry)

    try {
      const sent = await sseService.post('stream', {
        content: userMessage,
        conversationId: chatStore.currentConversation?.id,
        fileIds: currentFiles.filter(f => f.status === 'done' && f.id).map(f => f.id!),
        // issue #508：Chat 不再发送 useAgent（深度分析已迁至 /agent 工作台）。
        // 旧客户端若仍发 useAgent=true，后端显式返回 DEEP_ANALYSIS_MOVED_TO_AGENT。
        debug: debugEnabled?.value ?? false,
        payload: {
          strictMode: strictMode.value,
          privacyMode: privacyMode.value
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

    const currentFiles = [...attachedFiles.value]
    const hasParsedContent = currentFiles.some(f => f.parsedContent)
    const hasFileIds = currentFiles.some(f => f.status === 'done' && f.id)

    let userMessage = inputMessage.value.trim()
    if (!userMessage && (hasFileIds || hasParsedContent)) {
      userMessage = t('chat.analyzeFiles', { n: currentFiles.length })
    } else if (!userMessage && currentFiles.length > 0 && !hasFileIds && !hasParsedContent) {
      message.warning(t('chat.attachmentsNotReady'))
      return
    }
    if (!userMessage) return
    if (isLoading.value) return

    // 注入即时解析的文件内容
    const fileContext = buildFileContext(currentFiles)
    if (fileContext) {
      userMessage = `${fileContext}\n\n---\n\n基于以上文件内容，${userMessage}`
    }

    // issue #508：Chat 只保留普通 RAG 通道；深度分析（Agent/PER）已迁至
    // /agent 工作台（审批/回放 UI 完整）。不再存在「即使 WS 也强制 SSE 的
    // Agent 分支」——WS 传输开关恢复为纯用户选择。
    if (useSSE.value) {
      await handleSSESend(userMessage)
      return
    }

    if (!wsService.isConnected()) {
      message.warning(t('chat.connecting'))
      return
    }

    inputMessage.value = ''
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

  const stopGeneration = () => {
    // E3：手动停止时冲刷挂起的一帧（节流窗口内累积的内容不丢），再中止传输
    activeStream?.flush()
    activeStream = null
    sseService.abort()
    wsService.sendStop()
    // E7：仅在实际生成中停止时给最后一条 assistant 消息打「已停止」标记
    // （停止按钮只在加载态可见；guard 防止空闲/历史加载路径误标历史消息）
    if (isLoading.value || isRetrieving.value) {
      const lastMsg = messages.value[messages.value.length - 1]
      if (lastMsg && lastMsg.messageType === 'assistant') {
        lastMsg.stopped = true
      }
    }
    isLoading.value = false
    isRetrieving.value = false
  }

  // 组件卸载时中止进行中的 SSE 流并清理事件处理器，防止请求悬挂与内存泄漏
  onUnmounted(() => {
    // E3：取消挂起的节流落屏，避免卸载后写状态
    activeStream?.cancel()
    activeStream = null
    sseService.abort()
    sseService.off('chunk')
    sseService.off('message')
    sseService.off('error')
    sseService.off('warning')
    sseService.off('retry')
    sseService.off('debug')
  })

  return {
    inputMessage,
    strictMode,
    privacyMode,
    handleSend,
    handleSSESend,
    handleKeydown,
    regenerateMessage,
    stopGeneration,
  }
}
