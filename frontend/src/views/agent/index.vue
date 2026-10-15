<template>
  <div class="agent-page">
    <!-- Header -->
    <div class="agent-header">
      <div class="header-left">
        <h1 class="header-title">🤖 DocMind Agent</h1>
        <span class="header-badge">PER 架构</span>
      </div>
      <div class="header-center">
        <SessionSelector
          :sessions="agentStore.sessions"
          :modelValue="agentStore.currentSessionId"
          @update:modelValue="handleSessionChange"
          @delete="handleSessionDelete"
        />
      </div>
      <div class="header-right">
        <button class="header-btn" @click="showConfig = !showConfig" :class="{ active: showConfig }">
          ⚙ 配置
        </button>
        <button class="header-btn" @click="handleNewSession">+ 新建</button>
        <button class="header-btn" @click="agentStore.clearMessages()">清空</button>
      </div>
    </div>

    <!-- Body: Three-column layout -->
    <div class="agent-body">
      <!-- Loading state -->
      <div v-if="initLoading" class="loading-overlay">
        <n-spin size="large" />
        <p class="loading-text">加载 Agent 数据...</p>
      </div>
      <!-- Error state -->
      <div v-else-if="initError" class="error-overlay">
        <p class="error-icon">⚠️</p>
        <p class="error-text">加载失败，请检查后端服务是否运行</p>
        <n-button type="primary" size="small" @click="reloadInit">重试</n-button>
      </div>
      <template v-else>
      <!-- Left: Plan Tree -->
      <div class="left-panel" :class="{ collapsed: !showPlan }">
        <div class="panel-toggle" @click="showPlan = !showPlan">
          {{ showPlan ? '◀' : '▶' }}
        </div>
        <PlanTree
          v-if="showPlan && currentPlanSteps.length > 0"
          :steps="currentPlanSteps"
          :progress="agentStore.overallProgress"
        />
        <div v-else-if="showPlan" class="empty-panel">
          <p class="empty-text">发送复杂任务后，执行计划将显示在这里</p>
        </div>
      </div>

      <!-- Center: Chat / Execution -->
      <div class="center-panel" ref="centerPanel">
        <!-- Empty state -->
        <div v-if="agentStore.messages.length === 0" class="empty-state">
          <h2>DocMind 智能助手</h2>
          <p class="empty-subtitle">Planning → Execution → Reflection</p>
          <div class="suggestion-grid">
            <button
              v-for="s in suggestions"
              :key="s"
              class="suggestion-btn"
              @click="handleSend(s)"
            >
              {{ s }}
            </button>
          </div>
          <div class="feature-list">
            <div class="feature-item">📋 自动规划：复杂任务自动拆分</div>
            <div class="feature-item">🔧 30 个工具：搜索、分析、代码、翻译</div>
            <div class="feature-item">🧠 记忆系统：记住你的偏好和历史</div>
            <div class="feature-item">🔍 自我反思：执行后自动评估纠正</div>
          </div>
        </div>

        <!-- Messages -->
        <div
          v-for="(msg, idx) in agentStore.messages"
          :key="idx"
          class="message-group"
        >
          <!-- User message -->
          <div v-if="msg.role === 'user'" class="user-msg">
            <div class="user-bubble">{{ msg.content }}</div>
          </div>

          <!-- Agent message -->
          <div v-else class="agent-msg">
            <!-- Thinking Stream -->
            <ThinkingStream
              :text="msg.thinkingText"
              :thinkingType="getLastThinkingType(msg)"
              :active="msg.loading && msg.content.length === 0"
              :stopped="agentStore.taskState === 'cancelled'"
            />

            <!-- Tool calls -->
            <div v-if="msg.events.length > 0" class="tool-events">
              <template v-for="(evt, eIdx) in msg.events" :key="eIdx">
                <!-- Tool call + result paired -->
                <template v-if="evt.type === 'tool_call'">
                  <ToolCallCard
                    :tool-name="evt.tool_name || 'unknown'"
                    :tool-args="evt.tool_args"
                    :result-text="getToolResult(msg.events, eIdx)"
                    :duration="getToolDuration(msg.events, eIdx)"
                    :status="getToolStatus(msg.events, eIdx)"
                    :retry-attempt="getToolRetryAttempt(msg.events, eIdx)"
                  />
                </template>
                <!-- Standalone error -->
                <div
                  v-else-if="evt.type === 'tool_error'"
                  class="tool-error-standalone"
                >
                  ❌ {{ evt.content }}
                </div>
                <!-- Retry hint（issue #250 第 6 条收尾：事件此前只落 store 的
                     msg.events，视图没有渲染分支 —— UI 仍看不到；#253 run_report
                     同款处置的"展示"一半；issue #289 抽为共享组件，与聊天页
                     Agent 模式共用） -->
                <RetryHintLine v-else-if="evt.type === 'retry_hint'" :content="evt.content" />
                <!-- 整次运行失败（issue #288：error 事件此前只到 store 的
                     msg.events（170-176 已停 loading），视图没有渲染分支 ——
                     失败时用户只见空气泡、看不到失败原因；与 tool_error 的
                     单工具失败行以「运行失败」徽标 + 红边框做视觉区分） -->
                <div v-else-if="evt.type === 'error'" class="run-error-standalone">
                  <span class="run-error-badge">运行失败</span>
                  <span class="run-error-text">{{ evt.content }}</span>
                </div>
                <!-- HITL 取消（浏览器验收 F4）：run_cancelled 此前只落 store，
                     页面无"已取消"字样——用户无法分辨"停住了"。中立灰徽标：
                     取消是正常收敛，不是错误（诚实：停得住 ≠ 出错）。 -->
                <div v-else-if="evt.type === 'run_cancelled'" class="run-cancelled-standalone">
                  <span class="run-cancelled-badge">{{ t('hitl.cancelled') }}</span>
                  <span class="run-cancelled-text">{{ evt.content }}</span>
                </div>
                <!-- 落库失败告警（issue #301 批 D #22 / B29：后端在「回答已生成、
                     但会话落库失败」时发射 {type:'warning', content}
                     （agent.py:207-212 / :341-347），store 已收（stores/agent.ts
                     warning case），但视图此前无渲染分支 —— 唯一失败信号不可见，
                     用户刷新后问答消失仍无提示）。amber 底纹与「运行失败」红框区分。 -->
                <div v-else-if="evt.type === 'warning'" class="run-warning-standalone">
                  <span class="run-warning-badge">提示</span>
                  <span class="run-warning-text">{{ evt.content }}</span>
                </div>
              </template>
            </div>

            <!-- Reflection -->
            <ReflectionBanner
              v-for="evt in msg.events.filter((e) => e.type === 'reflection')"
              :key="'refl-' + evt.timestamp"
              :result="evt.reflection_result || 'pass'"
              :text="evt.content"
            />

            <!-- Final answer -->
            <div v-if="msg.content" class="final-answer">
              <Markdown :content="msg.content" />
            </div>

            <!-- 运行报告（issue #242 收尾：此前该事件在前端白名单被丢弃，UI 永远看不到；
                 issue #289 抽为共享组件，与聊天页 Agent 模式共用） -->
            <RunReportBlock :report="msg.runReport" :total-tokens="msg.executionContext?.total_tokens" />

            <!-- Feedback buttons (only after completion, only for messages with a messageId) -->
            <div v-if="!msg.loading && msg.messageId && msg.content" class="feedback-bar">
              <n-tooltip trigger="hover">
                <template #trigger>
                  <n-button size="tiny" quaternary circle
                    :class="msg.feedback === 1 ? 'feedback-active-up' : 'feedback-inactive'"
                    @click="handleFeedback(msg, 1)"
                  >
                    <template #icon><n-icon><ThumbsUpOutline /></n-icon></template>
                  </n-button>
                </template>
                有帮助
              </n-tooltip>
              <n-tooltip trigger="hover">
                <template #trigger>
                  <n-button size="tiny" quaternary circle
                    :class="msg.feedback === -1 ? 'feedback-active-down' : 'feedback-inactive'"
                    @click="handleFeedback(msg, -1)"
                  >
                    <template #icon><n-icon><ThumbsDownOutline /></n-icon></template>
                  </n-button>
                </template>
                没有帮助
              </n-tooltip>
            </div>

            <!-- Loading -->
            <div v-if="msg.loading && msg.content.length === 0 && msg.thinkingText.length === 0" class="loading-indicator">
              <span class="loading-dot"></span>
              <span class="loading-dot" style="animation-delay: 150ms"></span>
              <span class="loading-dot" style="animation-delay: 300ms"></span>
            </div>
          </div>
        </div>
      </div>

      <!-- Right: Config Panel -->
      <div class="right-panel" v-if="showConfig">
        <AgentConfigPanel
          :config="agentStore.config"
          @apply="handleConfigApply"
        />
      </div>
      </template>
    </div>

    <!-- Status bar -->
    <div class="status-bar">
      <span>工具: {{ agentStore.enabledToolCount }}/{{ agentStore.toolCount }}</span>
      <span v-if="agentStore.currentSessionId">
        会话: {{ agentStore.currentSession?.title || agentStore.currentSessionId?.slice(0, 8) }}
      </span>
      <span v-if="agentStore.overallProgress > 0">
        进度: {{ Math.round(agentStore.overallProgress * 100) }}%
      </span>
    </div>

    <!-- HITL 审批卡（dock 在输入区上方：暂停点在哪，卡片就在哪等人） -->
    <div v-if="agentStore.pendingApproval" class="approval-dock">
      <ApprovalCard
        :tool-name="agentStore.pendingApproval.tool_name"
        :tool-args="agentStore.pendingApproval.tool_args"
        :risk-level="agentStore.pendingApproval.risk_level"
        :reason="agentStore.pendingApproval.reason"
        :busy="approvalBusy"
        @decide="handleApprovalDecide"
      />
    </div>

    <!-- Input -->
    <AgentInput
      :placeholder="'输入复杂任务，Agent 自动规划执行...'"
      :disabled="false"
      :loading="agentStore.isLoading"
      :stopping="stopping"
      @send="handleSend"
      @stop="handleStop"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import { useAgentStore } from '@/stores/agent'
