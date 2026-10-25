import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { Conversation } from '@/types/chat'

export const useChatStore = defineStore('chat', () => {
  // 状态
  const conversations = ref<Conversation[]>([])
  const currentConversation = ref<Conversation | null>(null)

  // 方法
  const setCurrentConversation = (conversation: Conversation | null) => {
    currentConversation.value = conversation
  }

  const unbindDocuments = async () => {
    if (currentConversation.value?.id) {
      // 动态导入避免 user → chat → request → user 的模块初始化循环。
      const { unbindConversationDocs } = await import('@/api/chat')
      await unbindConversationDocs(currentConversation.value.id.toString())
      if (currentConversation.value.settings) {
        currentConversation.value.settings.bound_document_ids = []
      }
    }
  }

  const resetForIdentityChange = () => {
    conversations.value = []
    currentConversation.value = null
  }

  return {
    // 状态
    conversations,
    currentConversation,

    // 方法
    setCurrentConversation,
    unbindDocuments,
    resetForIdentityChange,
  }
})
