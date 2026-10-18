import { ref, onUnmounted } from 'vue'
import { useDedupedMessage } from '@/utils/message'
import { useI18n } from 'vue-i18n'
import { useChatStore } from '@/stores/chat'
import { useUserStore } from '@/stores/user'
import { wsService } from '@/utils/websocket'
import { sseService } from '@/utils/sseService'
import { applyPlanStepUpdate, buildPlanStep } from '@/utils/agentPlan'
import type { ChatMessage, AttachedFile, KnowledgeSource, RetrievalDebugData } from '@/types/chat'
import type { AgentEvent } from '@/types/agent'
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
  useAgent: { value: boolean },
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
    sseService.off('thinking')
    sseService.off('tool_call')
    sseService.off('tool_result')
    sseService.off('tool_error')
    sseService.off('debug')
    sseService.off('run_report')
    sseService.off('execution_context')
    sseService.off('retry_hint')
    sseService.off('plan_start')
    sseService.off('plan_step')
    sseService.off('plan_complete')
    sseService.off('reflection')

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

    const onThinking = (data: SSEMessage) => {
      // 仅保留 reasoning/planning/correction：evaluation 子类型（质检/步骤
      // 完成叙述）在聊天页为刻意降噪（issue #289 复核记录 #4）。计划步骤
      // 状态不依赖这些叙述——由 plan_step/tool_* 事件驱动（见下方 #289 块）。
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
        if (useStream.value) scheduleStreamContent()
        // 计划步骤状态推进（issue #289：与 Agent 页 store 同款归约）
        if (lastMsg.planSteps) {
          applyPlanStepUpdate(lastMsg.planSteps, { type: 'tool_call', plan_step_id: data.planStepId })
        }
      }
      scrollToBottom()
    }

    const onToolResult = (data: SSEMessage) => {
      const lastMsg = messages.value[messages.value.length - 1]
      if (lastMsg && lastMsg.messageType === 'assistant' && lastMsg.id === aiMsgId) {
        const stamp = `\n> ✅ 工具完成` + (data.toolDurationMs ? ` (${Math.round(data.toolDurationMs)}ms)` : '') + `\n`
        fullContent += stamp
        if (useStream.value) scheduleStreamContent()
        if (lastMsg.planSteps) {
          applyPlanStepUpdate(lastMsg.planSteps, { type: 'tool_result', plan_step_id: data.planStepId })
        }
        if (typeof data.planProgress === 'number') {
          lastMsg.planProgress = data.planProgress
        }
      }
    }

    const onToolError = (data: SSEMessage) => {
      const lastMsg = messages.value[messages.value.length - 1]
      if (lastMsg && lastMsg.messageType === 'assistant' && lastMsg.id === aiMsgId) {
        const stamp = `\n> ⚠️ 工具调用异常: ${data.content || '未知错误'}\n`
        fullContent += stamp
        if (useStream.value) scheduleStreamContent()
      }
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

    // ── issue #289：Agent 模式（useAgent）PER 事件的消费者 ─────────────
    // run_report / execution_context / retry_hint / plan_start / plan_step /
    // plan_complete / reflection 7 型此前经 sseService 白名单放行（#253）但
    // useChatSend 从未注册 handler —— 链路止于传输层，同一 run 在聊天页与
    // Agent 页看到的 UI 不同构。以下按 Agent 页 store（stores/agent.ts
    // processEvent）同款口径落账；渲染见 ChatMessages.vue。

    const lastAssistantMsg = (): ChatMessage | undefined => {
      const lastMsg = messages.value[messages.value.length - 1]
      if (lastMsg && lastMsg.messageType === 'assistant' && lastMsg.id === aiMsgId) return lastMsg
      return undefined
    }

    const pushAgentEvent = (msg: ChatMessage, event: AgentEvent) => {
      if (!msg.events) msg.events = []
      msg.events.push(event)
    }

    const onRunReport = (data: SSEMessage) => {
      const msg = lastAssistantMsg()
      if (msg) msg.runReport = data.content
    }

    const onExecutionContext = (data: SSEMessage) => {
      const msg = lastAssistantMsg()
      // chat.py 投影里 execution_context 的 content 是对象（含 total_tokens）
      if (msg) msg.executionContext = data.content as unknown as Record<string, unknown>
    }

    const onRetryHint = (data: SSEMessage) => {
      const msg = lastAssistantMsg()
      if (msg) pushAgentEvent(msg, { type: 'retry_hint', content: data.content || '' })
    }

    const onPlanStart = (data: SSEMessage) => {
      const msg = lastAssistantMsg()
      if (!msg) return
      msg.planSteps = []
      msg.planProgress = undefined
      pushAgentEvent(msg, { type: 'plan_start', content: data.content || '', plan_id: data.planId })
    }

    const onPlanStep = (data: SSEMessage) => {
      const msg = lastAssistantMsg()
      if (!msg) return
      const step = buildPlanStep({
        plan_step_id: data.planStepId,
        content: data.content,
        dependencies: data.dependencies,
        tool_hint: data.toolHint,
      })
      if (step) msg.planSteps = [...(msg.planSteps || []), step]
      pushAgentEvent(msg, {
        type: 'plan_step',
        content: data.content || '',
        plan_id: data.planId,
        plan_step_id: data.planStepId,
      })
    }

    const onPlanComplete = (data: SSEMessage) => {
      const msg = lastAssistantMsg()
      if (msg) pushAgentEvent(msg, { type: 'plan_complete', content: data.content || '', plan_id: data.planId })
    }

    const onReflection = (data: SSEMessage) => {
      const msg = lastAssistantMsg()
      if (msg) {
        pushAgentEvent(msg, {
          type: 'reflection',
          content: data.content || '',
          reflection_result: data.result as AgentEvent['reflection_result'],
        })
      }
    }

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
    sseService.on('thinking', onThinking)
    sseService.on('tool_call', onToolCall)
    sseService.on('tool_result', onToolResult)
    sseService.on('tool_error', onToolError)
    sseService.on('debug', onDebug)
    sseService.on('run_report', onRunReport)
    sseService.on('execution_context', onExecutionContext)
    sseService.on('retry_hint', onRetryHint)
    sseService.on('plan_start', onPlanStart)
    sseService.on('plan_step', onPlanStep)
    sseService.on('plan_complete', onPlanComplete)
    sseService.on('reflection', onReflection)
    sseService.on('retry', onRetry)

    const systemPrompt = localStorage.getItem('activeSystemPrompt')

    try {
      const sent = await sseService.post('stream', {
        content: userMessage,
        conversationId: chatStore.currentConversation?.id,
        fileIds: currentFiles.filter(f => f.status === 'done' && f.id).map(f => f.id!),
        useAgent: useAgent.value,
        debug: debugEnabled?.value ?? false,
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
    sseService.off('thinking')
    sseService.off('tool_call')
    sseService.off('tool_result')
    sseService.off('tool_error')
    sseService.off('debug')
    sseService.off('run_report')
    sseService.off('execution_context')
    sseService.off('retry_hint')
    sseService.off('plan_start')
    sseService.off('plan_step')
    sseService.off('plan_complete')
    sseService.off('reflection')
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