import { agentApi } from '@/api/agent'
import { useDedupedMessage } from '@/utils/message'
import type { AgentEvent, AgentChatMessage, PlanStep, ThinkingType, AgentSession } from '@/types/agent'
import { ThumbsUpOutline, ThumbsDownOutline } from '@vicons/ionicons5'

import PlanTree from '@/components/agent/PlanTree.vue'
import ThinkingStream from '@/components/agent/ThinkingStream.vue'
import ToolCallCard from '@/components/agent/ToolCallCard.vue'
import ReflectionBanner from '@/components/agent/ReflectionBanner.vue'
import RetryHintLine from '@/components/agent/RetryHintLine.vue'
import RunReportBlock from '@/components/agent/RunReportBlock.vue'
import AgentConfigPanel from '@/components/agent/AgentConfigPanel.vue'
import SessionSelector from '@/components/agent/SessionSelector.vue'
import AgentInput from '@/components/agent/AgentInput.vue'
import ApprovalCard from '@/components/agent/ApprovalCard.vue'
import Markdown from '@/components/common/Markdown.vue'

const agentStore = useAgentStore()
const { t } = useI18n()
const message = useDedupedMessage()

// 当前进行中的 Agent chat 请求控制器（用于停止/卸载时中止）
let currentChatController: AbortController | null = null
// HITL：停止中的收敛窗口定时器（服务端取消受理后，给 run 检查点收敛的时间；
// 超时兜底才硬断流——协作式取消不会被网络层"假装成功"）。
let stopFallbackTimer: ReturnType<typeof setTimeout> | null = null
const stopping = ref(false)
const approvalBusy = ref(false)

