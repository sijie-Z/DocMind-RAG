interface WebSocketSource {
  fileId?: number
  filename?: string
  relevanceScore?: number
  snippet?: string
}

interface WebSocketMessage {
  type: 'message' | 'error' | 'connect' | 'disconnect' | 'notification'
  content?: string
  conversationId?: number | string
  messageId?: string
  sources?: WebSocketSource[]
  title?: string
  fileIds?: string[]
  payload?: Record<string, unknown>
  is_cached?: boolean
}

import { getToken } from '@/utils/auth'

class WebSocketService {
  private ws: WebSocket | null = null
  private url: string
  private reconnectAttempts = 0
  private maxReconnectAttempts = 5
  private messageHandlers: Map<string, (_data: WebSocketMessage) => void> = new Map()
  private connectionStatus: 'connecting' | 'connected' | 'disconnected' | 'error' = 'disconnected'
  private currentUserId: number = 0
  private manualDisconnect = false

  private getWsBaseUrl(): string {
    if (import.meta.env.DEV) {
      // Route through Vite proxy (port 5173) so the proxy forwards to backend
      return `ws://${window.location.hostname}:5173`
    }
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    return `${protocol}//${window.location.host}`
  }

  private getWsPath(): string {
    // In dev, use /ws which Vite rewrites to /api/v1/chat/ws
    // In prod, use the full path
    return import.meta.env.DEV ? '/ws' : '/api/v1/chat/ws'
  }

  constructor() {
    this.url = `${this.getWsBaseUrl()}${this.getWsPath()}`
  }

  connect(userId: number, conversationId?: number | string) {
    // 0. 安全检查
    if (!userId || userId === 0 || String(userId) === '0') {
      // Connect aborted: Invalid userId
      return
    }

    if (this.ws && (this.connectionStatus === 'connected' || this.connectionStatus === 'connecting')) {
      return
    }

    this.currentUserId = userId
    this.connectionStatus = 'connecting'
    
    // 🛑 核心修复：1. 统一获取 Token 并清洗引号
    const rawToken = localStorage.getItem('docmind_token') || localStorage.getItem('paicongming_token') || getToken()
    
    if (!rawToken) {
        // Connection failed: No token found in any storage
        this.connectionStatus = 'error'
        this.emit('error', { type: 'error', content: '未找到登录凭证，请重新登录' })
        return
    }

    const cleanToken = rawToken.replace(/"/g, '')

    // token 同时走 subprotocol（安全）和 query param（兼容 Vite 代理转发）
    const wsUrl = `${this.getWsBaseUrl()}${this.getWsPath()}?user_id=${userId}&token=${encodeURIComponent(cleanToken)}${conversationId ? `&conversation_id=${conversationId}` : ''}`

    try {
      this.manualDisconnect = false
      this.ws = new WebSocket(wsUrl, [`auth.${cleanToken}`])
      this.setupEventListeners()
    } catch {
      // Exception during connection setup
      this.handleConnectionError()
    }
  }

  private setupEventListeners() {
    if (!this.ws) return

    this.ws.onopen = () => {
      this.connectionStatus = 'connected'
      this.reconnectAttempts = 0
      this.emit('connect', { type: 'connect' })
    }

    this.ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as WebSocketMessage
        this.handleMessage(data)
      } catch {
        // Failed to parse message
      }
    }

    this.ws.onclose = (_event) => {
      this.connectionStatus = 'disconnected'
      this.emit('disconnect', { type: 'disconnect' })
      if (!this.manualDisconnect) {
        this.attemptReconnect()
      }
    }

    this.ws.onerror = () => {
      this.connectionStatus = 'error'
      this.emit('error', { type: 'error', content: 'WebSocket连接错误' })
    }
  }

  private handleMessage(data: WebSocketMessage) {
    this.emit(data.type, data)
  }

  private handleConnectionError() {
    // 连接失败回调，先空着
  }

  private emit(event: string, data: WebSocketMessage) {
    const handler = this.messageHandlers.get(event)
    if (handler) {
      handler(data)
    }
  }

  on(event: string, handler: (_data: WebSocketMessage) => void) {
    this.messageHandlers.set(event, handler)
  }

  off(event: string) {
    this.messageHandlers.delete(event)
  }

  // 传递 fileIds, strictMode 和 privacyMode 参数给后端
  send(message: string, conversationId?: number | string, fileIds?: string[], strictMode: boolean = false, privacyMode: boolean = true) {
    if (this.connectionStatus !== 'connected' || !this.ws) {
      // WebSocket not connected
      return false
    }

    try {
      const messageData: WebSocketMessage = {
        type: 'message',
        content: message,
        conversationId,
        fileIds: fileIds || [],
        payload: { strictMode, privacyMode }
      }
      this.ws.send(JSON.stringify(messageData))
      return true
    } catch {
      // Failed to send WebSocket message
      return false
    }
  }

  sendStop() {
    if (this.connectionStatus !== 'connected' || !this.ws) {
      // WebSocket not connected
      return false
    }
    this.ws.send(JSON.stringify({ type: 'stop' }))
    return true
  }

  private attemptReconnect() {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      return
    }

    // Exponential backoff: 1s, 2s, 4s, 8s, 16s + jitter
    const baseDelay = Math.min(1000 * Math.pow(2, this.reconnectAttempts), 16000)
    const jitter = Math.random() * 1000
    const delay = baseDelay + jitter

    this.reconnectAttempts++

    setTimeout(() => {
      if (this.currentUserId > 0) {
        this.connect(this.currentUserId)
      }
    }, delay)
  }

  disconnect() {
    if (this.ws) {
      this.manualDisconnect = true
      this.ws.close()
      this.ws = null
    }
    this.connectionStatus = 'disconnected'
    this.reconnectAttempts = 0
  }

  getConnectionStatus() {
    return this.connectionStatus
  }

  isConnected() {
    return this.connectionStatus === 'connected'
  }
}

// 创建单例实例
export const wsService = new WebSocketService()

// 导出类型
export type { WebSocketMessage }
