<template>
  <div class="agent-page">
    <!-- Header -->
    <div class="agent-header">
      <div class="header-left">
        <!-- T9：首页/通知深链进入工作台后的清晰返回路径（会话列表在对话页侧栏） -->
        <button
          type="button"
          class="header-btn"
          data-testid="agent-back-to-chat"
          @click="backToChat"
        >
          <span aria-hidden="true">←</span> {{ t('agent.backToChat') }}
        </button>
        <h1 class="header-title">{{ t('menu.agent') }}</h1>
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
        <n-popover v-model:show="showTasks" trigger="click" placement="bottom-end">
          <template #trigger>
            <button class="header-btn" :class="{ active: showTasks }">
              🕘 {{ t('agentTasks.title') }}
            </button>
          </template>
          <RecentTaskList
            :tasks="recentTasks"
            :loading="tasksLoading"
            @view-replay="handleTaskReplayClick"
          />
        </n-popover>
        <button class="header-btn" @click="showConfig = !showConfig" :class="{ active: showConfig }">
          ⚙ 配置
        </button>
        <button
          class="header-btn"
          :class="{ active: showTerminal }"
          data-testid="agent-terminal-toggle"
          @click="showTerminal = !showTerminal"
        >
          <span aria-hidden="true">&gt;_</span> {{ t('agent.terminal.open') }}
        </button>
        <button class="header-btn" @click="handleNewSession">+ 新建</button>
        <button class="header-btn" @click="handleClearMessages">清空</button>
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
            <div class="feature-item">🔧 33 个内置工具：搜索、分析、代码、翻译</div>
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
            <div class="user-bubble">
              {{ msg.content }}
              <!-- 体验第四波 @引用：用户消息携带的引用文档（回溯用） -->
              <div v-if="msg.references?.length" class="user-refs" data-testid="user-refs">
                <span
                  v-for="r in msg.references"
                  :key="r.id"
                  class="user-ref-chip"
                >📄 {{ r.title }}</span>
              </div>
            </div>
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
                <!-- A：运行中转向——发送成功（本地落账）与步边界生效（后端 SSE）
                     两条留痕，蓝色系与取消的灰、失败的红区分。 -->
                <div v-else-if="evt.type === 'steer_sent'" class="steer-standalone">
                  <span class="steer-badge">{{ t('hitl.steerSent') }}</span>
                  <span class="steer-text">{{ evt.content }}</span>
                </div>
                <div
                  v-else-if="evt.type === 'steer_applied'"
                  class="steer-standalone steer-applied"
                >
                  <span class="steer-badge steer-badge-applied">{{ t('hitl.steerApplied') }}</span>
                  <span class="steer-text">{{ evt.content }}</span>
                </div>
                <!-- B：计划审批留痕（与取消/转向同款徽标族；紫=计划域） -->
                <div
                  v-else-if="evt.type === 'plan_approval_required'"
                  class="plan-standalone"
                >
                  <span class="plan-standalone-badge">{{ t('hitl.planTimeline') }}</span>
                  <span class="plan-standalone-text">{{ evt.content }}</span>
                </div>
                <!-- warning 事件渲染（B29 / 体验第四波：@引用失效、#301 落库失败等
                     此前只落 store、无视图分支——唯一可见信号不可见，用户刷新后
                     问答消失仍无提示）。两支同型实现（本支 timeline 条目 /
                     develop B29 落库失败提示）合一：取 develop 类名，补本支
                     data-testid 锚点；徽标文案走 i18n，琥珀色与失败红/取消灰区分。 -->
                <div
                  v-else-if="evt.type === 'warning'"
                  class="run-warning-standalone"
                  data-testid="warning-line"
                >
                  <span class="run-warning-badge">{{ t('common.warning') }}</span>
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

            <!-- 计时 + 成本（体验第四波）：运行中秒级计时；终态显示总用时 /
                 tokens / 估算成本（execution_context 数据 + 可配置价目表） -->
            <RunStats
              v-if="msg.startedAt"
              :loading="msg.loading"
              :started-at="msg.startedAt"
              :ended-at="msg.endedAt"
              :tokens="msg.executionContext?.total_tokens"
              :model="agentStore.config.model"
            />

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

            <!-- 回放入口：本次运行完整结束且确有回放数据（GET task 的 has_replay
                 如实标记；无数据不亮入口，深链/任务列表可达诚实空态） -->
            <div v-if="showReplayEntry(idx, msg)" class="replay-entry-line">
              <button class="replay-entry-btn" @click="openReplay(agentStore.taskId!)">
                🕘 {{ t('agentReplay.entry') }}
              </button>
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

    <!-- B：计划审批卡（同一 dock 家族；计划与工具的审批互斥，不会同屏两张） -->
    <div v-if="agentStore.pendingPlanApproval" class="approval-dock">
      <PlanApprovalCard
        :goal="agentStore.pendingPlanApproval.goal"
        :steps="agentStore.pendingPlanApproval.steps"
        :busy="planApprovalBusy"
        @decide="handlePlanApprovalDecide"
      />
    </div>

    <!-- 回放时间线（与审批卡同款 dock；URL ?id= 深链直达） -->
    <ReplayTimeline
      v-if="replayOpen"
      :task-id="replayTaskId"
      :timeline="replayData"
      :loading="replayLoading"
      :error="replayError"
      @close="closeReplay"
    />

    <!-- 持久终端抽屉（issue #477）：xterm 面板挂在现有 /agent 页右侧，不新增路由 -->
    <n-drawer v-model:show="showTerminal" placement="right" :width="760">
      <n-drawer-content :title="t('agent.terminal.title')" closable>
        <TerminalPanel :session-id="agentStore.currentSessionId" />
      </n-drawer-content>
    </n-drawer>

    <!-- Input -->
    <AgentInput
      :placeholder="'输入复杂任务，Agent 自动规划执行...'"
      :disabled="planApprovalPending"
      :loading="agentStore.isLoading"
      :stopping="stopping"
      :steering="steerEnabled"
      :hint-override="planApprovalPending ? t('hitl.planPendingHint') : ''"
      @send="handleSend"
      @stop="handleStop"
      @steer="handleSteer"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useAgentStore } from '@/stores/agent'
