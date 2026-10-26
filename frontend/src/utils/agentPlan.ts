/**
 * 计划步骤的共享归约逻辑（issue #289）：
 * Agent 页的 store（stores/agent.ts）与聊天页 Agent 模式（useChatSend）此前
 * 各自（后者是本次新增）处理 plan_* / tool_* 事件对 PlanStep 的影响。抽到
 * 此处保证两个入口的状态推导完全同构。
 *
 * 兼容两处事件口径：Agent 页事件来自 api/agent.ts 透传（snake_case 原样），
 * 聊天页事件来自 chat.py 的投影（camelCase），故入参用最小结构约定
 * （PlanStepSource / PlanStepUpdate），由调用方做字段映射。
 */
import type {
  AgentEvent,
  ExecutionStepResultPayload,
  PlanStep,
  PlanStepStatus,
} from '@/types/agent'

const PLAN_STEP_STATUSES: PlanStepStatus[] = [
  'pending',
  'ready',
  'running',
  'completed',
  'failed',
  'skipped',
]

export interface PlanStepSource {
  plan_step_id?: string
  content?: string
  dependencies?: string[]
  tool_hint?: string | null
  // 语义字段（G7）：to_sse_dict 按非默认值裁剪，缺省即是默认
  parallel_group?: number | null
  risk_level?: string
  retry_strategy?: string
  fallback_tool?: string | null
}

/** 由 plan_step 事件构建一个 pending 状态的 PlanStep；无 plan_step_id 返回 null */
export function buildPlanStep(src: PlanStepSource): PlanStep | null {
  if (!src.plan_step_id) return null
  return {
    id: src.plan_step_id,
    description: src.content || '',
    dependencies: src.dependencies || [],
    tool_hint: src.tool_hint || null,
    status: 'pending',
    retry_count: 0,
    parallel_group: src.parallel_group ?? null,
    risk_level: src.risk_level,
    retry_strategy: src.retry_strategy,
    fallback_tool: src.fallback_tool ?? null,
  }
}

export interface PlanStepUpdate {
  type: string
  plan_step_id?: string
  plan_step_status?: string
  retry_attempt?: number
}

/**
 * 把一条事件归约到对应步骤上：
 * - tool_call → running；tool_result → completed（failed 终态不回转）；
 *   tool_error → failed（与 store 原逐 case 逻辑一致）；
 * - plan_step_status（G6，thinking/tool_error 携带）为后端终态权威值；
 * - retry_attempt（G3，thinking/tool_error/retry_hint 携带）单调抬升
 *   retry_count，让 PlanTree 的「重试 N 次」徽标吃上真实数据。
 */
export function applyPlanStepUpdate(steps: PlanStep[], event: PlanStepUpdate): void {
  if (!event.plan_step_id) return
  const step = steps.find((s) => s.id === event.plan_step_id)
  if (!step) return

  if (event.type === 'tool_call') {
    step.status = 'running'
  } else if (event.type === 'tool_result') {
    if (step.status !== 'failed') step.status = 'completed'
  } else if (event.type === 'tool_error') {
    step.status = 'failed'
  }

  if (event.plan_step_status && PLAN_STEP_STATUSES.includes(event.plan_step_status as PlanStepStatus)) {
    step.status = event.plan_step_status as PlanStepStatus
  }
  if (event.retry_attempt && event.retry_attempt > step.retry_count) {
    step.retry_count = event.retry_attempt
  }
}

/** 兼容字符串 JSON、对象 content 与旧 ExecutorAgent 字段；未知字段直接忽略。 */
export function parseExecutionStepResult(content: unknown): ExecutionStepResultPayload | null {
  let value = content
  if (typeof value === 'string') {
    if (!value) return null
    try {
      value = JSON.parse(value)
    } catch {
      return null
    }
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const payload = value as Record<string, unknown>
  if (typeof payload.step_id !== 'string' || !payload.step_id) return null
  return payload as unknown as ExecutionStepResultPayload
}

function errorText(error: unknown): string | undefined {
  if (error === null || error === undefined || error === '') return undefined
  if (typeof error === 'string') return error
  if (typeof error === 'object' && !Array.isArray(error)) {
    const value = error as Record<string, unknown>
    if (typeof value.message === 'string') return value.message
    if (typeof value.code === 'string') return value.code
  }
  return String(error)
}

export function applyExecutionStepResult(
  steps: PlanStep[],
  event: Pick<AgentEvent, 'content'> & { plan_step_id?: string },
): void {
  const payload = parseExecutionStepResult(event.content)
  if (!payload) return
  const step = steps.find((s) => s.id === (payload.step_id || event.plan_step_id))
  if (!step) return

  if (payload.status && PLAN_STEP_STATUSES.includes(payload.status)) {
    step.status = payload.status
  }
  if (typeof payload.result === 'string') {
    step.result = payload.result
  } else if (payload.result === null) {
    step.result = undefined
  } else if (typeof payload.local_synthesis?.content === 'string') {
    step.result = payload.local_synthesis.content
  }
  const error = payload.error !== undefined ? payload.error : payload.error_context
  if (error !== undefined) step.error_context = errorText(error)
  if (payload.evidence) step.evidence = payload.evidence
  if (payload.local_synthesis) step.local_synthesis = payload.local_synthesis
  if (payload.evidence_schema) step.evidence_schema = payload.evidence_schema
  const retryCount = payload.retry_count
  if (typeof retryCount === 'number' && retryCount > step.retry_count) {
    step.retry_count = retryCount
  }
}

/** 时间线只展示局部步骤结果；该文本不会写入 msg.content/thinkingText。 */
export function executionStepResultText(event: Pick<AgentEvent, 'content'>): string {
  const payload = parseExecutionStepResult(event.content)
  if (!payload) return ''
  if (typeof payload.result === 'string') return payload.result
  const error = errorText(payload.error !== undefined ? payload.error : payload.error_context)
  if (error) return error
  return payload.local_synthesis?.content || ''
}