const showConfig = ref(false)
const showPlan = ref(true)
const centerPanel = ref<HTMLElement>()
const initLoading = ref(true)
const initError = ref(false)

const suggestions = [
  '知识库中有哪些文档？请帮我总结关键主题',
  '搜索公司相关的所有政策文档，提取合规要求',
  '分析最近上传的文档，找出共同点和差异',
  '帮我翻译这段中文到英文，并检测语言',
]

const currentPlanSteps = computed<PlanStep[]>(() => {
  // Get plan steps from the last assistant message
  const last = [...agentStore.messages].reverse().find((m) => m.role === 'assistant')
  return last?.planSteps || agentStore.planSteps
})

function getLastThinkingType(msg: AgentChatMessage): ThinkingType {
  const thinkEvents = msg.events.filter((e) => e.type === 'thinking')
  if (thinkEvents.length === 0) return 'reasoning'
  return thinkEvents[thinkEvents.length - 1].thinking_type || 'reasoning'
}

function getToolResult(events: AgentEvent[], callIdx: number): string | undefined {
  // Look ahead for matching tool_result
  const callId = events[callIdx]?.tool_call_id
  if (!callId) return undefined
  for (let i = callIdx + 1; i < events.length; i++) {
    if (events[i].type === 'tool_result' && events[i].tool_call_id === callId) {
      return events[i].content
    }
    if (events[i].type === 'tool_error' && events[i].tool_call_id === callId) {
      return events[i].content
    }
  }
  return undefined
}

function getToolDuration(events: AgentEvent[], callIdx: number): number | undefined {
  const callId = events[callIdx]?.tool_call_id
  if (!callId) return undefined
  for (let i = callIdx + 1; i < events.length; i++) {
    if (events[i].tool_call_id === callId && events[i].tool_duration_ms) {
      return events[i].tool_duration_ms
    }
  }
  return undefined
}

async function handleFeedback(msg: AgentChatMessage, value: number) {
  if (!msg.messageId) return
  // Toggle: if already selected, deselect
  const fb = msg.feedback === value ? 0 : value
  msg.feedback = fb
  try {
    await agentApi.submitFeedback(msg.messageId, fb)
  } catch {
    msg.feedback = 0
    message.error('反馈提交失败')
  }
}

function getToolStatus(
  events: AgentEvent[],
  callIdx: number,
): 'loading' | 'success' | 'error' {
  const callId = events[callIdx]?.tool_call_id
  if (!callId) return 'loading'
  for (let i = callIdx + 1; i < events.length; i++) {
    if (events[i].tool_call_id === callId) {
      if (events[i].type === 'tool_result') return 'success'
      if (events[i].type === 'tool_error') return 'error'
    }
  }
  return 'loading'
}

