import request from '@/utils/request'
import { getToken } from '@/utils/auth'
import type { ApiResponse } from '@/types/common'
import type {
  AgentEvent,
  ToolInfo,
  SkillInfo,
  AgentConfig,
  AgentSession,
  AgentSessionDetail,
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
}

export const agentApi = {
  /** Stream PER agent chat via SSE */
  async chat(
    query: string,
    onEvent: (event: AgentEvent) => void,
    options?: ChatOptions,
  ): Promise<void> {
    const token = getToken()
    const baseUrl = import.meta.env.VITE_API_BASE_URL || '/api/v1'

    // Create a new AbortController for this request
    const controller = new AbortController()
    agentApi.abortController = controller

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

    const reader = response.body?.getReader()
    if (!reader) throw new Error('No response body')

    const decoder = new TextDecoder()
    let buffer = ''

    try {
      while (true) {
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
      if (e instanceof DOMException && e.name === 'AbortError') return
      throw e
    }
  },

  /** Abort active SSE stream */
  abortController: null as AbortController | null,

  createAbortController(): AbortController {
    this.abortController = new AbortController()
    return this.abortController
  },

  abortStream(): void {
    if (this.abortController) {
      this.abortController.abort()
      this.abortController = null
    }
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