import { agentApi } from '@/api/agent'
import { apiUnwrap } from '@/utils/apiUnwrap'
import { useDedupedMessage } from '@/utils/message'
import { useConfirmDangerousAction } from '@/utils/confirm'
import type {
  AgentEvent,
  AgentChatMessage,
  AgentTaskListItem,
  AgentTaskView,
  AgentSession,
  AgentSessionDetail,
  AgentConfig,
  ToolInfo,
  PlanStep,
  PlanStepEdit,
  ThinkingType,
  ReferencedDoc,
  ReplayTimeline as ReplayTimelineData,
} from '@/types/agent'
import { ThumbsUpOutline, ThumbsDownOutline } from '@vicons/ionicons5'

import PlanTree from '@/components/agent/PlanTree.vue'
import ThinkingStream from '@/components/agent/ThinkingStream.vue'
import ToolCallCard from '@/components/agent/ToolCallCard.vue'
import ReflectionBanner from '@/components/agent/ReflectionBanner.vue'
import RetryHintLine from '@/components/agent/RetryHintLine.vue'
import RunReportBlock from '@/components/agent/RunReportBlock.vue'
import RunStats from '@/components/agent/RunStats.vue'
import AgentConfigPanel from '@/components/agent/AgentConfigPanel.vue'
import SessionSelector from '@/components/agent/SessionSelector.vue'
import AgentInput from '@/components/agent/AgentInput.vue'
import ApprovalCard from '@/components/agent/ApprovalCard.vue'
import PlanApprovalCard from '@/components/agent/PlanApprovalCard.vue'
import ReplayTimeline from '@/components/agent/ReplayTimeline.vue'
import RecentTaskList from '@/components/agent/RecentTaskList.vue'
import TerminalPanel from '@/components/agent/TerminalPanel.vue'
import Markdown from '@/components/common/Markdown.vue'