// issue #289 G2：ToolCallCard 的「重试 N」徽标此前绑定 tool_call 的
// evt.retry_attempt —— 后端 tool_call 事件从不携带该字段（恒 undefined，
// 徽标永不显示）。后端真实发射点是 tool_error（executor.py：值为该步骤
// 已重试次数），故与 getToolResult 同款前瞻配对，从配对的 tool_error 取数。
function getToolRetryAttempt(events: AgentEvent[], callIdx: number): number | undefined {
  const callId = events[callIdx]?.tool_call_id
  if (!callId) return undefined
  for (let i = callIdx + 1; i < events.length; i++) {
    if (events[i].tool_call_id !== callId) continue
    if (events[i].type === 'tool_result') return undefined
    if (events[i].type === 'tool_error') return events[i].retry_attempt || undefined
  }
  return undefined
}

function scrollToBottom() {
  nextTick(() => {
    if (centerPanel.value) {
      centerPanel.value.scrollTop = centerPanel.value.scrollHeight
    }
  })
}

async function handleSend(query: string) {
  if (agentStore.isLoading || !query.trim()) return

  agentStore.clearMessages()
  agentStore.resetTask()
  stopping.value = false

  // Add user message
  agentStore.addMessage({
    role: 'user',
    content: query,
    events: [],
    loading: false,
    currentTool: '',
    thinkingText: '',
    planSteps: [],
    planId: '',
    progress: 0,
  })

  // Add assistant placeholder
  agentStore.addMessage({
    role: 'assistant',
    content: '',
    events: [],
    loading: true,
    currentTool: '',
    thinkingText: '',
    planSteps: [],
    planId: '',
    progress: 0,
  })

  agentStore.setLoading(true)
  scrollToBottom()

  const chatController = new AbortController()
  currentChatController = chatController

  try {
    await agentApi.chat(
      query,
      (event: AgentEvent) => {
        agentStore.processEvent(event)
        scrollToBottom()
      },
      {
        signal: chatController.signal,
        sessionId: agentStore.currentSessionId || undefined,
        enableTools: agentStore.config.enable_tools,
        enablePlanning: agentStore.config.enable_planning,
        enableReflection: agentStore.config.enable_reflection,
        enableMemory: agentStore.config.enable_memory,
        enableThinking: agentStore.config.enable_thinking,
        model: agentStore.config.model,
        temperature: agentStore.config.temperature,
        disabledTools: agentStore.config.disabled_tools,
        systemPromptOverride: agentStore.config.system_prompt_override,
      },
    )
  } catch (e: unknown) {
    // AbortError is expected when user clicks stop
    if (e instanceof DOMException && e.name === 'AbortError') {
      agentStore.updateLastAssistant((msg) => {
        if (!msg.content) msg.content = '(已停止)'
        msg.loading = false
      })
    } else {
      const errMsg = e instanceof Error ? e.message : String(e)
      agentStore.updateLastAssistant((msg) => {
        msg.content = `❌ 错误: ${errMsg || 'Agent 连接失败'}`
        msg.loading = false
      })
    }
  } finally {
    currentChatController = null
    agentStore.setLoading(false)
    stopping.value = false
    if (stopFallbackTimer) {
      clearTimeout(stopFallbackTimer)
      stopFallbackTimer = null
    }
    scrollToBottom()
  }
}

/**
 * HITL 停止：先请求**服务端**协作式取消（POST /tasks/{id}/cancel），流内等
 * run_cancelled/done 收敛；取消通道不可用才退回客户端断流（如实的能力边界，
 * 不假装服务端已受理）。无任务上下文（checkpoint 未接）时直接客户端断流。
 */
function handleStop() {
  const taskId = agentStore.taskId
  if (!taskId) {
    fallbackAbort()
    return
  }
  stopping.value = true
  agentApi.cancelTask(taskId).catch(() => {
    // 503（取消通道不可用）等：如实退回断流
    fallbackAbort()
  })
  // 兜底：run 卡在长调用里（工具/LLM 不中断，等它返回检查点）——给收敛窗口，
  // 超时仍未收敛才硬断流（UI 不无限等下去）。
  if (stopFallbackTimer) clearTimeout(stopFallbackTimer)
  stopFallbackTimer = setTimeout(() => {
    stopFallbackTimer = null
    if (agentStore.isLoading) {
      fallbackAbort()
    }
  }, 15000)
}

function fallbackAbort() {
  currentChatController?.abort()
  currentChatController = null
  agentStore.setLoading(false)
  stopping.value = false
  agentStore.updateLastAssistant((msg) => {
    msg.loading = false
  })
}

