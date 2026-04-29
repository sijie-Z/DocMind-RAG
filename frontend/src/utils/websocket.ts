interface WebSocketMessage {
  type: 'message' | 'error' | 'connect' | 'disconnect' | 'notification'
  content?: string
  conversationId?: number | string
  messageId?: string
  sources?: any[]
  title?: string
  fileIds?: string[]
  payload?: Record<string, any>
}

import { getToken } from '@/utils/auth'

class WebSocketService {
  private ws: WebSocket | null = null
  private url: string
  private reconnectInterval = 5000
  private reconnectAttempts = 0
  private maxReconnectAttempts = 5
  private messageHandlers: Map<string, (data: WebSocketMessage) => void> = new Map()
  private connectionStatus: 'connecting' | 'connected' | 'disconnected' | 'error' = 'disconnected'
  private currentUserId: number = 0
  private manualDisconnect = false

  private getWsBaseUrl(): string {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    return `${protocol}//${window.location.host}`
  }

  constructor() {
    this.url = `${this.getWsBaseUrl()}/api/v1/chat/ws`
  }

  connect(userId: number, conversationId?: number | string) {
    console.log(`[WS] Attempting to connect. userId: ${userId}, currentStatus: ${this.connectionStatus}`)
    
    // 0. 安全检查
    if (!userId || userId === 0 || String(userId) === '0') {
      console.warn('[WS] Connect aborted: Invalid userId', userId)
      return
    }

    if (this.ws && (this.connectionStatus === 'connected' || this.connectionStatus === 'connecting')) {
      console.log('[WS] Already connected or connecting, skipping...')
      return
    }

    this.currentUserId = userId
    this.connectionStatus = 'connecting'
    
    // 🛑 核心修复：1. 统一获取 Token 并清洗引号
    const rawToken = localStorage.getItem('docmind_token') || localStorage.getItem('paicongming_token') || getToken()
    
    if (!rawToken) {
        console.error('[WS] Connection failed: No token found in any storage')
        this.connectionStatus = 'error'
        this.emit('error', { type: 'error', content: '未找到登录凭证，请重新登录' })
        return
    }

    const cleanToken = rawToken.replace(/"/g, '')

    const wsUrl = `${this.getWsBaseUrl()}/api/v1/chat/ws?token=${cleanToken}&user_id=${userId}${conversationId ? `&conversation_id=${conversationId}` : ''}`
    
    console.log("[WS] 物理直连地址 (已清洗 Token):", wsUrl.split('?')[0])
    
    try {
      this.manualDisconnect = false
      this.ws = new WebSocket(wsUrl)
      this.setupEventListeners()
    } catch (error) {
      console.error('[WS] Exception during connection setup:', error)
      this.handleConnectionError()
    }
  }

  private setupEventListeners() {
    if (!this.ws) return

    this.ws.onopen = () => {
      console.log('WebSocket connected')
      this.connectionStatus = 'connected'
      this.reconnectAttempts = 0
      this.emit('connect', { type: 'connect' })
    }

    this.ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as WebSocketMessage
        console.log('WebSocket message received:', data)
        this.handleMessage(data)
      } catch (error) {
        console.error('Failed to parse WebSocket message:', error)
      }
    }

    this.ws.onclose = (event) => {
      console.log(`[WS Close] WebSocket disconnected. Code: ${event.code}, Reason: ${event.reason || 'No reason provided'}, WasClean: ${event.wasClean}`)
      this.connectionStatus = 'disconnected'
      this.emit('disconnect', { type: 'disconnect' })
      if (!this.manualDisconnect) {
        this.attemptReconnect()
      }
    }

    this.ws.onerror = (error) => {
      console.error('WebSocket error:', error)
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

  on(event: string, handler: (data: WebSocketMessage) => void) {
    this.messageHandlers.set(event, handler)
  }

  off(event: string) {
    this.messageHandlers.delete(event)
  }

  // 传递 fileIds, strictMode 和 privacyMode 参数给后端
  send(message: string, conversationId?: number | string, fileIds?: string[], strictMode: boolean = false, privacyMode: boolean = true) {
    if (this.connectionStatus !== 'connected' || !this.ws) {
      console.error('WebSocket not connected')
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
    } catch (error) {
      console.error('Failed to send WebSocket message:', error)
      return false
    }
  }

  private attemptReconnect() {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.log('Max reconnection attempts reached')
      return
    }

    this.reconnectAttempts++
    console.log(`Attempting reconnection ${this.reconnectAttempts}/${this.maxReconnectAttempts}`)
    
    setTimeout(() => {
      if (this.currentUserId > 0) {
        this.connect(this.currentUserId) 
      } else {
        console.warn('Cannot reconnect: Invalid currentUserId')
      }
    }, this.reconnectInterval)
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
