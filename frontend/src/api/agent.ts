import request from '@/utils/request'
import { getToken } from '@/utils/auth'
import type { AxiosResponse } from 'axios'
import type { ApiResponse } from '@/types/common'
import type {
  AgentEvent,
  ToolInfo,
  SkillInfo,
  AgentConfig,
  AgentSession,
  AgentSessionDetail,
  AgentTaskListItem,
  AgentTaskView,
  ApprovalDecisionInput,
  ApprovalMode,
  PlanApprovalDecisionInput,
  ReplayTimeline,
} from '@/types/agent'

/** B：计划审批应答的结果形态（SSE 续跑流 / 终态 JSON 二选一）。 */
export type PlanApprovalOutcome =
  | { streamed: true; abort: () => void }
  | { streamed: false; state: string; rejections?: number; message?: string }

interface ChatOptions {
  sessionId?: string
  history?: Array<{ role: string; content: string }>
  enableTools?: boolean
  enablePlanning?: boolean
  enableReflection?: boolean
  enableMemory?: boolean
  enableThinking?: boolean
  model?: string
  temperature?: number
  disabledTools?: string[]
  systemPromptOverride?: string
  /** HITL：本次运行的审批模式（覆盖已保存配置；不传则沿用服务端已保存值） */
  approvalMode?: ApprovalMode
  /** 体验第四波 @引用：显式引用的知识库文档 id（后端注入 planner 上下文） */
  referencedDocumentIds?: string[]
  /** External abort signal (e.g. from the caller's AbortController) */
  signal?: AbortSignal
}

/** 消费一个 SSE 事件流（chat / approval 续跑共用）。 */
async function consumeEventStream(
  response: Response,
  onEvent: (event: AgentEvent) => void,
  controller: AbortController,
): Promise<{ abort: () => void }> {
  const reader = response.body?.getReader()
  if (!reader) throw new Error('No response body')

  const decoder = new TextDecoder()
  let buffer = ''

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        const trimmed = line.trim()
        if (!trimmed || !trimmed.startsWith('data: ')) continue
        const data = trimmed.slice(6)
        if (!data || data === '[DONE]') continue

        try {
          const event: AgentEvent = JSON.parse(data)
          onEvent(event)
        } catch {
          // Skip malformed events
        }
      }
    }
  } catch (e: unknown) {
    // Suppress AbortError from user clicking stop
    if (e instanceof DOMException && e.name === 'AbortError') {
      return { abort: () => controller.abort() }
    }
    throw e
  }
  return { abort: () => controller.abort() }
}

