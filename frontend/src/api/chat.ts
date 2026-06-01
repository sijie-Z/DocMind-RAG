import request from '@/utils/request'
import { useUserStore } from '@/stores/user'
import type { KnowledgeSource } from '@/types/chat'
import type { ApiResponse } from '@/types/common'

// Re-export conversation functions for backward compatibility
export {
  getConversationMessages,
  clearConversationMessages,
  unbindConversationDocs,
  updateMessageFeedback,
} from '@/api/conversation'

// 发送消息
export function sendMessage(data: { message: string; conversationId?: number }) {
  const orgId = useUserStore().currentOrgId
  return request.post<ApiResponse<{
    response: string
    sources: KnowledgeSource[]
  }>>('/chat/completions', {
    messages: [
        { role: 'user', content: data.message }
    ],
    organization_id: orgId,
    stream: true
  })
}

// 即时上传并解析文件（不走完整 RAG 链路，直接返回文本）
export async function parseUpload(file: File): Promise<{
  filename: string
  content: string
  chunk_count: number
  file_size: number
}> {
  const formData = new FormData()
  formData.append('file', file)
  const res = await request.post('/chat/parse-upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return res.data?.data || res.data
}
