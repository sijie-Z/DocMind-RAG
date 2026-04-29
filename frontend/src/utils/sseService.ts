import { getToken } from '@/utils/auth'

interface RetryOptions {
  maxRetries?: number
  retryDelay?: number
}

interface SSEMessage {
  type: 'chunk' | 'message' | 'error' | 'retry'
  content?: string
  conversationId?: number | string
  messageId?: string
  sources?: any[]
  is_cached?: boolean
  attempt?: number
  maxRetries?: number
  waitTime?: number
  rateLimit?: any
}

type SSEEventHandler = (data: SSEMessage) => void
type SSEStatusHandler = (status: 'connecting' | 'connected' | 'disconnected' | 'error') => void

class SSEService {
  private eventSource: EventSource | null = null
  private messageHandlers: Map<string, SSEEventHandler> = new Map()
  private statusHandlers: SSEStatusHandler[] = []
  private connectionStatus: 'connecting' | 'connected' | 'disconnected' | 'error' = 'disconnected'
  private currentAbortController: AbortController | null = null
  private retryOptions: RetryOptions = { maxRetries: 3, retryDelay: 1000 }
  private currentRetryCount = 0
  private maxRetries = 3
  private retryDelay = 1000

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

  on(event: 'chunk' | 'message' | 'error' | 'connect' | 'disconnect' | 'retry', handler: SSEEventHandler | SSEStatusHandler) {
    if (event === 'connect' || event === 'disconnect') {
      this.statusHandlers.push(handler as SSEStatusHandler)
    } else {
      this.messageHandlers.set(event, handler as SSEEventHandler)
    }
  }

  off(event: 'chunk' | 'message' | 'error' | 'connect' | 'disconnect' | 'retry', handler?: SSEEventHandler | SSEStatusHandler) {
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

  private emit(event: string, data?: any) {
    if (['chunk', 'message', 'error', 'retry'].includes(event)) {
      const handler = this.messageHandlers.get(event)
      if (handler) handler(data)
    } else if (event === 'connect' || event === 'disconnect') {
      this.statusHandlers.forEach(h => h(data))
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

  async post(endpoint: string, data: any): Promise<boolean> {
    const attemptRequest = async (attempt: number): Promise<boolean> => {
      this.setStatus('connecting')
      this.currentRetryCount = attempt

      try {
        const token = (localStorage.getItem('docmind_token') || localStorage.getItem('paicongming_token') || getToken())?.replace(/"/g, '')
        if (!token) {
          console.error('[SSE] No token found')
          this.setStatus('error')
          return false
        }

        const response = await fetch(`/api/v1/chat/${endpoint}`, {
          method: 'POST',
          headers: {
            'Accept': 'text/event-stream',
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`
          },
          body: JSON.stringify(data)
        })

        if (!response.ok) {
          const retryAfter = response.headers.get('Retry-After')
          const shouldRetry = response.status === 429 || response.status === 503

          if (shouldRetry && attempt < this.maxRetries) {
            const waitTime = retryAfter ? parseInt(retryAfter) * 1000 : this.retryDelay * Math.pow(2, attempt - 1)
            console.log(`[SSE] Retry ${attempt}/${this.maxRetries} after ${waitTime}ms`)
            this.emit('retry', { attempt, maxRetries: this.maxRetries, waitTime })
            await new Promise(resolve => setTimeout(resolve, waitTime))
            return attemptRequest(attempt + 1)
          }

          const rateLimitInfo = {
            status: response.status,
            retryAfter: retryAfter ? parseInt(retryAfter) : null,
            message: response.status === 429 ? '请求过于频繁，请稍后再试' : `HTTP错误: ${response.status}`
          }
          console.error('[SSE] HTTP error:', rateLimitInfo)
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
        while (true) {
          const { done, value } = await reader.read()
          if (done) break

          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split('\n')
          buffer = lines.pop() || ''

          for (const line of lines) {
            if (line.startsWith('event: ')) {
              currentEventType = line.slice(7).trim() as SSEMessage['type']
              continue
            }
            if (line.startsWith('data: ')) {
              const jsonStr = line.slice(6).trim()
              if (!jsonStr) continue
              try {
                const parsed = JSON.parse(jsonStr) as SSEMessage
                const data = { ...parsed, type: parsed.type || currentEventType } as SSEMessage
                console.log('[SSE] Message received:', data)
                if (data.type === 'chunk') {
                  this.emit('chunk', data)
                } else if (data.type === 'message') {
                  this.emit('message', data)
                } else if (data.type === 'error') {
                  this.emit('error', data)
                } else if (data.type === 'retry') {
                  this.emit('retry', data)
                }
              } catch (e) {
                console.error('[SSE] Parse error:', e)
              }
            }
          }
        }

        this.setStatus('disconnected')
        return true
      } catch (error) {
        console.error('[SSE] Connection error:', error)
        this.setStatus('error')
        return false
      }
    }

    return attemptRequest(1)
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