/**
 * HITL 审批应答：approve/deny/改指令 → 消费续跑 SSE（事件与 chat 同契约）。
 * 409/404 = 现场已变（已被应答/取消/超时）→ 刷新权威状态回到现场。
 */
async function handleApprovalDecide(payload: {
  decision: 'approve' | 'deny'
  modifiedInstruction?: string
  note?: string
}) {
  const taskId = agentStore.taskId
  if (!taskId || approvalBusy.value) return
  approvalBusy.value = true
  const controller = new AbortController()
  currentChatController = controller
  agentStore.setTaskState('running')
  agentStore.setLoading(true)
  // 乐观清除卡片：续跑若再次暂停，approval_required 事件会重建；失败则由刷新恢复。
  agentStore.setPendingApproval(null)
  try {
    await agentApi.decideApproval(
      taskId,
      {
        decision: payload.decision,
        modified_instruction: payload.modifiedInstruction,
        note: payload.note,
      },
      (event: AgentEvent) => {
        agentStore.processEvent(event)
        scrollToBottom()
      },
      { signal: controller.signal },
    )
  } catch {
    message.error('审批提交失败，已刷新任务状态')
    await refreshTaskState(taskId)
  } finally {
    approvalBusy.value = false
    currentChatController = null
    agentStore.setLoading(false)
    scrollToBottom()
  }
}

/** 从权威接口刷新任务现场（审批冲突/提交失败后的恢复路径）。 */
async function refreshTaskState(taskId: string) {
  try {
    const res = await agentApi.getTask(taskId)
    // F2：request 拦截器返回完整 AxiosResponse，信封在 res.data = {success, data}
    const data = res.data?.data
    if (!data) return
    agentStore.setTaskState(data.state)
    if (data.state === 'waiting_approval' && data.approval) {
      agentStore.setTaskId(taskId)
      agentStore.setPendingApproval(data.approval)
    } else {
      agentStore.setPendingApproval(null)
    }
  } catch {
    // 刷新失败不覆盖已有现场（保持现状，下一次操作再试）
  }
}

/** 刷新/重连后回到审批现场（消费既有 GET task + 持久化的 last task id）。 */
async function restorePendingApproval() {
  const last = agentStore.loadPersistedTaskId()
  if (!last) return
  try {
    const res = await agentApi.getTask(last)
    // F2：读信封内层（res.data = {success, data}；此前读 res.data 导致
    // data.state 恒 undefined，刷新后审批卡永不恢复——浏览器验收实锤）
    const data = res.data?.data
    if (data?.state === 'waiting_approval' && data.approval) {
      agentStore.setTaskId(last)
      agentStore.setTaskState('waiting_approval')
      agentStore.setPendingApproval(data.approval)
    } else if (
      data &&
      ['completed', 'cancelled', 'failed', 'abandoned'].includes(data.state)
    ) {
      agentStore.setTaskId(null) // 陈旧指针：清掉
    }
  } catch {
    // 任务不存在/无权（过期清理等）→ 清掉陈旧指针
    agentStore.setTaskId(null)
  }
}

// 组件卸载时中止进行中的 Agent SSE 流，防止请求悬挂
onUnmounted(() => {
  currentChatController?.abort()
  currentChatController = null
  if (stopFallbackTimer) {
    clearTimeout(stopFallbackTimer)
    stopFallbackTimer = null
  }
})

async function handleSessionChange(sessionId: string | null) {
  agentStore.setCurrentSession(sessionId)
  if (sessionId) {
    try {
      const res = await agentApi.getSession(sessionId)
      // 真载荷在 data.data.config（agent.py:637-647 的 {success, data:{…,config}}）；
      // 旧读 res.data.config 恒 undefined → 会话配置静默 no-op（B25）
      const detail = res.data?.data
      if (detail?.config) {
        agentStore.updateConfig(detail.config)
      }
    } catch (e) {
      message.error('加载会话失败')
    }
  }
}

async function handleSessionDelete(sessionId: string) {
  try {
    await agentApi.deleteSession(sessionId)
    agentStore.setSessions(agentStore.sessions.filter((s) => s.id !== sessionId))
    if (agentStore.currentSessionId === sessionId) {
      agentStore.setCurrentSession(null)
    }
    message.success('会话已删除')
  } catch (e) {
    message.error('删除会话失败')
  }
}

async function handleNewSession() {
  try {
    const res = await agentApi.createSession('New Agent Session')
    // 创建端点只回 {success, data:{id, title}}（agent.py:585-591），真载荷在
    // data.data；旧实现把整个信封当会话对象 → 幻影「Untitled ()」项（选中即
    // 404）、currentSessionId 失效（B26）
    const session = res.data?.data
    if (session?.id) {
      agentStore.setSessions([
        { ...session, message_count: 0, created_at: '', updated_at: '' } as AgentSession,
        ...agentStore.sessions,
      ])
      agentStore.setCurrentSession(session.id)
      message.success('已创建新会话')
    }
  } catch (e) {
    message.error('创建会话失败')
  }
}