const agentStore = useAgentStore()
const { t } = useI18n()
const message = useDedupedMessage()
const confirmDangerousAction = useConfirmDangerousAction()
// 深链（?id=<task_id>，通知点击/刷新直达回放）：测试环境可能无 router——全部访问带守卫
const route = useRoute()
const router = useRouter()

/** T9：工作台的清晰返回出口——回到对话页（会话列表在 ChatSidebar）。 */
function backToChat() {
  if (!router) return
  void router.push('/chat')
}

// 当前进行中的 Agent chat 请求控制器（用于停止/卸载时中止）
let currentChatController: AbortController | null = null
// HITL：停止中的收敛窗口定时器（服务端取消受理后，给 run 检查点收敛的时间；
// 超时兜底才硬断流——协作式取消不会被网络层"假装成功"）。
let stopFallbackTimer: ReturnType<typeof setTimeout> | null = null
const stopping = ref(false)
const approvalBusy = ref(false)
// A：转向提交在途标志（防连点重复提交）
const steerBusy = ref(false)
// B：计划审批提交在途标志
const planApprovalBusy = ref(false)

/** A：转向模式——任务 running（流打开）或 waiting_approval（暂停等应答）时可发指导。
 *  有 taskId 才接通服务端转向通道（无任务的裸跑没有队列可写）。 */
const steerEnabled = computed(
  () =>
    !!agentStore.taskId &&
    (agentStore.isLoading || agentStore.taskState === 'waiting_approval'),
)

/** B（评审 MINOR-B）：等待计划审批时输入区闸门——此时发送新消息会清掉任务指针、
 *  把暂停中的任务丢成孤儿（后端对该状态 steer 也诚实 409）。这里禁用输入并
 *  提示先处理计划卡（保持后端 409 语义不变，闸门只在前端体验层）。 */
const planApprovalPending = computed(
  () => agentStore.taskState === 'waiting_plan_approval' && !!agentStore.taskId,
)

const showConfig = ref(false)
const showTerminal = ref(false)
const showPlan = ref(true)
const centerPanel = ref<HTMLElement>()
const initLoading = ref(true)
const initError = ref(false)

// ── 回放消费面（跑完之后）：入口 / 时间线 dock / 任务记录 / 深链 ──────────────
const replayOpen = ref(false)
const replayTaskId = ref('')
const replayData = ref<ReplayTimelineData | null>(null)
const replayLoading = ref(false)
const replayError = ref<'' | 'not_found' | 'failed'>('')
// 最近一次运行是否有回放可看（由 GET task 的 has_replay 如实判定；null=未知/不亮入口）
const lastRunHasReplay = ref<boolean | null>(null)
const showTasks = ref(false)
const recentTasks = ref<AgentTaskListItem[]>([])
const tasksLoading = ref(false)

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

