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
  | 'error'

export type ThinkingType = 'reasoning' | 'planning' | 'evaluation' | 'correction'
export type ReflectionResult = 'pass' | 'retry' | 'replan' | 'escalate'
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
  feedback?: number        // 1=thumbs_up, -1=thumbs_down, 0=none
  feedbackNote?: string
}