function handleConfigApply(config: typeof agentStore.config) {
  agentStore.updateConfig(config)
  // Persist to server
  agentApi.updateConfig(config).catch(() => {})
}

// Load initial data — wrap in try/catch to prevent errors from
// propagating through Suspense and triggering the ErrorBoundary.
onMounted(async () => {
  try {
    await loadInit()
    // HITL：刷新/重连后回到审批现场（last task id 持久化 + 权威状态）
    await restorePendingApproval()
  } catch (e) {
    console.error('[Agent] onMounted error:', e)
    initError.value = true
    initLoading.value = false
  }
})

async function reloadInit() {
  initError.value = false
  await loadInit()
}

async function loadInit() {
  initLoading.value = true
  initError.value = false
  try {
    const [toolsRes, sessionsRes, configRes] = await Promise.all([
      agentApi.listTools(),
      agentApi.listSessions(),
      agentApi.getConfig(),
    ])
    // 三个端点均为 {success, data} 信封（agent.py:450-472 工具数组在 data、
    // :500-556 会话在 data.sessions、:696-710 配置在 data）——真载荷在
    // res.data.data（axios body 再一层 data）。旧实现把信封当载荷 →
    // 工具列表永不下发（「工具: 0/0」）、会话下拉恒空、整信封写入
    // store.config（B22/B23/B24，同族嵌套漂移）
    const toolsData = toolsRes.data?.data
    const sessionsData = sessionsRes.data?.data
    const configData = configRes.data?.data
    if (Array.isArray(toolsData)) agentStore.tools = toolsData
    if (Array.isArray(sessionsData?.sessions)) agentStore.setSessions(sessionsData.sessions)
    if (configData && typeof configData === 'object') agentStore.updateConfig(configData)
  } catch (e) {
    initError.value = true
    try { message.error('Agent 初始化失败') } catch { /* provider not ready */ }
  } finally {
    initLoading.value = false
  }
}
</script>

<style scoped>
/* 注：retry-hint 行与 run-report 折叠块已抽为共享组件
   （components/agent/RetryHintLine.vue / RunReportBlock.vue，issue #289），
   样式随组件走，本页与聊天页 Agent 模式共用同一实现。 */
.agent-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--bg-primary, #fff);
}

