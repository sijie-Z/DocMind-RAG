import { ref, nextTick } from 'vue'
import { useDedupedMessage } from '@/utils/message'
import { useI18n } from 'vue-i18n'
import type { ChatMessage } from '@/types/chat'
import { updateMessageFeedback } from '@/api/chat'

export function useChatMessages() {
  const message = useDedupedMessage()
  const { t } = useI18n()
  const messages = ref<ChatMessage[]>([])
  const chatContainer = ref<HTMLElement>()
  const isUserScrolling = ref(false)
  const showBackToBottom = ref(false)
  const autoFollowResponse = ref(true)

  const handleScroll = () => {
    if (!chatContainer.value) return
    const { scrollTop, scrollHeight, clientHeight } = chatContainer.value
    const distanceToBottom = scrollHeight - scrollTop - clientHeight
    
    isUserScrolling.value = distanceToBottom > 50
    showBackToBottom.value = distanceToBottom > 300
  }

  const scrollToBottom = (behavior: ScrollBehavior = 'smooth', force = false) => {
    if (!force) {
      if (!autoFollowResponse.value) return
      if (isUserScrolling.value) return
    }

    nextTick(() => {
      if (chatContainer.value) {
        chatContainer.value.scrollTo({ top: chatContainer.value.scrollHeight, behavior })
      }
    })
  }

  const handleFeedback = async (msg: ChatMessage, feedback: number) => {
    if (!msg.id) return
    const newFeedback = msg.feedback === feedback ? 0 : feedback
    
    try {
      const res = await updateMessageFeedback(String(msg.id), newFeedback)
      if (res.data?.success) {
        msg.feedback = newFeedback
        if (newFeedback !== 0) {
          message.success(newFeedback === 1 ? t('chat.feedback.likeSuccess') || t('chat.liked') : t('chat.feedback.dislikeSuccess') || t('chat.disliked'))
        }
      }
    } catch (err) {
      message.error(t('chat.feedback.failed') || t('chat.feedbackFailed'))
    }
  }

  const copyText = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text)
      message.success(t('common.copySuccess'))
    } catch (err) {
      message.error(t('common.copyFailed'))
    }
  }

  return {
    messages,
    chatContainer,
    isUserScrolling,
    showBackToBottom,
    autoFollowResponse,
    handleScroll,
    scrollToBottom,
    handleFeedback,
    copyText
  }
}
