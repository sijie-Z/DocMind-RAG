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
  AgentTaskView,
  ApprovalDecisionInput,
} from '@/types/agent'

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
      }),
      signal: controller.signal,
    })

    if (!response.ok) {
      throw new Error(`Agent request failed: ${response.status}`)
    }

    return consumeEventStream(response, onEvent, controller)
  },

  /** Submit user feedback on an agent response */
  submitFeedback(
    messageId: string,
    feedback: number,
    note?: string,
  ): Promise<ApiResponse<{ message: string }>> {
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
   * （信封在 `res.data = {success, data}`），消费者必须读 `res.data.data`。
   * 类型如实声明 AxiosResponse，杜绝"类型说已解包、运行时没有"的漂移。
   */
  getTask(taskId: string): Promise<AxiosResponse<ApiResponse<AgentTaskView>>> {
    return request.get(`/agent/tasks/${taskId}`)
  },

  /** 协作式取消（服务端停止协议）：202 = 已受理待收敛 / 200 = 已直接终态 */
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

  /** List available tools */
  listTools(): Promise<ApiResponse<ToolInfo[]>> {
    return request.get('/agent/tools')
  },

  /** List learned skills */
  listSkills(): Promise<ApiResponse<SkillInfo[]>> {
    return request.get('/agent/skills')
  },

  // ── Sessions ──

  /** List agent sessions */
  listSessions(params?: {
    page?: number
    page_size?: number
  }): Promise<ApiResponse<{ sessions: AgentSession[]; total: number; page: number; page_size: number }>> {
    return request.get('/agent/sessions', { params })
  },

  /** Create agent session */
  createSession(
    title: string,
  ): Promise<ApiResponse<AgentSession>> {
    return request.post('/agent/sessions', { title })
  },

  /** Get session details */
  getSession(sessionId: string): Promise<ApiResponse<AgentSessionDetail>> {
    return request.get(`/agent/sessions/${sessionId}`)
  },

  /** Delete session */
  deleteSession(sessionId: string): Promise<ApiResponse<{ message: string }>> {
    return request.delete(`/agent/sessions/${sessionId}`)
  },

  // ── Configuration ──

  /** Get user agent config */
  getConfig(): Promise<ApiResponse<AgentConfig>> {
    return request.get('/agent/config')
  },

  /** Update user agent config */
  updateConfig(
    updates: Partial<AgentConfig>,
  ): Promise<ApiResponse<AgentConfig>> {
    return request.put('/agent/config', updates)
  },
}
