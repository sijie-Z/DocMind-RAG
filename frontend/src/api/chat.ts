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
