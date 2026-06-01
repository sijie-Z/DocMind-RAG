export interface ChatMessage {
  id?: string | number
  content: string
  messageType: 'user' | 'assistant'
  conversationId: string | number
  createdAt?: string
  sources?: KnowledgeSource[]
  files?: AttachedFile[]
  feedback?: number
  feedbackNote?: string
  isCached?: boolean
  thinking?: string
}

export interface AttachedFile {
  name: string
  status: 'uploading' | 'parsing' | 'indexing' | 'done' | 'error'
  id?: string
  errorMsg?: string
  _originalFile?: File
  _errorExpanded?: boolean
  // 进度信息
  progress?: number // 0-100
  statusDetail?: string // 详细状态文本
  // 即时解析内容（不走完整RAG链路）
  parsedContent?: string // 文件解析后的纯文本，直接作为上下文
}

export interface Conversation {
  id: string | number
  title: string
  userId: number
  createdAt: string
  updatedAt: string
  messageCount?: number
  settings?: {
    bound_document_ids?: string[]
  }
}

export interface KnowledgeSource {
  fileId: number
  filename: string
  relevanceScore: number
  snippet?: string
  content?: string
  chunkIndex: number
  hasKeyword?: boolean
  hasVector?: boolean
  rewriteHits?: number
  freshFactor?: number
}
