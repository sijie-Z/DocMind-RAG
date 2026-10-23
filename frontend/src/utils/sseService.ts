import { getToken } from '@/utils/auth'
import type { SSEMessage } from '@/types/api'

interface RetryOptions {
  maxRetries?: number
  retryDelay?: number
}

// issue #289 G8：SSEEventType 类型别名此前漏了 run_report / execution_context /
// retry_hint（运行时白名单有、TS 类型没有），且 emit()/post() 两处运行时白名单
// 与类型别名三处各自维护、口径漂移。收敛为单一 const 数组 + 派生类型：
// 类型层与两处运行时校验同源，新增事件只改一处。
const SSE_EVENT_TYPES = [
  'chunk',
  'message',
  'error',
  // ISS-3：后端 warning 事件（#301 批 D 落库失败可见 / Agent 侧降级提示，载荷
  // type+content，agent.py 出口经 chat.py 转发）此前不在白名单被静默丢弃 →
  // 聊天页无任何提示。此处放行后 emit()/post() 两处同源校验随之生效。
  'warning',
  'retry',
  'thinking',
  'tool_call',
  'tool_result',
  'tool_error',
  'plan_start',
  'plan_step',
  'plan_complete',
  'reflection',
  'done',
  'run_report',
  'execution_context',
  'retry_hint',
  'debug',
] as const

type SSEEventType = (typeof SSE_EVENT_TYPES)[number]

type SSEEventHandler = (_data: SSEMessage) => void
type SSEStatusHandler = (_status: 'connecting' | 'connected' | 'disconnected' | 'error') => void

class SSEService {
  private eventSource: EventSource | null = null
  private messageHandlers: Map<string, SSEEventHandler> = new Map()
  // issue #480：未知事件透传回调（default 分支），不依赖白名单。
  private eventHandlers: SSEEventHandler[] = []
  private statusHandlers: SSEStatusHandler[] = []
  private connectionStatus: 'connecting' | 'connected' | 'disconnected' | 'error' = 'disconnected'
  private currentAbortController: AbortController | null = null
  private retryOptions: RetryOptions = { maxRetries: 3, retryDelay: 1000 }
  private currentRetryCount = 0
  private maxRetries = 3
  private retryDelay = 1000
  // issue #480 §5.2：最近收到的 SSE id（`{stream_id}:{seq}`），供断线续传。
  private lastEventId: string | null = null

  setRetryOptions(options: RetryOptions) {
    this.retryOptions = { ...this.retryOptions, ...options }
    this.maxRetries = this.retryOptions.maxRetries ?? 3
    this.retryDelay = this.retryOptions.retryDelay ?? 1000
  }

  isConnected(): boolean {
    return this.connectionStatus === 'connected'
  }

  getConnectionStatus(): string {
    return this.connectionStatus
  }

  on(event: SSEEventType | 'connect' | 'disconnect', handler: SSEEventHandler | SSEStatusHandler) {
    if (event === 'connect' || event === 'disconnect') {
      this.statusHandlers.push(handler as SSEStatusHandler)
    } else {
      this.messageHandlers.set(event, handler as SSEEventHandler)
    }
  }

  /**
   * issue #480：订阅**所有**事件（包括不在白名单里的未知类型）。
   * 返回取消订阅函数。已知类型仍按类型分发，未知类型只走这里。
   */
  onEvent(handler: SSEEventHandler): () => void {
    this.eventHandlers.push(handler)
    return () => {
      const idx = this.eventHandlers.indexOf(handler)
      if (idx > -1) this.eventHandlers.splice(idx, 1)
    }
  }

  /** 最近收到的 SSE id（可用于下次 `post` 的 `lastEventId` 续传）。 */
  getLastEventId(): string | null {
    return this.lastEventId
  }

  off(event: SSEEventType | 'connect' | 'disconnect', handler?: SSEEventHandler | SSEStatusHandler) {
    if (event === 'connect' || event === 'disconnect') {
      if (handler) {
        const idx = this.statusHandlers.indexOf(handler as SSEStatusHandler)
        if (idx > -1) this.statusHandlers.splice(idx, 1)
      } else {
        this.statusHandlers = []
      }
    } else {
      if (handler) {
        this.messageHandlers.delete(event)
      } else {
        this.messageHandlers.delete(event)
      }
    }
  }

  private emit(event: string, data?: unknown) {
    if ((SSE_EVENT_TYPES as readonly string[]).includes(event)) {
      const handler = this.messageHandlers.get(event)
      if (handler) handler(data as SSEMessage)
    } else if (event === 'connect' || event === 'disconnect') {
      this.statusHandlers.forEach(h => h(data as 'connecting' | 'connected' | 'disconnected' | 'error'))
    }
  }

  private setStatus(status: 'connecting' | 'connected' | 'disconnected' | 'error') {
    this.connectionStatus = status
    if (status === 'connected') {
      this.emit('connect')
    } else if (status === 'disconnected' || status === 'error') {
      this.emit('disconnect')
    }
  }