.dark .agent-page {
  background: var(--bg-primary-dark, #0f172a);
}

/* Header */
.agent-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px;
  border-bottom: 1px solid var(--border-color, #e5e7eb);
  gap: 12px;
  flex-shrink: 0;
}

.dark .agent-header {
  border-color: var(--border-color-dark, #1e293b);
}

.header-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.header-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary, #111827);
  margin: 0;
}

.dark .header-title { color: var(--text-primary-dark, #f9fafb); }

.loading-overlay {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  width: 100%;
  gap: 16px;
}

.loading-text {
  font-size: 13px;
  color: #94a3b8;
}

.error-overlay {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  width: 100%;
  gap: 12px;
}

.error-icon {
  font-size: 32px;
}

.error-text {
  font-size: 13px;
  color: #94a3b8;
}

.header-badge {
  font-size: 10px;
  background: linear-gradient(135deg, #3b82f6, #6366f1);
  color: white;
  padding: 2px 8px;
  border-radius: 6px;
  font-weight: 500;
}

.header-center { flex: 1; display: flex; justify-content: center; }

.header-right { display: flex; gap: 6px; }

.header-btn {
  padding: 5px 10px;
  border-radius: 8px;
  border: 1px solid var(--border-color, #d1d5db);
  background: var(--bg-primary, #fff);
  color: var(--text-secondary, #4b5563);
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s;
}

.dark .header-btn {
  background: var(--bg-secondary-dark, #1f2937);
  border-color: var(--border-color-dark, #374151);
  color: var(--text-secondary-dark, #9ca3af);
}

.header-btn:hover {
  background: var(--bg-secondary, #f3f4f6);
}

.header-btn.active {
  background: rgba(59, 130, 246, 0.1);
  border-color: #3b82f6;
  color: #3b82f6;
}

/* Body */
.agent-body {
  display: flex;
  flex: 1;
  overflow: hidden;
}

/* Left panel */
.left-panel {
  width: 260px;
  border-right: 1px solid var(--border-color, #e5e7eb);
  overflow-y: auto;
  flex-shrink: 0;
  padding: 12px;
  position: relative;
  transition: width 0.3s;
}

.dark .left-panel {
  border-color: var(--border-color-dark, #1e293b);
}

.left-panel.collapsed {
  width: 28px;
  padding: 12px 4px;
}

.panel-toggle {
  position: absolute;
  top: 8px;
  right: -14px;
  z-index: 10;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: var(--bg-secondary, #f3f4f6);
  border: 1px solid var(--border-color, #d1d5db);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  font-size: 10px;
  color: var(--text-tertiary, #9ca3af);
}

.dark .panel-toggle {
  background: var(--bg-secondary-dark, #1f2937);
  border-color: var(--border-color-dark, #374151);
}

/* Center panel */
.center-panel {
  flex: 1;
  overflow-y: auto;
  padding: 16px 20px;
}

/* Right panel */
.right-panel {
  width: 280px;
  border-left: 1px solid var(--border-color, #e5e7eb);
  overflow-y: auto;
  flex-shrink: 0;
  padding: 12px;
}

.dark .right-panel {
  border-color: var(--border-color-dark, #1e293b);
}

/* Empty state */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  text-align: center;
}

.empty-state h2 {
  font-size: 20px;
  font-weight: 600;
  color: var(--text-primary, #111827);
  margin-bottom: 4px;
}

.dark .empty-state h2 { color: var(--text-primary-dark, #f9fafb); }

.empty-subtitle {
  font-size: 13px;
  color: var(--text-tertiary, #9ca3af);
  margin-bottom: 24px;
  font-family: monospace;
}

.suggestion-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  max-width: 520px;
  margin-bottom: 24px;
}

.suggestion-btn {
  text-align: left;
  padding: 10px 14px;
  border-radius: 10px;
  border: 1px solid var(--border-color, #e5e7eb);
  background: var(--bg-secondary, #f9fafb);
  color: var(--text-secondary, #4b5563);
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s;
}

.dark .suggestion-btn {
  border-color: var(--border-color-dark, #374151);
  background: var(--bg-secondary-dark, #1f2937);
  color: var(--text-secondary-dark, #9ca3af);
}

.suggestion-btn:hover {
  border-color: #3b82f6;
  background: rgba(59, 130, 246, 0.05);
  color: #3b82f6;
}

.feature-list {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  justify-content: center;
  max-width: 520px;
}

.feature-item {
  font-size: 11px;
  padding: 4px 10px;
  border-radius: 6px;
  background: var(--bg-tertiary, #f3f4f6);
  color: var(--text-secondary, #6b7280);
}

.dark .feature-item {
  background: var(--bg-tertiary-dark, #374151);
  color: var(--text-secondary-dark, #9ca3af);
}

.empty-panel {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
}

.empty-text {
  font-size: 11px;
  color: var(--text-tertiary, #9ca3af);
  text-align: center;
  line-height: 1.6;
}

/* Messages */
.message-group {
  margin-bottom: 16px;
}

.user-msg {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 12px;
}

.user-bubble {
  max-width: 75%;
  padding: 10px 16px;
  border-radius: 16px 16px 4px 16px;
  background: #3b82f6;
  color: white;
  font-size: 13px;
  line-height: 1.5;
  word-break: break-word;
}

.agent-msg {
  margin-bottom: 16px;
}

.final-answer {
  padding: 12px 16px;
  background: var(--bg-secondary, #f9fafb);
  border-radius: 12px;
  border: 1px solid var(--border-color, #e5e7eb);
}

.dark .final-answer {
  background: var(--bg-secondary-dark, #1f2937);
  border-color: var(--border-color-dark, #374151);
}

/* Feedback bar */
.feedback-bar {
  display: flex;
  gap: 4px;
  padding: 6px 0 2px 4px;
}

.feedback-inactive {
  color: var(--text-tertiary, #9ca3af) !important;
  transition: color 0.15s;
}

.feedback-inactive:hover {
  color: var(--text-secondary, #6b7280) !important;
}

.feedback-active-up {
  color: #16a34a !important;
}

.feedback-active-down {
  color: #dc2626 !important;
}

.tool-error-standalone {
  padding: 8px 12px;
  background: rgba(239, 68, 68, 0.08);
  border-radius: 8px;
  font-size: 12px;
  color: #dc2626;
  margin: 4px 0;
}

/* 整次运行失败（issue #288：error 事件的渲染分支）。与 tool_error 的
   单工具失败行（上方 .tool-error-standalone，浅底无边框）区分：加「运行
   失败」实心徽标 + 左边框强调，表明是整次运行中止而非某个工具出错。 */
.run-error-standalone {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin: 4px 0;
  padding: 8px 12px;
  background: rgba(220, 38, 38, 0.1);
  border: 1px solid rgba(220, 38, 38, 0.35);
  border-left: 3px solid #dc2626;
  border-radius: 8px;
  font-size: 12px;
  color: #b91c1c;
}

.run-error-badge {
  flex-shrink: 0;
  padding: 0 6px;
  border-radius: 4px;
  background: #dc2626;
  color: #fff;
  font-size: 10px;
  font-weight: 600;
  line-height: 18px;
}

.run-error-text {
  word-break: break-word;
  line-height: 18px;
}

/* HITL 取消标识（F4）：中立灰——取消是正常收敛而非运行失败，视觉上不与
   "运行失败"红徽标混淆。 */
.run-cancelled-standalone {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin: 4px 0;
  padding: 8px 12px;
  background: rgba(100, 116, 139, 0.08);
  border: 1px solid rgba(100, 116, 139, 0.3);
  border-left: 3px solid #64748b;
  border-radius: 8px;
  font-size: 12px;
  color: #475569;
}

.dark .run-cancelled-standalone { color: #94a3b8; }

.run-cancelled-badge {
  flex-shrink: 0;
  padding: 0 6px;
  border-radius: 4px;
  background: #64748b;
  color: #fff;
  font-size: 10px;
  font-weight: 600;
  line-height: 18px;
}

.run-warning-standalone {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin: 4px 0;
  padding: 8px 12px;
  background: rgba(245, 158, 11, 0.1);
  border: 1px solid rgba(245, 158, 11, 0.35);
  border-left: 3px solid #f59e0b;
  border-radius: 8px;
  font-size: 12px;
  color: #92400e;
}

.dark .run-warning-standalone {
  color: #fcd34d;
}

.run-warning-badge {
  flex-shrink: 0;
  padding: 0 6px;
  border-radius: 4px;
  background: #f59e0b;
  color: #fff;
  font-size: 10px;
  font-weight: 600;
  line-height: 18px;
}

.run-cancelled-text {
  word-break: break-word;
  line-height: 18px;
}

.run-warning-text {
  word-break: break-word;
  line-height: 18px;
}

.loading-indicator {
  display: flex;
  gap: 4px;
  padding: 12px 0;
}

.loading-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #3b82f6;
  animation: bounce 1s ease-in-out infinite;
}

@keyframes bounce {
  0%, 100% { opacity: 0.3; transform: translateY(0); }
  50% { opacity: 1; transform: translateY(-4px); }
}

/* HITL 审批卡 dock：悬在输入区上方（暂停点在哪，卡片就在哪等人） */
.approval-dock {
  padding: 0 16px;
  flex-shrink: 0;
}

/* Status bar */
.status-bar {
  display: flex;
  gap: 16px;
  padding: 4px 16px;
  border-top: 1px solid var(--border-color, #e5e7eb);
  font-size: 10px;
  color: var(--text-tertiary, #9ca3af);
  background: var(--bg-secondary, #f9fafb);
  flex-shrink: 0;
}

.dark .status-bar {
  background: var(--bg-secondary-dark, #111827);
  border-color: var(--border-color-dark, #1e293b);
}

/* === Responsive: collapse 3-column to single column === */
@media (max-width: 767px) {
  .agent-body {
    flex-direction: column;
  }

  .left-panel {
    width: 100% !important;
    max-height: 0;
    overflow: hidden;
    padding: 0 12px;
    border-right: none;
    border-bottom: 1px solid var(--border-color, #e5e7eb);
    transition: max-height 0.3s ease, padding 0.3s ease;
  }
  .dark .left-panel {
    border-bottom-color: var(--border-color-dark, #1e293b);
  }
  .left-panel.collapsed {
    max-height: 0;
    padding: 0 12px;
    width: 100% !important;
  }
  .left-panel:not(.collapsed) {
    max-height: 200px;
    padding: 12px;
    overflow-y: auto;
  }

  .left-panel .panel-toggle {
    display: none;
  }

  .right-panel {
    width: 100% !important;
    max-height: 0;
    overflow: hidden;
    padding: 0 12px;
    border-left: none;
    border-top: 1px solid var(--border-color, #e5e7eb);
    transition: max-height 0.3s ease, padding 0.3s ease;
  }
  .dark .right-panel {
    border-top-color: var(--border-color-dark, #1e293b);
  }
  .right-panel:not([style*="display: none"]) {
    max-height: 300px;
    padding: 12px;
    overflow-y: auto;
  }

  .center-panel {
    flex: 1;
    min-height: 0;
    padding: 12px;
  }

  .agent-header {
    flex-wrap: wrap;
    padding: 8px 12px;
    gap: 8px;
  }

  .header-center {
    order: 3;
    flex: 0 0 100%;
    justify-content: flex-start;
  }

  .header-right {
    gap: 4px;
  }

  .header-btn {
    padding: 4px 8px;
    font-size: 11px;
  }

  .suggestion-grid {
    grid-template-columns: 1fr;
    max-width: 100%;
  }

  .feature-list {
    max-width: 100%;
  }

  .status-bar {
    flex-wrap: wrap;
    gap: 8px;
    font-size: 9px;
  }

  .empty-state h2 {
    font-size: 16px;
  }
}

/* Tablet: some adjustments */
@media (min-width: 768px) and (max-width: 1023px) {
  .left-panel {
    width: 200px;
  }
  .right-panel {
    width: 240px;
  }
  .center-panel {
    padding: 12px 16px;
  }
}
</style>
