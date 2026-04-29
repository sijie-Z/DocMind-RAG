import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { ChatMessage, Conversation } from '@/types/chat'
import { unbindConversationDocs } from '@/api/chat'

export const useChatStore = defineStore('chat', () => {
  // 状态
  const conversations = ref<Conversation[]>([])
  const currentConversation = ref<Conversation | null>(null)
  const messages = ref<ChatMessage[]>([])
  const isLoading = ref(false)
  
  // 方法
  const setCurrentConversation = (conversation: Conversation | null) => {
    currentConversation.value = conversation
  }
  
  const addMessage = (message: ChatMessage) => {
    messages.value.push(message)
  }
  
  const updateMessage = (index: number, updates: Partial<ChatMessage>) => {
    if (messages.value[index]) {
      messages.value[index] = { ...messages.value[index], ...updates }
    }
  }
  
  const clearMessages = () => {
    messages.value = []
  }
  
  const setLoading = (loading: boolean) => {
    isLoading.value = loading
  }

  const unbindDocuments = async () => {
    if (currentConversation.value?.id) {
      await unbindConversationDocs(currentConversation.value.id.toString())
      if (currentConversation.value.settings) {
        currentConversation.value.settings.bound_document_ids = []
      }
    }
  }
  
  return {
    // 状态
    conversations,
    currentConversation,
    messages,
    isLoading,
    
    // 方法
    setCurrentConversation,
    addMessage,
    updateMessage,
    clearMessages,
    setLoading,
    unbindDocuments
  }
})