  async post(
    endpoint: string,
    data: Record<string, unknown>,
    options: { lastEventId?: string | null } = {},
  ): Promise<boolean> {
    this.currentAbortController?.abort()
    this.currentAbortController = new AbortController()
    const signal = this.currentAbortController.signal
    // issue #480：显式续传点（断线重连）才带 Last-Event-ID；普通新消息重置。
    const resumeFrom = options.lastEventId ?? null
    this.lastEventId = resumeFrom

    const attemptRequest = async (attempt: number): Promise<boolean> => {
      this.setStatus('connecting')
      this.currentRetryCount = attempt

      try {
        const token = (localStorage.getItem('docmind_token') || localStorage.getItem('paicongming_token') || getToken())?.replace(/"/g, '')
        if (!token) {
          // No token found
          this.setStatus('error')
          return false
        }

        const response = await fetch(`/api/v1/chat/${endpoint}`, {
          method: 'POST',
          headers: {
            'Accept': 'text/event-stream',
            'Content-Type': 'application/json',
            ...(resumeFrom ? { 'Last-Event-ID': resumeFrom } : {}),
            'Authorization': `Bearer ${token}`
          },
          body: JSON.stringify(data),
          signal,
        })

        if (!response.ok) {
          const retryAfter = response.headers.get('Retry-After')
          const shouldRetry = response.status === 429 || response.status === 503

          if (shouldRetry && attempt < this.maxRetries) {
            const waitTime = retryAfter ? parseInt(retryAfter) * 1000 : this.retryDelay * Math.pow(2, attempt - 1)
            this.emit('retry', { attempt, maxRetries: this.maxRetries, waitTime })
            await new Promise(resolve => setTimeout(resolve, waitTime))
            return attemptRequest(attempt + 1)
          }

          const rateLimitInfo = {
            status: response.status,
            retryAfter: retryAfter ? parseInt(retryAfter) : null,
            message: response.status === 429 ? '请求过于频繁，请稍后再试' : `HTTP错误: ${response.status}`
          }
          // SSE HTTP error
          this.setStatus('error')
          this.emit('error', {
            type: 'error',
            content: rateLimitInfo.message,
            rateLimit: rateLimitInfo
          } as SSEMessage)
          return false
        }

        const reader = response.body?.getReader()
        if (!reader) {
          this.setStatus('error')
          return false
        }

        const decoder = new TextDecoder()
        let buffer = ''

        this.setStatus('connected')

        let currentEventType: SSEMessage['type'] = 'chunk'
        let eventTypeSet = false
        for (;;) {
          if (signal.aborted) { reader.cancel(); break }
          const { done, value } = await reader.read()
          if (done) break

          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split('\n')
          buffer = lines.pop() || ''

          for (const line of lines) {
            if (line.startsWith('id: ')) {
              // issue #480：记录事件 id（`{stream_id}:{seq}`），供断线续传。
              this.lastEventId = line.slice(4).trim()
              continue
            }
            if (line.startsWith('event: ')) {
              currentEventType = line.slice(7).trim() as SSEMessage['type']
              eventTypeSet = true
              continue
            }
            if (line.startsWith('data: ')) {
              const jsonStr = line.slice(6).trim()
              if (!jsonStr) continue
              try {
                const parsed = JSON.parse(jsonStr) as SSEMessage
                // event: line (SSE protocol) is authoritative over data.type
                const resolvedType = (eventTypeSet ? currentEventType : parsed.type || currentEventType) as SSEMessage['type']
                eventTypeSet = false
                const data = { ...parsed, type: resolvedType } as SSEMessage
                // issue #289 G8：与 emit()/类型别名同源（SSE_EVENT_TYPES），
                // message/error 与其余类型统一按类型查 handler（emit 的
                // 白名单分支此前对这两型做等价转发，收敛后行为一致）。
                if ((SSE_EVENT_TYPES as readonly string[]).includes(data.type)) {
                  const handler = this.messageHandlers.get(data.type)
                  if (handler) handler(data as SSEMessage)
                } else {
                  // issue #480：未知事件不再静默丢弃——透传给 onEvent 订阅者。
                  for (const handler of this.eventHandlers) handler(data as SSEMessage)
                }
              } catch {
                // SSE Parse error
              }
            }
          }
        }

        this.setStatus('disconnected')
        return true
      } catch {
        // SSE Connection error
        this.setStatus('error')
        return false
      }
    }

    return attemptRequest(1)
  }

  abort() {
    this.currentAbortController?.abort()
    this.currentAbortController = null
  }

  disconnect() {
    if (this.eventSource) {
      this.eventSource.close()
      this.eventSource = null
    }
    if (this.currentAbortController) {
      this.currentAbortController.abort()
      this.currentAbortController = null
    }
    this.setStatus('disconnected')
  }
}

export const sseService = new SSEService()
