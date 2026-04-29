import { getToken } from '@/utils/auth'

export interface RealtimeNotificationPayload {
  id: number
  title: string
  content: string
  type: string
  is_read: boolean
  created_at: string
  target_route?: string
  target_id?: string
}

type NotificationHandler = (payload: RealtimeNotificationPayload) => void

class NotificationSocketService {
  private ws: WebSocket | null = null
  private handlers: Set<NotificationHandler> = new Set()
  private reconnectTimer: number | null = null
  private manualDisconnect = false
  private reconnectAttempts = 0
  private maxReconnectAttempts = 10
  private baseReconnectDelay = 2000

  // 心跳相关
  private pingInterval = 30000 // 每30秒发一次心跳
  private pingTimer: number | null = null
  private pongTimeoutTimer: number | null = null
  private pongTimeout = 10000 // 10秒收不到 pong 则认为断开

  private emitWsMetricEvent(payload: Record<string, any>) {
    try {
      window.dispatchEvent(new CustomEvent('app:ws-metric', { detail: payload }))
    } catch {
      // ignore non-browser environments
    }
  }

  private clearHeartbeat() {
    if (this.pingTimer) {
      window.clearInterval(this.pingTimer)
      this.pingTimer = null
    }
    if (this.pongTimeoutTimer) {
      window.clearTimeout(this.pongTimeoutTimer)
      this.pongTimeoutTimer = null
    }
  }

  private startHeartbeat() {
    this.clearHeartbeat()
    this.pingTimer = window.setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ type: 'ping' }))
        
        // 等待 pong 响应
        this.pongTimeoutTimer = window.setTimeout(() => {
          console.warn('[NotificationSocket] Heartbeat timeout, reconnecting...')
          this.emitWsMetricEvent({ kind: 'heartbeat-timeout' })
          this.ws?.close() // 主动断开，触发 onclose 重连
        }, this.pongTimeout)
      }
    }, this.pingInterval)
  }

  connect() {
    const token = getToken()
    if (!token) return

    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return
    }

    const cleanToken = token.replace(/"/g, '')
    
    // 动态获取 WS URL
    const baseUrl = (import.meta as any).env.VITE_API_BASE_URL || '/api/v1'
    let wsBaseUrl = ''
    if (baseUrl.startsWith('http')) {
      wsBaseUrl = baseUrl.replace(/^http/, 'ws')
    } else {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const host = window.location.host
      wsBaseUrl = `${protocol}//${host}${baseUrl.startsWith('/') ? '' : '/'}${baseUrl}`
    }
    
    const url = `${wsBaseUrl}/notifications/ws?token=${cleanToken}`
    this.manualDisconnect = false
    this.ws = new WebSocket(url)

    this.ws.onmessage = (event) => {
      try {
        const parsed = JSON.parse(event.data)
        
        // 处理心跳响应
        if (parsed?.type === 'pong') {
          if (this.pongTimeoutTimer) {
            window.clearTimeout(this.pongTimeoutTimer)
            this.pongTimeoutTimer = null
          }
          return
        }

        if (parsed?.type === 'notification' && parsed?.data) {
          const payload = parsed.data as RealtimeNotificationPayload
          this.handlers.forEach(handler => handler(payload))
        }
      } catch (err) {
        console.warn('[NotificationSocket] Parse error:', err)
      }
    }

    this.ws.onopen = () => {
      this.reconnectAttempts = 0 // 连接成功后重置重连次数
      this.emitWsMetricEvent({ kind: 'connected' })
      this.startHeartbeat()
    }

    this.ws.onclose = () => {
      this.clearHeartbeat()
      this.emitWsMetricEvent({ kind: 'closed', manualDisconnect: this.manualDisconnect })
      this.ws = null
      if (!this.manualDisconnect) {
        this.scheduleReconnect()
      }
    }

    this.ws.onerror = () => {
      this.emitWsMetricEvent({ kind: 'error' })
      this.ws?.close()
    }
  }

  onNotification(handler: NotificationHandler) {
    this.handlers.add(handler)
  }

  offNotification(handler?: NotificationHandler) {
    if (handler) {
      this.handlers.delete(handler)
    } else {
      this.handlers.clear()
    }
  }

  disconnect() {
    this.clearHeartbeat()
    if (this.reconnectTimer) {
      window.clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    this.manualDisconnect = true
    this.reconnectAttempts = 0
    this.ws?.close()
    this.ws = null
  }

  private scheduleReconnect() {
    if (this.reconnectTimer) return
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.error('[NotificationSocket] Max reconnect attempts reached')
      this.emitWsMetricEvent({ kind: 'max-reconnect-reached' })
      return
    }
    
    // 指数退避重连策略
    const delay = Math.min(this.baseReconnectDelay * Math.pow(1.5, this.reconnectAttempts), 30000)
    this.reconnectAttempts++
    this.emitWsMetricEvent({ kind: 'reconnect-scheduled', delayMs: delay, attempt: this.reconnectAttempts })
    
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null
      this.connect()
    }, delay)
  }
}

export const notificationSocket = new NotificationSocketService()
