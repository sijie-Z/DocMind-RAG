// Agent event types matching the PER architecture
export type AgentEventType =
  | 'thinking'
  | 'plan_start'
  | 'plan_step'
  | 'plan_complete'
  | 'tool_call'
  | 'tool_result'
  | 'tool_error'
  | 'reflection'
  | 'chunk'
  | 'done'
  | 'run_report'
  | 'execution_context'
  | 'retry_hint'
  | 'warning'
  | 'error'
  // HITL（人在环）：执行器停在审批点 / 协作式取消收敛（见后端 approval.py）
  | 'approval_required'
  | 'run_cancelled'

export type ThinkingType = 'reasoning' | 'planning' | 'evaluation' | 'correction'
// 六态真源：backend/app/agent/events.py:39-42（unavailable=评估服务不可用；
// unparsed=模型输出不含可用判定——两者都不是 pass（假成功），也不是 run 失败）
export type ReflectionResult = 'pass' | 'retry' | 'replan' | 'escalate' | 'unavailable' | 'unparsed'
export type PlanStepStatus = 'pending' | 'ready' | 'running' | 'completed' | 'failed' | 'skipped'

export interface AgentEvent {
  type: AgentEventType
  content: string
  // Tool
  tool_name?: string
  tool_args?: Record<string, any>
  tool_call_id?: string
  tool_duration_ms?: number
  // Plan
  plan_id?: string
  plan_step_id?: string
  plan_progress?: number
  plan_step_status?: PlanStepStatus
  dependencies?: string[]
  tool_hint?: string
  // Plan 语义字段（issue #289 G7：后端 to_sse_dict 已发射，前端此前未声明；
  // 默认值分别为 low / auto_retry / None，非默认时才会出现在载荷里）
  parallel_group?: number | null
  risk_level?: string
  retry_strategy?: string
  fallback_tool?: string | null
  // Thinking
  thinking_type?: ThinkingType
  // Reflection
  reflection_result?: ReflectionResult
  // Error
  retry_attempt?: number
  retry_hint?: string
  // Meta
  iteration?: number
  timestamp?: number
  message_id?: string      // from done event for feedback
  saved?: boolean          // done 事件：本轮会话落库是否成功（issue #301 批 D）
  task_id?: string         // 任务级 checkpoint 关联 id（HITL：取消/审批/恢复的钥匙）
  // HITL：done 事件如实盖章任务态（waiting_approval / cancelled 时出现）
  task_state?: AgentTaskState
}

// ── HITL（人在环）─────────────────────────────────────────────────────────

export type AgentTaskState =
  | 'pending'
  | 'running'
  | 'waiting_tool'
  | 'waiting_approval'
  | 'waiting_human'
  | 'failed'
  | 'completed'
  | 'abandoned'
  | 'cancelled'

/** 待审批请求（GET /tasks/{id} 的 data.approval；approval_required 事件同形）。 */
export interface PendingApproval {
  tool_name: string
  tool_args?: Record<string, any>
  tool_call_id?: string
  args_hash?: string
  risk_level?: string
  reason?: string
  step_id?: string
  plan_id?: string
  requested_at?: number
  expires_at?: number | null
  more_pending?: number
}

/** 任务状态视图（GET /agent/tasks/{id}）。 */
export interface AgentTaskView {
  task_id: string
  state: AgentTaskState
  is_recoverable: boolean
  progress: number
  completed_steps: number
  total_steps: number | null
  current_step_id: string | null
  last_error: string | null
  last_error_type: string | null
  query: string
  approval?: PendingApproval
  approval_expired?: boolean
}

/** 审批应答（POST /agent/tasks/{id}/approval）。 */
export interface ApprovalDecisionInput {
  decision: 'approve' | 'deny'
  /** 改指令续跑（仅 approve）：批准时附加的人工指导，注入后续步骤提示词。 */
  modified_instruction?: string
  /** 备注（拒绝理由等）。 */
  note?: string
}

export interface PlanStep {
  id: string
  description: string
  dependencies: string[]
  tool_hint: string | null
  status: PlanStepStatus
  result?: string
  error_context?: string
  retry_count: number
  // 计划语义字段（issue #289 G7：由 plan_step 事件落账，PlanTree 按非默认值出徽标）
  parallel_group?: number | null
  risk_level?: string
  retry_strategy?: string
  fallback_tool?: string | null
}

export interface Plan {
  id: string
  goal: string
  reasoning: string
  steps: PlanStep[]
  completed_steps: number
  failed_steps: number
  total_steps: number
  progress: number
  is_complete: boolean
}

export interface AgentConfig {
  model: string
  temperature: number
  max_tokens: number
  enable_planning: boolean
  enable_reflection: boolean
  enable_tools: boolean
  enable_memory: boolean
  enable_thinking: boolean
  personality: 'precise' | 'creative' | 'balanced'
  disabled_tools: string[]
  system_prompt_override?: string
  max_plan_steps: number
  max_retries_per_step: number
}

export interface ToolInfo {
  name: string
  description: string
  tags: string[]
  parameters: Record<string, any>
  requires_auth: boolean
  disabled_by_default: boolean
}

export interface SkillInfo {
  id: string
  name: string
  description: string
  success_rate: number
  trigger_patterns: string[]
  tool_sequence: Array<{ tool_name: string; description: string }>
}

export interface AgentSession {
  id: string
  title: string
  message_count: number
  created_at: string
  updated_at: string
}

export interface AgentMessage {
  id: string
  content: string
  message_type: string
  created_at: string
  meta?: any
}

export interface AgentSessionDetail extends AgentSession {
  messages: AgentMessage[]
  config: AgentConfig | null
}

// Frontend message model for the chat area
export interface AgentChatMessage {
  role: 'user' | 'assistant'
  content: string
  events: AgentEvent[]
  loading: boolean
  currentTool: string
  thinkingText: string
  planSteps: PlanStep[]
  planId: string
  progress: number
  messageId?: string       // for feedback
  runReport?: string       // 运行报告文本（run_report 事件；issue #242 收尾补接）
  executionContext?: Record<string, any>  // 运行上下文（execution_context 事件，含 total_tokens）
  feedback?: number        // 1=thumbs_up, -1=thumbs_down, 0=none
  feedbackNote?: string
}