async function handleSend(query: string, refs?: ReferencedDoc[]) {
  if (agentStore.isLoading || !query.trim()) return

  agentStore.clearMessages()
  agentStore.resetTask()
  stopping.value = false
  lastRunHasReplay.value = null

  // Add user message（@引用随消息落账，供回溯）
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
    references: refs,
  })

  // Add assistant placeholder（startedAt：计时器起点）
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
    startedAt: Date.now(),
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
        // HITL：面板选择的审批模式随运行上送（与保存配置同源）
        approvalMode: agentStore.config.approval_mode,
        // @引用（体验第四波）：引用文档 id 上送，后端注入 planner 上下文
        referencedDocumentIds: refs?.map((r) => r.id),
      },
    )
  } catch (e: unknown) {
    // AbortError is expected when user clicks stop
    if (e instanceof DOMException && e.name === 'AbortError') {
      agentStore.updateLastAssistant((msg) => {
        if (!msg.content) msg.content = '(已停止)'
        msg.loading = false
        msg.endedAt = Date.now()  // 计时：客户端断流也是终态
      })
    } else {
      const errMsg = e instanceof Error ? e.message : String(e)
      agentStore.updateLastAssistant((msg) => {
        msg.content = `❌ 错误: ${errMsg || 'Agent 连接失败'}`
        msg.loading = false
        msg.endedAt = Date.now()  // 计时：连接失败也是终态
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
    // 跑完之后：回读 has_replay 决定是否亮「查看回放」入口（旁路，不阻塞收尾）
    void refreshReplayFlag()
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
    msg.endedAt = Date.now()  // 计时：硬断流也是终态
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
    // 审批续跑收敛后同款回读（续跑是新的 run 收敛点）
    void refreshReplayFlag()
    scrollToBottom()
  }
}

/**
 * A：运行中转向——把一句指导提交给 running / waiting_approval 的任务。
 * 不打断当前步骤；后端在下一个步边界消费并注入后续步骤提示词。
 * 成功：本地乐观落账（"已发送指导"时间线条目），生效留痕由后端
 * steer_applied 事件（当前流或审批恢复流）承担。失败按诚实文案提示：
 * 429=缓冲满 / 409=状态不符 / 503=通道不可用 / 网络失败。
 */
async function handleSteer(text: string) {
  const taskId = agentStore.taskId
  if (!taskId || !text.trim() || steerBusy.value) return
  steerBusy.value = true
  try {
    const res = await agentApi.steerTask(taskId, text)
    const data = apiUnwrap<{ message?: string }>(res)
    agentStore.addSteerSent(text)
    if (data?.message) message.success(data.message)
  } catch (err: unknown) {
    const resp = (err as {
      response?: { status?: number; data?: { message?: unknown; detail?: unknown } }
    })?.response
    const status = resp?.status
    const detail = resp?.data?.message ?? resp?.data?.detail
    if (status === 429) {
      message.warning(t('hitl.steerFull'))
    } else if (status && detail) {
      // 409/503 等：后端 AppError 的诚实文案原样呈现（请求带 X-Silent-Error）
      message.error(String(detail))
    } else {
      message.error(t('hitl.steerFailed'))
    }
  } finally {
    steerBusy.value = false
  }
}

/**
 * B：计划审批应答（approve / edit / reject）→ 消费续跑 SSE（事件同 chat 契约）。
 * - reject#2（重规划机会用尽）：后端回 JSON 终态 rejected（无流可续）——如实收敛现场；
 * - 422（非法编辑）/409（现场已变）：按详情提示 + 刷新权威状态。
 */
async function handlePlanApprovalDecide(payload: {
  decision: 'approve' | 'edit' | 'reject'
  edited_steps?: PlanStepEdit[]
  note?: string
}) {
  const taskId = agentStore.taskId
  if (!taskId || planApprovalBusy.value) return
  planApprovalBusy.value = true
  const controller = new AbortController()
  currentChatController = controller
  agentStore.setTaskState('running')
  agentStore.setLoading(true)
  // 乐观清卡：续跑若再次暂停（reject#1 重规划后 / 下一版计划），
  // plan_approval_required 事件会重建；失败则由刷新恢复。
  agentStore.setPendingPlanApproval(null)
  try {
    const outcome = await agentApi.decidePlanApproval(
      taskId,
      {
        decision: payload.decision,
        edited_steps: payload.edited_steps,
        note: payload.note,
      },
      (event: AgentEvent) => {
        agentStore.processEvent(event)
        scrollToBottom()
      },
      { signal: controller.signal },
    )
    if (!outcome.streamed) {
      // 终态拒绝：决定已被受理并导致终态——如实告知，不用流装完成。
      agentStore.setTaskState('rejected')
      message.info(outcome.message || t('hitl.planRejectedMessage'))
      await refreshTaskState(taskId)
    }
  } catch (err: unknown) {
    const e = err as { status?: number; detail?: string }
    if (e?.status === 422) {
      message.error(e.detail || t('hitl.planEditInvalid'))
    } else {
      message.error(t('hitl.planFailed'))
    }
    await refreshTaskState(taskId)
  } finally {
    planApprovalBusy.value = false
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
    const data = apiUnwrap<AgentTaskView>(res)
    if (!data) return
    agentStore.setTaskState(data.state)
    if (data.state === 'waiting_approval' && data.approval) {
      agentStore.setTaskId(taskId)
      agentStore.setPendingApproval(data.approval)
    } else if (data.state === 'waiting_plan_approval' && data.plan_approval) {
      // B：回到计划审批现场
      agentStore.setTaskId(taskId)
      agentStore.setPendingPlanApproval(data.plan_approval)
      agentStore.setPendingApproval(null)
    } else {
      agentStore.setPendingApproval(null)
      agentStore.setPendingPlanApproval(null)
    }
  } catch {
    // 刷新失败不覆盖已有现场（保持现状，下一次操作再试）
  }
}

/** 刷新/重连后回到审批现场（消费既有 GET task + 持久化的 last task id）。
 *  HITL 工具审批与 B 计划审批两种现场都恢复。 */
async function restorePendingApproval() {
  const last = agentStore.loadPersistedTaskId()
  if (!last) return
  try {
    const res = await agentApi.getTask(last)
    // F2：读信封内层（res.data = {success, data}；此前读 res.data 导致
    // data.state 恒 undefined，刷新后审批卡永不恢复——浏览器验收实锤）
    const data = apiUnwrap<AgentTaskView>(res)
    if (data?.state === 'waiting_approval' && data.approval) {
      agentStore.setTaskId(last)
      agentStore.setTaskState('waiting_approval')
      agentStore.setPendingApproval(data.approval)
    } else if (data?.state === 'waiting_plan_approval' && data.plan_approval) {
      // B：计划审批现场恢复
      agentStore.setTaskId(last)
      agentStore.setTaskState('waiting_plan_approval')
      agentStore.setPendingPlanApproval(data.plan_approval)
    } else if (
      data &&
      ['completed', 'cancelled', 'failed', 'abandoned', 'rejected'].includes(data.state)
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

// ── 回放消费面：入口 / 时间线 / 任务记录 / 深链 ────────────────────────────────

/** 运行收敛后回读权威 has_replay：只有"真有回放"才亮入口（不虚标、不空跳）。 */
async function refreshReplayFlag() {
  const taskId = agentStore.taskId
  if (!taskId) {
    lastRunHasReplay.value = null
    return
  }
  try {
    const res = await agentApi.getTask(taskId)
    // SEAM-3：拦截器返回完整 AxiosResponse，信封在 res.data = {success, data} → apiUnwrap 解一层
    lastRunHasReplay.value = !!apiUnwrap<AgentTaskView>(res)?.has_replay
  } catch {
    lastRunHasReplay.value = null
  }
}

/** 回放入口显示条件：最新一条助手消息 + 非加载中 + 该运行确有回放数据。 */
function showReplayEntry(idx: number, msg: AgentChatMessage): boolean {
  return (
    lastRunHasReplay.value === true &&
    idx === agentStore.messages.length - 1 &&
    msg.role === 'assistant' &&
    !msg.loading &&
    !!agentStore.taskId
  )
}

/** URL 深链同步：?id=<task_id> 可刷新/分享直达；关闭面板时清掉。 */
function syncRouteTaskId(taskId: string | null) {
  if (!router || !route) return
  const q: Record<string, any> = { ...(route.query || {}) }
  if (taskId) q.id = taskId
  else delete q.id
  router.replace({ query: q }).catch(() => {})
}

/** 打开回放时间线（入口按钮 / 任务记录 / 深链三处共用）。 */
async function openReplay(taskId: string) {
  replayOpen.value = true
  replayTaskId.value = taskId
  replayData.value = null
  replayError.value = ''
  replayLoading.value = true
  syncRouteTaskId(taskId)
  try {
    const res = await agentApi.getReplay(taskId)
    replayData.value = apiUnwrap<ReplayTimelineData>(res) || null
    if (!replayData.value) replayError.value = 'not_found'
  } catch (e: any) {
    // 404（REPLAY_NOT_FOUND / TASK_NOT_FOUND）→ 诚实空态；其余 → 加载失败
    replayError.value = e?.response?.status === 404 ? 'not_found' : 'failed'
  } finally {
    replayLoading.value = false
  }
}

function closeReplay() {
  replayOpen.value = false
  replayData.value = null
  replayError.value = ''
  syncRouteTaskId(null)
}

/** 任务记录（列表带 has_replay：完成态标出「有回放可看」）。 */
async function loadRecentTasks() {
  tasksLoading.value = true
  try {
    const res = await agentApi.listTasks({ page: 1, page_size: 10 })
    recentTasks.value = apiUnwrap<{ tasks: AgentTaskListItem[] }>(res)?.tasks || []
  } catch {
    recentTasks.value = []
    message.error(t('agentTasks.loadFailed'))
  } finally {
    tasksLoading.value = false
  }
}

watch(showTasks, (visible) => {
  if (visible) loadRecentTasks()
})

function handleTaskReplayClick(taskId: string) {
  showTasks.value = false
  openReplay(taskId)
}

// 回放深链：URL ?id=<task_id>（通知点击/分享/刷新）→ 打开时间线（诚实空态兜底）。
// F2：watch 而非一次性 onMounted——页面**已挂载**时再点别的通知只变 query
// （同路由、组件不重挂），必须照样切到新任务。
watch(
  () => route?.query?.id,
  (newId) => {
    if (typeof newId === 'string' && newId) {
      // 面板已展示同一任务则不重复请求（openReplay 回写 URL 不算切换）
      if (!(replayOpen.value && replayTaskId.value === newId)) {
        void openReplay(newId)
      }
    } else if (replayOpen.value) {
      // 深链被移除（浏览器后退等）→ 同步收起面板
      closeReplay()
    }
  },
  { immediate: true },
)

async function handleSessionChange(sessionId: string | null) {
  agentStore.setCurrentSession(sessionId)
  if (sessionId) {
    try {
      const res = await agentApi.getSession(sessionId)
      // 真载荷在 apiUnwrap(res).config（agent.py:637-647 的 {success, data:{…,config}}）；
      // 旧读 res.data.config 恒 undefined → 会话配置静默 no-op（B25）
      const detail = apiUnwrap<AgentSessionDetail>(res)
      if (detail?.config) {
        agentStore.updateConfig(detail.config)
      }
    } catch (e) {
      message.error('加载会话失败')
    }
  }
}

/** E2：「清空」仅清空当前会话在界面的消息显示，须二次确认（不删服务端历史） */
async function handleClearMessages() {
  const confirmed = await confirmDangerousAction(
    t('agent.clearConfirmTitle'),
    t('agent.clearConfirm'),
  )
  if (!confirmed) return
  agentStore.clearMessages()
}

async function handleSessionDelete(sessionId: string) {
  const session = agentStore.sessions.find((s) => s.id === sessionId)
  const confirmed = await confirmDangerousAction(
    t('agent.deleteSessionConfirmTitle'),
    t('agent.deleteSessionConfirm', { title: session?.title || sessionId }),
  )
  if (!confirmed) return
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
    // 创建端点只回 {success, data:{id, title}}（agent.py:585-591），真载荷用
    // apiUnwrap 解一层；旧实现把整个信封当会话对象 → 幻影「Untitled ()」项（选中即
    // 404）、currentSessionId 失效（B26）
    const session = apiUnwrap<{ id: string; title: string }>(res)
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
    // 回放深链由 setup 里的 route watch（immediate）统一处理——含已挂载后切换（F2）
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
    // :500-556 会话在 data.sessions、:696-710 配置在 data）——统一经
    // apiUnwrap 解一层。旧实现把信封当载荷 →
    // 工具列表永不下发（「工具: 0/0」）、会话下拉恒空、整信封写入
    // store.config（B22/B23/B24，同族嵌套漂移）
    const toolsData = apiUnwrap<ToolInfo[]>(toolsRes)
    const sessionsData = apiUnwrap<{ sessions: AgentSession[] }>(sessionsRes)
    const configData = apiUnwrap<AgentConfig>(configRes)
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

/* 体验第四波 @引用：用户气泡内的引用 chips（半透明白，衬在蓝底上） */
.user-refs {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 6px;
}

.user-ref-chip {
  font-size: 10px;
  padding: 1px 8px;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.2);
  border: 1px solid rgba(255, 255, 255, 0.35);
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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

.run-cancelled-text {
  word-break: break-word;
  line-height: 18px;
}

/* A：运行中转向（steering）——蓝色系（"方向"的语言）；已发送=浅蓝描边，
   已生效=实底徽标加重（生效是方向盘真的转了）。深色下同为低饱和蓝底。 */
.steer-standalone {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin: 4px 0;
  padding: 8px 12px;
  background: rgba(59, 130, 246, 0.08);
  border: 1px solid rgba(59, 130, 246, 0.3);
  border-left: 3px solid #3b82f6;
  border-radius: 8px;
  font-size: 12px;
  color: #1d4ed8;
}

.dark .steer-standalone {
  background: rgba(59, 130, 246, 0.14);
  color: #93c5fd;
}

.steer-badge {
  flex-shrink: 0;
  padding: 0 6px;
  border-radius: 4px;
  background: #3b82f6;
  color: #fff;
  font-size: 10px;
  font-weight: 600;
  line-height: 18px;
}

.steer-standalone.steer-applied {
  background: rgba(99, 102, 241, 0.12);
  border-color: rgba(99, 102, 241, 0.4);
  border-left-color: #6366f1;
  color: #4338ca;
}

.dark .steer-standalone.steer-applied {
  background: rgba(99, 102, 241, 0.18);
  color: #c7d2fe;
}

.steer-badge-applied { background: #6366f1; }

.steer-text {
  word-break: break-word;
  line-height: 18px;
}

/* B：计划审批时间线条目（青紫=计划域；与转向蓝、取消灰、失败红区分）。 */
.plan-standalone {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin: 4px 0;
  padding: 8px 12px;
  background: rgba(99, 102, 241, 0.08);
  border: 1px solid rgba(99, 102, 241, 0.3);
  border-left: 3px solid #6366f1;
  border-radius: 8px;
  font-size: 12px;
  color: #4338ca;
}

.dark .plan-standalone {
  background: rgba(99, 102, 241, 0.14);
  color: #c7d2fe;
}

.plan-standalone-badge {
  flex-shrink: 0;
  padding: 0 6px;
  border-radius: 4px;
  background: #6366f1;
  color: #fff;
  font-size: 10px;
  font-weight: 600;
  line-height: 18px;
}

.plan-standalone-text {
  word-break: break-word;
  line-height: 18px;
}

/* warning 时间线条目（琥珀=提醒：@引用失效 / 落库失败等；与失败红、取消灰、
   计划紫区分）。双实现合一后取 develop 类名。 */
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
  background: rgba(245, 158, 11, 0.14);
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

/* 回放入口（完成后入口行；时间线 dock 样式随 ReplayTimeline 组件走） */
.replay-entry-line {
  margin-top: 8px;
}

.replay-entry-btn {
  border: 1px solid rgba(37, 99, 235, 0.4);
  color: #2563eb;
  background: rgba(37, 99, 235, 0.06);
  border-radius: 6px;
  font-size: 12px;
  padding: 3px 12px;
  cursor: pointer;
}

.replay-entry-btn:hover {
  background: rgba(37, 99, 235, 0.14);
}

.dark .replay-entry-btn {
  color: #93c5fd;
  border-color: rgba(59, 130, 246, 0.5);
  background: rgba(59, 130, 246, 0.12);
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