export const agentApi = {
  /** Stream PER agent chat via SSE */
  async chat(
    query: string,
    onEvent: (event: AgentEvent) => void,
    options?: ChatOptions,
  ): Promise<{ abort: () => void }> {
    const token = getToken()
    const baseUrl = import.meta.env.VITE_API_BASE_URL || '/api/v1'

    // Each chat() call gets its own AbortController — no shared module state
    const controller = new AbortController()
    const externalSignal = options?.signal
    if (externalSignal) {
      if (externalSignal.aborted) {
        controller.abort()
      } else {
        externalSignal.addEventListener('abort', () => controller.abort(), { once: true })
      }
    }

    const response = await fetch(`${baseUrl}/agent/chat`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({
        query,
        session_id: options?.sessionId,
        history: options?.history,
        enable_tools: options?.enableTools ?? true,
        enable_planning: options?.enablePlanning ?? true,
        enable_reflection: options?.enableReflection ?? true,
        enable_memory: options?.enableMemory ?? true,
        enable_thinking: options?.enableThinking ?? true,
        model: options?.model ?? 'deepseek-v4-flash',
        temperature: options?.temperature ?? 0.1,
        disabled_tools: options?.disabledTools ?? [],
        system_prompt_override: options?.systemPromptOverride,
        // HITL：仅在显式传值上送（undefined 被 JSON.stringify 省略 = 沿用保存配置）
        approval_mode: options?.approvalMode,
        // @引用（体验第四波）：仅在携带时上送（空数组 = 不引用，等价省略）
        referenced_document_ids: options?.referencedDocumentIds?.length
          ? options.referencedDocumentIds
          : undefined,
      }),
      signal: controller.signal,
    })

    if (!response.ok) {
      throw new Error(`Agent request failed: ${response.status}`)
    }

    return consumeEventStream(response, onEvent, controller)
  },

  /** Submit user feedback on an agent response（信封契约同 getTask；结果不读） */
  submitFeedback(
    messageId: string,
    feedback: number,
    note?: string,
  ): Promise<AxiosResponse<ApiResponse<{ message: string }>>> {
    return request.post('/agent/feedback', {
      message_id: messageId,
      feedback,
      note: note || undefined,
    })
  },

  // ── HITL：任务状态 / 取消 / 审批（人在环）──────────────────────────────

  /** 任务状态视图（审批现场 / 取消后的权威状态；HITL 恢复入口）。
   *
   * 注意返回形状（F2 教训）：request 的响应拦截器返回**完整 AxiosResponse**
   * （信封在 `res.data = {success, data}`），消费者统一用 `@/utils/apiUnwrap`
   * 解一层（apiUnwrap(res) → 业务体）。
   * 类型如实声明 AxiosResponse，杜绝"类型说已解包、运行时没有"的漂移。
   */
  getTask(taskId: string): Promise<AxiosResponse<ApiResponse<AgentTaskView>>> {
    return request.get(`/agent/tasks/${taskId}`)
  },

  // ── 回放消费面（跑完之后的"可带走"体验；信封契约同 getTask：apiUnwrap(res)）──

  /** 最近任务列表：每条带 has_replay（「有回放可看」标记）。 */
  listTasks(params?: {
    page?: number
    page_size?: number
  }): Promise<AxiosResponse<ApiResponse<{ tasks: AgentTaskListItem[]; total: number; page: number; page_size: number }>>> {
    return request.get('/agent/tasks', { params })
  },

  /**
   * 执行回放（结构化 timeline，字段与执行写侧逐字对齐）。
   * 404 REPLAY_NOT_FOUND = 该任务没有回放数据（诚实空态，绝不编造轨迹）；
   * 404 TASK_NOT_FOUND = 任务不存在/非本人/组织不一致。
   */
  getReplay(taskId: string): Promise<AxiosResponse<ApiResponse<ReplayTimeline>>> {
    return request.get(`/agent/tasks/${taskId}/replay`)
  },

  /** 协作式取消（服务端停止协议）：202 = 已受理待收敛 / 200 = 已直接终态。
   * 信封契约同 getTask（拦截器返回完整 AxiosResponse）。 */
  cancelTask(
    taskId: string,
  ): Promise<AxiosResponse<ApiResponse<{ task_id: string; state: string; message?: string; already?: boolean }>>> {
    return request.post(`/agent/tasks/${taskId}/cancel`)
  },

  /**
   * A：运行中转向 —— 向 running / waiting_approval 的任务发送一句指导。
   * 普通 JSON 响应（非 SSE）：不打断当前步骤，在下一个步边界注入。
   * X-Silent-Error：429（缓冲满）/409（状态不符）的诚实文案由调用方原样呈现
   * ——拦截器的通用 429 文案（"请求过于频繁"）会盖掉"缓冲已满"的准确语义。
   */
  steerTask(
    taskId: string,
    messageText: string,
  ): Promise<AxiosResponse<ApiResponse<{ task_id: string; state: string; pending: number; message: string }>>> {
    return request.post(
      `/agent/tasks/${taskId}/steer`,
      { message: messageText },
      { headers: { 'X-Silent-Error': '1' } },
    )
  },

  /**
   * 审批应答（approve / deny / 改指令）→ SSE 续跑流。
   * 事件契约与 chat 一致；续跑可能再次停在下一个审批点（流内再发 approval_required）。
   */
  async decideApproval(
    taskId: string,
    input: ApprovalDecisionInput,
    onEvent: (event: AgentEvent) => void,
    options?: { signal?: AbortSignal },
  ): Promise<{ abort: () => void }> {
    const token = getToken()
    const baseUrl = import.meta.env.VITE_API_BASE_URL || '/api/v1'
    const controller = new AbortController()
    const externalSignal = options?.signal
    if (externalSignal) {
      if (externalSignal.aborted) {
        controller.abort()
      } else {
        externalSignal.addEventListener('abort', () => controller.abort(), { once: true })
      }
    }

    const response = await fetch(`${baseUrl}/agent/tasks/${taskId}/approval`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({
        decision: input.decision,
        modified_instruction: input.modified_instruction || undefined,
        note: input.note || undefined,
      }),
      signal: controller.signal,
    })

    if (!response.ok) {
      // 409/404：任务不再处于待审批（已被应答/取消/终态）——由调用方刷新现场
      throw new Error(`Approval request failed: ${response.status}`)
    }

    return consumeEventStream(response, onEvent, controller)
  },

  /**
   * B：计划审批应答（approve / edit / reject）→ 两种结果形态：
   * - approve / edit / reject#1：SSE 续跑流（事件契约与 /chat 一致；可能再次停在
   *   计划审批点：流内再发 plan_approval_required）；
   * - reject#2（重规划机会用尽）：200 JSON 终态 rejected——无流可续（streamed=false）。
   * 422（非法编辑）由调用方按详情提示（请求带 X-Silent-Error 防通用文案覆盖）。
   */
  async decidePlanApproval(
    taskId: string,
    input: PlanApprovalDecisionInput,
    onEvent: (event: AgentEvent) => void,
    options?: { signal?: AbortSignal },
  ): Promise<PlanApprovalOutcome> {
    const token = getToken()
    const baseUrl = import.meta.env.VITE_API_BASE_URL || '/api/v1'
    const controller = new AbortController()
    const externalSignal = options?.signal
    if (externalSignal) {
      if (externalSignal.aborted) {
        controller.abort()
      } else {
        externalSignal.addEventListener('abort', () => controller.abort(), { once: true })
      }
    }

    const response = await fetch(`${baseUrl}/agent/tasks/${taskId}/plan-approval`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Silent-Error': '1',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({
        decision: input.decision,
        edited_steps: input.edited_steps,
        note: input.note || undefined,
      }),
      signal: controller.signal,
    })

    if (!response.ok) {
      const failure = new Error(`Plan approval request failed: ${response.status}`) as Error & {
        status?: number
        detail?: string
      }
      failure.status = response.status
      try {
        const body = await response.json()
        const raw = body?.detail ?? body?.message
        if (raw !== undefined) {
          failure.detail = Array.isArray(raw)
            ? raw.map((e: any) => (e && typeof e === 'object' && 'msg' in e ? String(e.msg) : String(e))).join('; ')
            : String(raw)
        }
      } catch {
        // 非 JSON 错误体：保留状态码
      }
      throw failure
    }

    const contentType = response.headers.get('content-type') || ''
    if (contentType.includes('text/event-stream')) {
      const stream = await consumeEventStream(response, onEvent, controller)
      return { streamed: true, abort: stream.abort }
    }
    // 终态拒绝：JSON 回执（data.state = "rejected"）
    const body = await response.json()
    const data = body?.data || {}
    return {
      streamed: false,
      state: String(data.state || ''),
      rejections: data.rejections,
      message: data.message ? String(data.message) : undefined,
    }
  },

  /** List available tools（真载荷 apiUnwrap(res)；agent.py:450-472） */
  listTools(): Promise<AxiosResponse<ApiResponse<ToolInfo[]>>> {
    return request.get('/agent/tools')
  },

  /** List learned skills（信封契约同 getTask） */
  listSkills(): Promise<AxiosResponse<ApiResponse<SkillInfo[]>>> {
    return request.get('/agent/skills')
  },

  // ── Sessions ──

  /** List agent sessions（真载荷 apiUnwrap(res).sessions；agent.py:500-556） */
  listSessions(params?: {
    page?: number
    page_size?: number
  }): Promise<AxiosResponse<ApiResponse<{ sessions: AgentSession[]; total: number; page: number; page_size: number }>>> {
    return request.get('/agent/sessions', { params })
  },

  /** Create agent session（后端只回 {id, title}，真载荷 apiUnwrap(res)；agent.py:585-591） */
  createSession(
    title: string,
  ): Promise<AxiosResponse<ApiResponse<{ id: string; title: string }>>> {
    return request.post('/agent/sessions', { title })
  },

  /** Get session details（真载荷 apiUnwrap(res).config；agent.py:637-647） */
  getSession(sessionId: string): Promise<AxiosResponse<ApiResponse<AgentSessionDetail>>> {
    return request.get(`/agent/sessions/${sessionId}`)
  },

  /** Delete session（信封契约同 getTask；结果不读） */
  deleteSession(sessionId: string): Promise<AxiosResponse<ApiResponse<{ message: string }>>> {
    return request.delete(`/agent/sessions/${sessionId}`)
  },

  // ── Configuration ──

  /** Get user agent config（真载荷 apiUnwrap(res)；agent.py:696-710） */
  getConfig(): Promise<AxiosResponse<ApiResponse<AgentConfig>>> {
    return request.get('/agent/config')
  },

  /** Update user agent config（信封契约同 getTask；结果不读） */
  updateConfig(
    updates: Partial<AgentConfig>,
  ): Promise<AxiosResponse<ApiResponse<AgentConfig>>> {
    return request.put('/agent/config', updates)
  },
}
