import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type {
  AgentConfig,
  AgentChatMessage,
  AgentEvent,
  AgentSession,
  AgentTaskState,
  PendingApproval,
  PendingPlanApproval,
  PlanStep,
  ToolInfo,
} from '@/types/agent'
import { applyPlanStepUpdate, buildPlanStep } from '@/utils/agentPlan'

/** 持久化"最近一次运行的 task_id"：刷新/重连后回到审批现场的钥匙。 */
const LAST_TASK_KEY = 'docmind.agent.lastTaskId'

export const useAgentStore = defineStore('agent', () => {
  // ── Config ──
  const config = ref<AgentConfig>({
    model: 'deepseek-v4-flash',
    temperature: 0.1,
    max_tokens: 4096,
    enable_planning: true,
    enable_reflection: true,
    enable_tools: true,
    enable_memory: true,
    enable_thinking: true,
    personality: 'balanced',
    disabled_tools: ['execute_python', 'execute_sql'],
    max_plan_steps: 10,
    max_retries_per_step: 3,
    // HITL：与后端 AgentConfig 默认一致（backend/app/agent/config.py:204）
    approval_mode: 'risky',
  })

  // ── Sessions ──
  const sessions = ref<AgentSession[]>([])
  const currentSessionId = ref<string | null>(null)

  // ── Runtime state ──
  const messages = ref<AgentChatMessage[]>([])
  const isLoading = ref(false)
  const isThinking = ref(false)
  const currentToolName = ref('')
  const planSteps = ref<PlanStep[]>([])
  const planId = ref('')
  const overallProgress = ref(0)
  const tools = ref<ToolInfo[]>([])
  const expandedEvents = ref<Set<string>>(new Set())

  // ── HITL（人在环）：当前任务 / 任务态 / 待审批请求 ──
  const taskId = ref<string | null>(null)
  // 'idle' 仅是前端占位；其余与后端 TaskStatus 对齐（'failed' 由错误分支落）
  const taskState = ref<'idle' | AgentTaskState>('idle')
  const pendingApproval = ref<PendingApproval | null>(null)
  // B：待审批计划（plan_approval_mode=always 的暂停现场）
  const pendingPlanApproval = ref<PendingPlanApproval | null>(null)

  // ── Computed ──
  const toolCount = computed(() => tools.value.length)
  const enabledToolCount = computed(
    () => tools.value.filter((t) => !config.value.disabled_tools.includes(t.name)).length,
  )
  const hasSessions = computed(() => sessions.value.length > 0)
  const currentSession = computed(() =>
    sessions.value.find((s) => s.id === currentSessionId.value),
  )

  // ── Actions ──

  function addMessage(msg: AgentChatMessage) {
    messages.value.push(msg)
  }

  function updateLastAssistant(updater: (msg: AgentChatMessage) => void) {
    const last = [...messages.value].reverse().find((m) => m.role === 'assistant')
    if (last) updater(last)
  }

  function processEvent(event: AgentEvent) {
    // HITL：任何携带 task_id 的事件都是取消/审批/恢复的钥匙，实时落账
    if (event.task_id) {
      setTaskId(event.task_id)
    }
    switch (event.type) {
      case 'thinking':
        isThinking.value = true
        updateLastAssistant((msg) => {
          if (event.thinking_type === 'correction') {
            msg.thinkingText += '\n' + event.content
          } else {
            msg.thinkingText += event.content
          }
          // issue #289 G1：此前 thinking 从不进 msg.events，视图
          // getLastThinkingType() 过滤恒空 → ThinkingStream 的思考类型标签
          // 恒为 reasoning。落进事件列表后「规划中/评估中/纠正中」标签才可区分；
          // 该事件同时携带 plan_step_status（G6）与 retry_attempt（G3）。
          msg.events.push(event)
        })
        applyPlanStepUpdate(planSteps.value, event)
        break

      case 'plan_start':
        planId.value = event.plan_id || ''
        planSteps.value = []
        updateLastAssistant((msg) => {
          msg.planId = event.plan_id || ''
          msg.planSteps = []
        })
        break

      case 'plan_step': {
        // issue #289 G7：plan_step 事件实际携带 parallel_group/risk_level/
        // retry_strategy/fallback_tool（planner 发射、to_sse_dict 按非默认值带出），
        // 统一经 buildPlanStep 落账，PlanTree 才能展示这些语义徽标。
        const step = buildPlanStep(event)
        if (step) {
          planSteps.value.push(step)
          updateLastAssistant((msg) => {
            msg.planSteps = [...planSteps.value]
          })
        }
        break
      }

      case 'plan_complete':
        isThinking.value = false
        break

      case 'tool_call':
        currentToolName.value = event.tool_name || ''
        updateLastAssistant((msg) => {
          msg.currentTool = event.tool_name || ''
          msg.events.push(event)
        })
        // Update plan step status
        applyPlanStepUpdate(planSteps.value, event)
        break

      case 'tool_result':
        currentToolName.value = ''
        updateLastAssistant((msg) => {
          msg.currentTool = ''
          msg.events.push(event)
        })
        applyPlanStepUpdate(planSteps.value, event)
        if (event.plan_progress !== undefined) {
          overallProgress.value = event.plan_progress
        }
        break

      case 'tool_error':
        currentToolName.value = ''
        updateLastAssistant((msg) => {
          msg.currentTool = ''
          msg.events.push(event)
        })
        applyPlanStepUpdate(planSteps.value, event)
        break

      case 'reflection':
        updateLastAssistant((msg) => {
          msg.events.push(event)
        })
        overallProgress.value = 1.0
        break

      case 'retry_hint':
        // issue #250 第 6 条：后端（executor）在 reset_transport / refresh_auth
        // 重试意图时发射该事件，此前 store 无 case —— "发而无收"，重试原因在
        // UI 里不可见（与 #242 的 run_report 同款处置）。落进事件列表。
        updateLastAssistant((msg) => {
          msg.events.push(event)
        })
        // issue #289 G3：retry_hint 携带 plan_step_id + retry_attempt，
        // 把重试次数归约进对应 PlanStep（PlanTree「重试 N 次」徽标的数据源）。
        applyPlanStepUpdate(planSteps.value, event)
        break

      case 'warning':
        // issue #301 批 D（#22）：后端在「回答已生成、但会话落库失败」时发射该
        // 事件（此前落库失败完全静默，用户刷新后本轮问答凭空消失）。store 若
        // 无 case 会再次把唯一可见的失败信号丢掉 —— 落进事件列表。
        updateLastAssistant((msg) => {
          msg.events.push(event)
        })
        break

      case 'chunk':
        updateLastAssistant((msg) => {
          msg.content += event.content
        })
        break

      case 'error':
        updateLastAssistant((msg) => {
          msg.events.push(event)
          msg.loading = false
          // 体验第四波 计时：整次运行失败也是终态（冻结总用时）
          msg.endedAt = Date.now()
        })
        isLoading.value = false
        break

      case 'run_report':
        // issue #242 收尾：该事件此前在前端白名单被静默丢弃（后端产、前端扔），
        // Run Report / Tokens 在 UI 里永远看不见。现在落账到最后一条回答上。
        updateLastAssistant((m) => { m.runReport = event.content })
        break
      case 'execution_context':
        updateLastAssistant((m) => {
          m.executionContext = event.content as unknown as Record<string, any>
        })
        break

      // ── HITL（人在环）──
      case 'approval_required':
        // 执行器停在审批点（工具未执行）：任务态 waiting_approval + 弹出审批卡。
        taskState.value = 'waiting_approval'
        pendingApproval.value = {
          tool_name: event.tool_name || 'unknown',
          tool_args: event.tool_args || {},
          tool_call_id: event.tool_call_id,
          // F3：缺字段 = low（后端对 low 曾省略该字段；徽标兜底成 'high' 与卡片
          // 理由"风险 low"自相矛盾）。后端现已对审批事件恒显式携带，此兜底
          // 与线上契约一致。
          risk_level: event.risk_level || 'low',
          reason: event.content,
          step_id: event.plan_step_id,
          plan_id: event.plan_id,
        }
        isThinking.value = false
        updateLastAssistant((msg) => {
          msg.events.push(event)
        })
        break

      case 'run_cancelled':
        // 协作式取消收敛：诚实落账（不是完成，也不是错误）。
        taskState.value = 'cancelled'
        pendingApproval.value = null
        updateLastAssistant((msg) => {
          msg.events.push(event)
          msg.loading = false
          // 体验第四波 计时：取消即终态（冻结总用时）
          msg.endedAt = Date.now()
        })
        isLoading.value = false
        isThinking.value = false
        break

      // ── A：运行中转向 ──
      case 'steer_applied':
        // 指导在步边界被消费（注入后续步骤提示词）：时间线留痕，不改任务态。
        updateLastAssistant((msg) => {
          msg.events.push(event)
        })
        break

      case 'steer_sent':
        // 前端本地事件（乐观落账）：发送成功即进时间线，不至于"发出去没影"。
        updateLastAssistant((msg) => {
          msg.events.push(event)
        })
        break

      // ── B：计划审批 ──
      case 'plan_approval_required': {
        // planner 出计划后暂停：任务态 waiting_plan_approval + 弹计划审批卡。
        taskState.value = 'waiting_plan_approval'
        pendingPlanApproval.value = {
          plan_id: event.plan_id || '',
          goal: event.plan_goal || '',
          steps: event.plan_steps || [],
        }
        isThinking.value = false
        // 恢复流不回放 plan_step 事件——用事件自足载荷回填 PlanTree，
        // 让左侧计划树在 resume 场景也能显示待审批计划（无 assistant 消息
        // 在场时同样生效，故不放进 updateLastAssistant 回调）。
        if (event.plan_steps && event.plan_steps.length > 0) {
          const steps: PlanStep[] = event.plan_steps.map((s) => ({
            id: s.id,
            description: s.description,
            dependencies: s.dependencies || [],
            tool_hint: s.tool_hint ?? null,
            status: 'pending',
            retry_count: 0,
          }))
          planSteps.value = steps
          updateLastAssistant((msg) => {
            msg.planSteps = steps
          })
        }
        updateLastAssistant((msg) => {
          msg.events.push(event)
        })
        break
      }

      case 'done':
        updateLastAssistant((msg) => {
          msg.loading = false
          // 体验第四波 计时：done 落终态时间（审批暂停也会写上——恢复续跑时
          // loading 再起、最终 done 覆盖为真实总用时）
          msg.endedAt = Date.now()
          if (event.message_id) {
            msg.messageId = event.message_id
          }
        })
        // HITL：done 如实盖章任务态（waiting_approval / cancelled 时出现）——
        // 暂停不是完成，卡片保持；取消不是完成，状态收敛到 cancelled。
        // B：waiting_plan_approval 同款——计划卡保持。
        if (event.task_state === 'waiting_approval') {
          taskState.value = 'waiting_approval'
        } else if (event.task_state === 'waiting_plan_approval') {
          taskState.value = 'waiting_plan_approval'
        } else if (event.task_state === 'cancelled') {
          taskState.value = 'cancelled'
          pendingApproval.value = null
          pendingPlanApproval.value = null
        } else {
          taskState.value = 'completed'
          pendingApproval.value = null
          pendingPlanApproval.value = null
        }
        isLoading.value = false
        isThinking.value = false
        overallProgress.value = 1.0
        break
    }
  }

  function clearMessages() {
    messages.value = []
    planSteps.value = []
    planId.value = ''
    overallProgress.value = 0
    isThinking.value = false
    currentToolName.value = ''
    expandedEvents.value = new Set()
  }

  function setLoading(val: boolean) {
    isLoading.value = val
  }

  function setSessions(list: AgentSession[]) {
    sessions.value = list
  }

  function setCurrentSession(id: string | null) {
    currentSessionId.value = id
  }

  function updateConfig(partial: Partial<AgentConfig>) {
    Object.assign(config.value, partial)
  }

  function toggleEventExpanded(eventKey: string) {
    const next = new Set(expandedEvents.value)
    if (next.has(eventKey)) {
      next.delete(eventKey)
    } else {
      next.add(eventKey)
    }
    expandedEvents.value = next
  }

  // ── HITL actions ──

  function persistTaskId(id: string | null) {
    try {
      if (id) localStorage.setItem(LAST_TASK_KEY, id)
      else localStorage.removeItem(LAST_TASK_KEY)
    } catch {
      // localStorage 不可用（隐私模式等）：持久化是增强不是前提
    }
  }

  function setTaskId(id: string | null) {
    taskId.value = id
    persistTaskId(id)
  }

  function setTaskState(state: 'idle' | AgentTaskState) {
    taskState.value = state
  }

  function setPendingApproval(p: PendingApproval | null) {
    pendingApproval.value = p
  }

  /** B：计划审批现场（刷新/重连恢复 + 事件驱动的权威重建）。 */
  function setPendingPlanApproval(p: PendingPlanApproval | null) {
    pendingPlanApproval.value = p
  }

  /** A：转向发送成功后的本地乐观落账（时间线"已发送指导"条目；后端无此事件）。 */
  function addSteerSent(text: string) {
    updateLastAssistant((msg) => {
      msg.events.push({ type: 'steer_sent', content: text } as AgentEvent)
    })
  }

  /** 新一轮发送前复位任务现场（保留 pendingApproval 由 approval_required 重建）。 */
  function resetTask() {
    setTaskId(null)
    taskState.value = 'running'
    pendingApproval.value = null
    pendingPlanApproval.value = null
  }

  function loadPersistedTaskId(): string | null {
    try {
      return localStorage.getItem(LAST_TASK_KEY)
    } catch {
      return null
    }
  }

  return {
    // State
    config,
    sessions,
    currentSessionId,
    messages,
    isLoading,
    isThinking,
    currentToolName,
    planSteps,
    planId,
    overallProgress,
    tools,
    expandedEvents,
    taskId,
    taskState,
    pendingApproval,
    pendingPlanApproval,
    // Computed
    toolCount,
    enabledToolCount,
    hasSessions,
    currentSession,
    // Actions
    addMessage,
    updateLastAssistant,
    processEvent,
    clearMessages,
    setLoading,
    setSessions,
    setCurrentSession,
    updateConfig,
    toggleEventExpanded,
    setTaskId,
    setTaskState,
    setPendingApproval,
    setPendingPlanApproval,
    addSteerSent,
    resetTask,
    loadPersistedTaskId,
  }
})
