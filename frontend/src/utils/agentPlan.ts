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
import type { PlanStep, PlanStepStatus } from '@/types/agent'

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
